## MODIFIED Requirements

### Requirement: TP Field Single Source of Truth
All writes to `position.take_profit` and `position.take_profit_levels` SHALL
go through the single setter `_set_position_tp(position, tp_first, tp_levels)`
that enforces `position.take_profit == position.take_profit_levels[0]`. This
applies to EVERY post-open write path that mutates TP, INCLUDING
`add_to_position` (加仓), which recomputes TP against the new weighted-average
entry. Writing scalar `take_profit` without the matching `take_profit_levels`
update through the setter is prohibited. Direct mutation that violates this
invariant SHALL halt the symbol and emit a `tp_invariant_breach` risk alert
when partial_tp_1/partial_tp_2 is about to fire.

When `add_to_position` recomputes TP after a successful add, it SHALL shift
every element of `take_profit_levels` by that element's own
distance-from-old-entry ratio applied to the new entry (mirroring the SL
distance-ratio recompute), then write both fields via `_set_position_tp`. The
shift SHALL preserve multi-level structure and SHALL NOT alter `tp_filled`. An
add that occurs after a partial TP fill (`tp_filled > 0`) SHALL NOT breach the
invariant.

#### Scenario: 加仓后 TP 不变量保持，不触发误熔断
- **WHEN** 一笔已开多仓 `take_profit_levels=[L0, L1]`、`take_profit==L0`、`protection_state=='protected'`
- **AND** `add_to_position` 成功加仓推高加权均价
- **THEN** `position.take_profit == position.take_profit_levels[0]`
- **AND** 下一轮 `_update_trailing` MUST NOT 触发 `tp_invariant_breach` halt

#### Scenario: 多级 TP 加仓后各级距离比例保持
- **WHEN** 加仓前 `take_profit_levels` 各级距 old_entry 的比例为 `[d0, d1]`
- **THEN** 加仓后各级距 new_entry 的比例仍为 `[d0, d1]`（按持仓方向取 ± 号），多级结构不被压平

#### Scenario: partial-TP 已部分成交后加仓
- **WHEN** `tp_filled == 1` 且 `add_to_position` 成功
- **THEN** `tp_filled` MUST 仍为 1
- **AND** `take_profit == take_profit_levels[0]` 不变量保持
- **AND** MUST NOT 触发 `tp_invariant_breach` halt
