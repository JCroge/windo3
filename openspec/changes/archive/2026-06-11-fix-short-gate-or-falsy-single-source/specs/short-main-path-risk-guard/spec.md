## ADDED Requirements

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

### Requirement: Hard RSI Threshold Preservation

The Judge SHALL preserve the existing hard no-short behavior for `open_short` when `RSI <= 30`. This threshold SHALL NOT be changed by the short main path parity gate.

#### Scenario: RSI hard threshold remains unchanged
- **WHEN** an `open_short` candidate has `RSI <= 30`
- **THEN** Judge SHALL apply the existing hard no-short/pending-pullback behavior
- **AND** this behavior SHALL remain distinct from the `short_live_min_rsi` gate

#### Scenario: RSI above hard threshold can still fail structural gate
- **WHEN** an `open_short` candidate has `RSI=31.5` or `RSI=34` and fails daily/range/pre-move/score short gate conditions
- **THEN** Judge SHALL reject the candidate through the structural short gate
- **AND** the rejection SHALL NOT be reported as a change to the `RSI <= 30` hard threshold

### Requirement: LLM Reversal Risk Tightening

The Judge SHALL detect LLM short reversal-risk text from parsed reasoning, key factors, and risk warnings when LLM action is `hold`. This signal SHALL be used for attribution and MAY tighten decisions only when independent market-structure short risk is present. It SHALL NOT be a standalone veto for all rule-signal shorts.

#### Scenario: Parsed do-not-short text is attributed
- **WHEN** LLM action is `hold` and reasoning contains text such as `禁止做空`, `超卖`, `看涨背离`, `支撑`, or `追空风险`
- **THEN** Judge SHALL set `llm_short_reversal_risk=true` in attribution
- **AND** the final decision SHALL still be based on the structural short gate outcome

#### Scenario: LLM parse failure does not allow structural-risk short
- **WHEN** LLM parsing yields default `hold` with empty reasoning
- **AND** the candidate fails daily/range/pre-move/score short gate conditions
- **THEN** Judge SHALL reject the candidate through structural short gate reasons
- **AND** the missing LLM reasoning SHALL NOT allow a `main_direct` short

### Requirement: Short Gate Attribution Versioning

The Judge SHALL include versioned short gate attribution on accepted and rejected short candidates so downstream metrics can separate pre-change and post-change behavior.

#### Scenario: Rejected short includes gate metadata
- **WHEN** Judge rejects an `open_short` candidate through the parity gate
- **THEN** the decision/rejected-plan attribution SHALL include `short_gate_version`, `short_gate_decision`, and `short_gate_reason`

#### Scenario: Accepted short includes pass metadata
- **WHEN** Judge accepts an `open_short` candidate after the parity gate
- **THEN** the executable decision attribution SHALL include `short_gate_version` and `short_gate_decision=pass`
