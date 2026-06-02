# Capability: entry-drift-policy (delta)

## ADDED Requirements

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

### Requirement: Two-Gate Execution
The drift gate SHALL run twice on the limit-then-market path:
1. Gate 1: at executor entry, before any order submission
2. Gate 2: after a 30s limit order timeout, before the fallback market order

Both gates SHALL use the original `plan.entry_ref` as the drift baseline. The
recomputed plan from Gate 1 SHALL NOT be passed as input to Gate 2.

### Requirement: TP Field Single Source of Truth
All writes to `position.take_profit` and `position.take_profit_levels` SHALL
go through a single setter that enforces
`position.take_profit == position.take_profit_levels[0]`. Direct mutation
that violates this invariant SHALL halt the symbol and emit a
`tp_invariant_breach` risk alert when partial_tp_1/partial_tp_2 is about to
fire.
