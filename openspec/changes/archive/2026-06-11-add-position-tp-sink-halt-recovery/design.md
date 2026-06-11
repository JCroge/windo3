# 高层设计：加仓 TP 收口 + halt 恢复语义诚实

> 本文件是 open 阶段的高层架构决策。深度技术 RFC（含逐步实现）由 comet-design 阶段产出至 `docs/superpowers/specs/`。范围 = B（已与用户确认）。

## 关键事实（一手代码核对，executor.py）

- `_set_position_tp(position, tp_first, tp_levels)`（2087-2096）：TP 字段唯一收口，断言 `tp_levels` 非空且 `tp_first == tp_levels[0]`，写 `take_profit` 与 `take_profit_levels`。
- TP 不变量守卫（`_update_trailing` 顶部，1923-1938）：`take_profit_levels` 非空 且 `take_profit != take_profit_levels[0]` → `_halt_symbol('tp_invariant_breach')`（严格 `!=`，无容差）。
- **生命周期关键**：`take_profit_levels` 在整个生命周期**不收缩**，始终是完整 `[tp1, tp2, ...]`；`tp_filled`（0/1/2，2991 在 reduce 真实成交后推进）是独立计数，驱动 `_update_trailing` 检查哪一级（1961/1968）。`take_profit` scalar 恒 == `levels[0]`，有 levels 时仅为满足不变量（实际 TP 逻辑走 levels + tp_filled，1908-1912 的 scalar 路径仅 legacy 无 levels 时生效）。
- bug 点 `add_to_position`（3166-3188）：重算只写 `position['take_profit'] = new_entry*(1±tp_dist)`，**不碰 levels** → `take_profit != levels[0]` → 下一轮守卫 halt。
- per-symbol halt 已有牙齿：`is_symbol_halted` gate 开仓(2113)/加仓(3061)/setTP；`_halted_symbols` 纯内存（无持久化）。全局 `halt_state` 持久化。`clear_symbol_halt`(942-973) 只清内存。

## 决策 1（P1-01）：加仓 TP 按 level 平移 + 经 `_set_position_tp` 收口

把 `add_to_position` 的 TP 重算段（3178-3183）替换为：

```
old_levels = position.get('take_profit_levels') or [old_tp]   # old_tp == old_levels[0]，由不变量保证
new_levels = []
for lvl in old_levels:
    dist = abs(lvl - old_entry) / old_entry      # 每个 level 各自距 OLD 均价的比例
    new_levels.append(new_entry * (1 + dist) if side == 'long'
                      else new_entry * (1 - dist))
self._set_position_tp(position, new_levels[0], new_levels)     # 单点收口，不变量保住
```

**为何按"每个 level 各自距离"而非单一 tp_dist**：保留多级 TP 结构（tp1@2% / tp2@4% 不被压平）。沿用 SL 重算（3170-3177）的"保持距离比例"同philosophy。

**tp_filled-safe**：`take_profit_levels` 不收缩、`tp_filled` 不动；已填级（如 tp_filled==1 时的 level 0）平移无害（`_update_trailing` 按 tp_filled 跳过它），未填级（level 1）平移到反映新均价是**正确**的（剩余 TP 目标应随新均价移动）。

**边界**：
- `old_tp` 为 None / 无 levels（理论上不该发生，因 FR-05 只在 `protected` 放行且开仓必经 `_set_position_tp`）→ 保持现有"`if old_tp and old_entry>0`"守卫，无 levels 时回退 `[old_tp]` 单级，仍经 sink。
- FR-05（3070-3074）已确保加仓只在 `protection_state=='protected'` 发生，levels 此时形态良好。

## 决策 2（P2-02 轻量）：恢复语义诚实，零控制流改动

`clear_symbol_halt` 清完 per-symbol halt 后，读全局 `get_halt_state()` 是否仍 halted；若是，把"全局仍 halt"事实透传给调用链（返回结构或结构化字段），由 `agents/trading/executor.py` 的 resume_symbol 处理 + `telegram_notifier.py` 的 `/resume_symbol` 文案回显"per-symbol 已清，但全局仍 halt，请用 /resume（带对账）解除"。

**明确不做**（out-of-scope，保持安全姿态）：
- `_halt_symbol` 仍无条件跳全局 fail-closed（真实故障该全局停）——不改。
- per-symbol halt 不加持久化——不改（拆后续 change）。
- `/resume_symbol` 不绕过 reconciliation 清全局——不改。

## 测试与同构

- 新增"加仓（含 tp_filled==1）后再跑 `_update_trailing` 不 halt 且 `take_profit==take_profit_levels[0]`"同构用例。
- 多级 levels 加仓后各级比例保持的断言。
- `clear_symbol_halt` 在全局 halt 仍在时回显提示的断言。
- CLAUDE.md 红线：核对 `event_backtest.py` 是否复刻加仓 TP 路径；若复刻则同步平移逻辑，否则 tasks 记录"加仓 TP 不进回测决策路径"的理由。
- 基线 `1066 passed` 变更后须全绿。

## 风险

- 改动集中在单函数 `add_to_position` 的 TP 段 + `clear_symbol_halt` 回显，blast radius 小。
- 主要风险是 tp_filled>0 时平移语义——已论证 safe，但必须有覆盖 tp_filled==1 加仓的测试坐实。
