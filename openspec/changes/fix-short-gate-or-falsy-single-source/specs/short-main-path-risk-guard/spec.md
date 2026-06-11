## MODIFIED Requirements

### Requirement: Route-Consistent Short Risk Gate

The Judge SHALL evaluate main-path and deferred-path `open_short` candidates with the same side-aware short risk gate before publishing an executable short decision. A candidate that fails the gate SHALL NOT be published as `main_direct` `open_short`.

The short risk gate SHALL treat a present `position_in_24h_range` value of `0.0` (price at the true 24-hour low) as the literal value `0.0`, NOT coalesce it into a neutral default. Metric extraction SHALL distinguish a present zero from an absent value: only an absent (None) metric MAY fall back to its configured default.

The Judge SHALL implement the short structural gate (daily-bias / range-position / pre-move / RSI / score / higher-timeframe-votes) in exactly one function, `_classify_short_entry_risk`. `_apply_regime_policy` SHALL delegate to `_classify_short_entry_risk` rather than re-implement the gate inline, while retaining its `probe_short` routing shell. Missing-metric default values SHALL be identical across all callers of the gate.

#### Scenario: Main path rejects bullish daily short
- **WHEN** a main-path `ma_aligned_short` candidate has `symbol_daily_bias=bullish` and is not eligible for `probe_short`
- **THEN** Judge SHALL publish `hold` instead of `open_short`
- **AND** the rejection reason SHALL include `daily_bearish_required`

#### Scenario: Deferred path matches main path rejection
- **WHEN** the same `open_short` candidate is evaluated through the deferred entry route
- **THEN** Judge SHALL produce the same short gate rejection class as the main path
- **AND** no route SHALL bypass the daily/range/pre-move/score short gate semantics

#### Scenario: Price at 24h low is rejected, not coalesced
- **WHEN** an `open_short` candidate has `position_in_24h_range=0.0` (a present value, price at the 24h bottom), `daily_bias=bearish`, and is not a probe
- **AND** `0.0 < short_live_min_range_pos`
- **THEN** Judge SHALL reject the candidate with reason `range_position_too_low`
- **AND** the gate SHALL NOT substitute a neutral default (e.g. 0.5) for the present `0.0`

#### Scenario: Absent range metric falls back to a single shared default
- **WHEN** an `open_short` candidate has no `position_in_24h_range` in either `short_context` or `entry_context`
- **THEN** the gate SHALL use the same configured default value regardless of whether the candidate is evaluated via `_classify_short_entry_risk` or `_apply_regime_policy`

#### Scenario: Regime policy delegates to the single gate implementation
- **WHEN** `_apply_regime_policy` evaluates an `open_short` candidate's structural risk
- **THEN** it SHALL obtain the gate outcome from `_classify_short_entry_risk`
- **AND** it SHALL NOT contain a second inline evaluation of the daily-bias / range / pre-move / RSI / score / htf conditions
- **AND** when the gate outcome is `daily_bearish_required`, the existing `probe_short` routing shell SHALL still decide between routing to a probe and rejecting

#### Scenario: Short gate attribution preserved after delegation
- **WHEN** `_apply_regime_policy` accepts or rejects an `open_short` candidate through the delegated gate
- **THEN** attribution SHALL still include `short_gate_version`, `short_gate_decision`, `short_gate_reason`, and `llm_short_reversal_risk`
