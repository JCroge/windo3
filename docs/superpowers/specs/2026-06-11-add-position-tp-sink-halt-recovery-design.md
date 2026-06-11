---
comet_change: add-position-tp-sink-halt-recovery
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-11-add-position-tp-sink-halt-recovery
status: final
---

# 技术设计：加仓 TP 收口 + halt 恢复语义诚实

> 上游事实源是 OpenSpec 产物（proposal / delta specs）。本文是技术 RFC，描述 HOW。范围 = B（用户已确认）。需求口径以 `openspec/changes/add-position-tp-sink-halt-recovery/specs/` 为准。

## 1. 背景与根因（一手代码核对）

第五次审计 P1-01（对抗复核 CONFIRMED 0.9）：`ContractExecutor.add_to_position`（`executor.py:3046-3213`）加仓后基于新均价重算 TP，但只写 scalar `position['take_profit']`（3178-3183），不碰 `take_profit_levels`、不经 `_set_position_tp`。

生命周期事实：
- `_set_position_tp(position, tp_first, tp_levels)`（2087-2096）是 TP 唯一收口，断言 `tp_first == tp_levels[0]`。
- `take_profit_levels` 整个生命周期**不收缩**（始终完整 `[L0, L1, ...]`）；`tp_filled`（0/1/2，在 `reduce_position(tp_advance)` 真实成交后于 2991 推进）是独立计数，驱动 `_update_trailing`（1961/1968）检查哪一级。
- `take_profit` scalar 在有 levels 时恒 == `levels[0]`，仅为满足不变量（实际 TP 逻辑走 levels + tp_filled；scalar 路径 1908-1912 仅 legacy 无 levels 时生效）。
- 不变量守卫（`_update_trailing` 顶部 1923-1938）：`levels` 非空 且 `take_profit != levels[0]` → `_halt_symbol('tp_invariant_breach')`（严格 `!=`，无容差）。
- `_halt_symbol`（925-937）**无条件** `get_halt_state().halt()` 跳全局；全局 halt 持久化、`can_open_new()=False` 拦全系统开仓（agent 层 157）。

故障链：加仓写 scalar ≠ levels[0] → 下一轮守卫 halt → 全局熔断 → 全系统冻结开仓、不自愈。加仓门槛低（PA conviction≥70），1066 测试无覆盖。

附带 P2-02：`clear_symbol_halt`（942-973）只清内存 `_halted_symbols`，不清持久化全局 halt → `/resume_symbol` 恢复语义陷阱。

## 2. P1-01 实现：加仓 TP 按 level 平移 + 经 sink 收口

替换 `executor.py:3178-3183` 的 TP 重算段：

```python
if old_tp and old_entry > 0:
    old_levels = position.get('take_profit_levels') or [old_tp]
    new_levels = []
    for lvl in old_levels:
        dist = abs(lvl - old_entry) / old_entry
        new_levels.append(new_entry * (1 + dist) if side == 'long'
                          else new_entry * (1 - dist))
    self._set_position_tp(position, new_levels[0], new_levels)
```

**设计要点**：
- **按每个 level 各自距 old_entry 比例**平移（非单一 tp_dist），保多级结构（L0@2% / L1@4% 不被压平）。与 SL 重算（3170-3177）同 distance-ratio philosophy。
- **经 `_set_position_tp` 收口**：满足 CLAUDE.md "TP 写入必须经单一收口"红线，加仓后 `take_profit == take_profit_levels[0]` 不变量必保。
- **tp_filled-safe**：`take_profit_levels` 不收缩、`tp_filled` 不动。已填级（tp_filled==1 时的 L0）平移无害（`_update_trailing` 按 tp_filled 跳过它）；未填级（L1）随新均价平移是正确的（剩余 TP 目标应跟新均价走）。
- **边界**：保留 `if old_tp and old_entry > 0` 守卫；无 levels 时回退 `[old_tp]` 单级仍经 sink。FR-05（3070-3074）已保证加仓只在 `protection_state=='protected'` 发生，levels 此时形态良好（`take_profit==levels[0]` 成立），平移输入可信。

**不变性**：scalar=levels[0] 的关系在 sink 内由断言强制；加仓不引入第二真相源。

## 3. P2-02 实现：恢复语义诚实（纯 TG/agent 层，零控制流改动）

- `clear_symbol_halt` **签名与返回类型不变**（int 项数）——其有 ~15 处测试断言与既有 `tg-symbol-halt-control` spec 依赖，且 agent 层包装 `_safe_clear_symbol_halt`（`agents/trading/executor.py:484`）声明 `-> int`。
- 诚实回显放在**已持有 `self._halt_state` 的层**：MultiExecutor 的 resume_symbol 处理（`agents/trading/executor.py:119` 调用点附近）/ `telegram_notifier.py` 的 `/resume_symbol` 回显。清完 per-symbol 后查 `self._halt_state`（全局 halted?），若仍 halt，回显文案追加"per-symbol 已清，但全局仍 halt，请用 /resume（带对账）解除"。
- **明确不动**（保安全姿态，out-of-scope）：`_halt_symbol` 仍无条件跳全局 fail-closed；per-symbol halt 不加持久化；`/resume_symbol` 不绕过 reconciliation 清全局。这些深度 halt 重构拆为独立后续 change。

## 4. 测试策略

`test_partial_tp_lifecycle.py`（TP 不变量的既有 home）扩：
- 加仓后 `take_profit == take_profit_levels[0]` 不变量保持
- 加仓后再跑 `_update_trailing` MUST NOT 触发 `tp_invariant_breach` halt
- **tp_filled==1（partial TP 已部分成交）加仓**：levels 平移 + tp_filled 不变 + 不 halt
- 多级 levels 加仓后各级距离比例保持

`test_tg_symbol_halt_control.py`（halt 控制的既有 home）扩：
- 全局 halt 仍在时 `/resume_symbol` 回显含全局提示
- 全局未 halt 时 `/resume_symbol` 回显不附加提示
- `clear_symbol_halt` 返回类型仍 int（既有断言不破）

## 5. 同构核对（CLAUDE.md 红线）

`event_backtest.py` 经核对**无加仓路径**（只有 open + `_maybe_partial_tp` + trailing，grep `add_to_position`/`加仓` 为空）。结论：**加仓 TP 重算是 live-only，不进回测决策路径，无同构对象需同步**。在 tasks/验收记录此理由，红线满足。

## 6. 风险与回归

- blast radius 小：集中在 `add_to_position` 单段 TP + TG 回显。
- 主要风险点是 tp_filled>0 平移语义——已论证 safe，必须由 tp_filled==1 加仓测试坐实。
- 回归：全量 `python3 -m pytest -q` 须全绿（基线 1066 + 新增用例后上调）；`compileall` 通过。
- 不回归红线：`_set_position_tp` 单点收口、保护单 fail-closed、`/resume_symbol` 不动全局 halt。

## 7. Spec Patch（已回写 delta spec）

- `entry-drift-policy`：MODIFIED "TP Field Single Source of Truth"，显式覆盖 `add_to_position` + 加仓平移 + tp_filled-safe + 3 个 Scenario。
- `tg-symbol-halt-control`：ADDED "`/resume_symbol` 必须在全局 halt 仍在时诚实回显" + 3 个 Scenario（含 clear_symbol_halt 返回类型不变）。
