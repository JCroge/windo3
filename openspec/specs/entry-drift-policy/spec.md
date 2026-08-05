## Purpose

Define entry-drift behavior and anchor validation for executor and sidecar live opens so stale or malformed plans cannot submit unsafe orders.

## Requirements

### Requirement: Entry Drift Classification
The system SHALL classify the relative drift between Judge plan's `entry_ref`
anchor and the executor's live ticker price into one of four bands and act
accordingly:

- `accept` (drift ≤ 0.5%): proceed with the original plan unchanged
- `small` (0.5% < drift ≤ 2%): recompute SL/TP by sl_pct/tp_pct ratios on the
  new entry, re-check R:R against the plan's original floor; pass = accept
  recomputed plan, fail = reject with reason `drift_rr_floor_fail`
- `medium` (2% < drift ≤ 5%): recompute as above but with floor + 0.20
  absolute bump
- `abandon` (drift > 5%): reject with reason `drift_too_large`

#### Scenario: 5/30 XLM stale plan abandons cleanly
- **WHEN** Judge plan has entry_ref=0.2179 and executor sees live price 0.2336
  (drift 7.2%)
- **THEN** the drift gate returns decision=abandon, reason=drift_too_large
- **AND** no order is submitted to the exchange
- **AND** execution_result.v2 is published with status=rejected,
  reason=drift_too_large

#### Scenario: medium band recalculation passes when R:R clears bumped floor
- **WHEN** drift is 3% and recomputed R:R is 2.30 with original floor 2.00
- **THEN** the gate returns decision=recalc_pass, rr_floor_used=2.20

#### Scenario: medium band recalculation fails when R:R below bumped floor
- **WHEN** drift is 3% and recomputed R:R is 2.10 with original floor 2.00
- **THEN** the gate returns decision=recalc_fail, reason=drift_rr_floor_fail

### Requirement: Plan Field Fail-Safe
The system SHALL accept the original plan and emit a
`plan_missing_entry_ref` risk alert when the plan lacks any of `entry_ref`,
`sl_pct`, or `tp_pct`. The drift_pct of such a fail-safe accept SHALL be 0.0
to make the path identifiable in attribution downstream.

#### Scenario: Missing drift fields emit identifiable fail-safe alert
- **WHEN** a non-sidecar executor plan lacks `entry_ref`, `sl_pct`, or `tp_pct`
- **THEN** the drift gate SHALL accept the original plan with `drift_pct=0.0`
- **AND** it SHALL emit a `plan_missing_entry_ref` risk alert

### Requirement: Two-Gate Execution
The drift gate SHALL run twice on the limit-then-market path:
1. Gate 1: at executor entry, before any order submission
2. Gate 2: after a 30s limit order timeout, before the fallback market order

Both gates SHALL use the original `plan.entry_ref` as the drift baseline. The
recomputed plan from Gate 1 SHALL NOT be passed as input to Gate 2.

#### Scenario: Fallback market order rechecks original drift baseline
- **WHEN** a limit order times out and the executor considers fallback market execution
- **THEN** the second drift gate SHALL compare the live price to the original `plan.entry_ref`
- **AND** it SHALL NOT use a recomputed Gate 1 plan as the Gate 2 baseline

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

### Requirement: Sidecar live opens SHALL enforce stale-entry drift protection
The sidecar live open path SHALL evaluate live price drift against the Tactical shadow plan entry reference before submitting a market order. If explicit drift anchors are missing, the sidecar SHALL derive stop and TP percentages from `entry_ref`, `stop_loss`, and the first `take_profit` level when possible. A stale sidecar plan beyond the configured hard drift bound SHALL be rejected before order submission.

#### Scenario: Large sidecar entry drift rejects before order
- **WHEN** a sidecar Tactical plan has `entry_ref`
- **AND** the current market price drifts beyond the configured hard drift bound from that entry reference
- **THEN** `open_sidecar_plan` SHALL reject the open before calling `create_order`
- **AND** the sidecar SHALL record a drift rejection audit event

#### Scenario: Sidecar drift decision is recorded on accepted open
- **WHEN** a sidecar Tactical plan passes stale-entry drift protection
- **THEN** the sidecar SHALL persist enough drift metadata on the position or audit stream to explain the admission decision
- **AND** the open SHALL still satisfy existing SL-side, slippage, precheck, min-size, and protective-SL verification checks

#### Scenario: Missing drift anchors fail safely
- **WHEN** a sidecar Tactical plan cannot provide or derive enough information for stale-entry drift protection
- **THEN** the sidecar SHALL reject the open or emit an explicit fail-safe audit reason before order submission
- **AND** it SHALL NOT silently bypass drift protection

### Requirement: Tactical V2 entry drift SHALL use the frozen R anchor
Plans with `exit_profile=tactical_v2` SHALL bypass the Main percentage drift classification, plan-field fail-safe, and limit-to-market fallback. Tactical V2 SHALL calculate `R=abs(entry_ref-stop_loss)` from the immutable intent and calculate worse-side drift from executable ask for longs or executable bid for shorts. An immediate entry MAY occur only when worse-side drift is at most `0.10R` and price has not reached a pre-fill terminal boundary. Otherwise Tactical V2 SHALL keep one limit order at the frozen entry for at most 900 seconds and MUST NOT translate entry, SL, or TP to the current price.

#### Scenario: Main drift behavior remains unchanged
- **WHEN** an executable plan does not have `exit_profile=tactical_v2`
- **THEN** the existing percentage drift classification and two-gate Main execution policy SHALL apply
- **AND** this Tactical override SHALL NOT change its entry, SL, or TP handling

#### Scenario: Worse-side drift within point one R enters without mutation
- **WHEN** a long executable ask is no more than `0.10R` above its frozen entry, or a short executable bid is no more than `0.10R` below its frozen entry
- **AND** neither frozen TP nor frozen SL has been reached
- **THEN** Tactical V2 MAY submit the immediate entry
- **AND** it SHALL preserve the frozen entry reference, SL, and TP

#### Scenario: Price near the target is not chased
- **WHEN** a long executable ask is more than `0.10R` above its frozen entry, or a short executable bid is more than `0.10R` below its frozen entry
- **AND** the price has not yet reached the frozen TP
- **THEN** Tactical V2 SHALL place or retain a limit only at the frozen entry
- **AND** it SHALL NOT submit a market order at the current price

#### Scenario: Target already reached terminates the episode
- **WHEN** a Tactical V2 intent has not filled
- **AND** executable price reaches or crosses its frozen TP
- **THEN** Tactical V2 SHALL cancel any remaining entry order and mark the episode `missed_after_target`
- **AND** a later return to the frozen entry SHALL NOT permit another attempt in the same episode

#### Scenario: Invalid R fails closed
- **WHEN** a Tactical V2 intent lacks finite entry or stop values, has `R<=0`, or cannot obtain the required executable side price
- **THEN** Tactical V2 SHALL record an explicit terminal rejection or integrity reason before exchange submission
- **AND** it SHALL NOT fall back to Main drift handling or silently accept the plan

#### Scenario: Tactical limit expiry has no market fallback
- **WHEN** a Tactical V2 original-entry limit remains unfilled for 900 seconds
- **THEN** the system SHALL cancel its remainder and mark the episode expired
- **AND** the Main 30-second fallback market path SHALL NOT run for that intent
