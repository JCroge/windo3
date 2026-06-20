---
comet_change: fix-phantom-position-resync
role: technical-design
canonical_spec: openspec
---

# 幽灵持仓补录双确认 + 症状硬化（技术设计）

> 需求事实源为 OpenSpec delta spec `openspec/changes/fix-phantom-position-resync/specs/position-sync-resilience/spec.md`。本文档只描述 HOW。

## 1. 根因（已实测）

```
close(02:16:33) → _close_cooldown[XRP]=now+60s (executor.py:928/1828)
sync(02:17:49, +76s) → 冷却已过(60<76) → 交易所滞后仍上报 XRP → 补录幽灵(executor.py:2747, 无 SL)
  → [Migrate] reconcile protection_state=unknown + _halt_symbol(reason='migrate_missing_sl') (executor.py:667/669)
  → ERROR ×131 / ~69min + per-symbol halt → 人工 /resume(03:27)
```

`_close_cooldown` 60s 固定窗被 OKX >76s 上报延迟击穿。**系统性复发**：近 3 天 3 次（06-18 UNI / 06-19 XLM / 06-20 XRP），每次都在平仓后。本次零真实风险（幽灵、仓位真平），但签名区分不了幽灵 vs 真·丢 SL 实仓。

## 2. 方案：双确认（persist-2-ticks）+ 症状硬化

```
现状: 交易所新见持仓 → 立即补录
改后: 交易所新见持仓 → _pending_resync 计 tick → 连续 N=2 tick 都见 → 才补录
      幽灵下个 tick 即消失 → 计数归零 → 永不补录
```

不赌固定冷却窗（鲁棒于任意 OKX 滞后），不加网络调用（轻）。`_close_cooldown` 作第一道防线保留——多数幽灵被 60s 冷却挡住，双确认兜住超窗者。

## 3. 详细设计（全部在 `executor.py::sync_positions`）

### 3.1 双确认状态机（补录 else 分支前置，~line 2729）

新增实例状态 `self._pending_resync: dict[str, int]`（构造期 `{}`，`getattr` 防御）。补录分支改为：

```python
for sym, ex_pos in active.items():
    if sym in cooldown and now < cooldown[sym]:
        continue                                  # 第一道防线: 冷却期不补录
    if sym in self.positions:
        ... 既有数量校正/unrealized 更新 ...        # 不变
    else:
        cnt = self._pending_resync.get(sym, 0) + 1
        if cnt < confirm_ticks:                    # 默认 2, config position_resync_confirm_ticks
            self._pending_resync[sym] = cnt
            continue                               # 等下个 tick 确认
        self._pending_resync.pop(sym, None)
        ... 既有补录逻辑(SL/TP 兜底 + setdefault + self.positions[sym]=ex_pos) ...

# 扫尾: 本 tick 未在 active 出现的 pending sym → 幽灵消失, 清计数
for sym in list(self._pending_resync):
    if sym not in active:
        self._pending_resync.pop(sym, None)
```

计 **tick 数**而非时间戳：与 sync 节奏天然对齐，幽灵下个 tick 不出现即归零，无需时钟运算。`confirm_ticks` 经 config（默认 2）。

### 3.2 protection-unknown 告警去重退避（~line 667/669）

新增 `self._last_protection_alert: dict[str, str]`（sym→上次告警的 protection_state/reason）。`migrate_missing_sl` 分支：

```python
prev = self._last_protection_alert.get(symbol)
if prev != 'migrate_missing_sl':                   # 仅状态变化时记 ERROR
    self.logger.error(f"[Migrate] {symbol} 本地有仓位但交易所无 SL algo,protection_state→unknown")
    self._last_protection_alert[symbol] = 'migrate_missing_sl'
position['protection_state'] = 'unknown'
if not self.is_symbol_halted(symbol):              # 已 halt 同因则跳过(幂等)
    self._halt_symbol(symbol, reason='migrate_missing_sl')
```

恢复保护或移除持仓时清 `_last_protection_alert[symbol]`，使下次再失能重新告警。

### 3.3 halt 自愈（移除分支，~line 2707）

移除某 sym（已不在交易所）时：

```python
halt_info = getattr(self, '_halted_symbols', {}).get(sym)
if halt_info and halt_info.get('reason') == 'migrate_missing_sl':
    self.clear_symbol_halt(sym)                    # 连带清 halt_state 全局项
    self.logger.info(f"[SelfHeal] {sym} 幽灵移除, 自动清 migrate_missing_sl halt")
self._last_protection_alert.pop(sym, None)
```

`_halted_symbols[sym]` 已存 `{'reason','halted_at'}`，可精确判因。**只清 `migrate_missing_sl`**；其它 reason（真实对账冲突、保护单失败）不在自愈范围，维持 fail-closed。

## 4. 边界条件

| 情形 | 处理 |
|---|---|
| 幽灵 tick1 见 / tick2 消失 | tick1 计 1 不补录, tick2 扫尾清计数 → 永不补录、不 halt |
| 真实残留仓连续 2 tick | tick2 补录(延迟 ~32s), 后续 reconcile 正常归属 SL |
| 冷却期内交易所仍上报 | 跳过、不计入双确认 tick |
| 真·补录后 reconcile 仍无 SL | 照旧 halt(migrate_missing_sl)——安全不放松 |
| 真仓 halt 后又恢复 SL | 既有 reconcile 路径解除(不在本 change) |
| 非 migrate_missing_sl 的 halt | 自愈不碰, 维持既有恢复路径 |

## 5. 安全不放松论证

- 双确认只延迟"补录"，不延迟"对已补录的真实无保护仓位 halt"。
- halt 自愈仅在 `sym 被 sync 移除`（= 交易所确认已无此仓 = 确认 flat）这一明确安全态触发，且限 `migrate_missing_sl` 单一 reason。
- 真·丢 SL 的实仓：连续 2 tick 仍在交易所 → 补录 → reconcile 无 SL → halt，与现状一致。

## 6. 测试策略

`tests/`（新增，真同构驱动 `sync_positions` 非纯 mock）：

1. 幽灵：mock `fetch_positions` 序列 [有 X, 无 X] → `sync_positions` 跑 2 次 → X 从不进 `self.positions`、无 halt。
2. 真仓：序列 [有 X, 有 X] → 第 2 次 X 补录进 `self.positions`。
3. 冷却期：X 在 `_close_cooldown` 内 + 交易所有 X → 跳过、`_pending_resync` 不计。
4. ERROR 去重：同 X 连续 N tick protection-unknown → `logger.error` 仅首次；`_halt_symbol` 不重复。
5. halt 自愈：X 因 migrate_missing_sl halt → sync 移除 X → halt 清除；另造一个 reason='reconcile_conflict' 的 halt → 移除时不被清。
6. 不回归 `position-sync-resilience` transient-error 重试 + 既有 halt/reconcile 测试。

全量回归零退化。

## 7. 红线 / 非目标

- 不改 `_calc_risk_budget`（20x 按设计、max_loss bounded 5%，非 bug）。
- 不改保护单 owner-tag / SL 挂单 / close path / reconciler / realized_pnl_resolver。
- 不引入 fills 网络核查（双确认已足够）。
- 不重构 sync_positions 整体结构，只前置状态机 + 两处症状硬化。
