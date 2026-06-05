## ADDED Requirements

### Requirement: Route-Consistent Short Risk Gate

The Judge SHALL evaluate main-path and deferred-path `open_short` candidates with the same side-aware short risk gate before publishing an executable short decision. A candidate that fails the gate SHALL NOT be published as `main_direct` `open_short`.

#### Scenario: Main path rejects bullish daily short
- **WHEN** a main-path `ma_aligned_short` candidate has `symbol_daily_bias=bullish` and is not eligible for `probe_short`
- **THEN** Judge SHALL publish `hold` instead of `open_short`
- **AND** the rejection reason SHALL include `daily_bearish_required`

#### Scenario: Deferred path matches main path rejection
- **WHEN** the same `open_short` candidate is evaluated through the deferred entry route
- **THEN** Judge SHALL produce the same short gate rejection class as the main path
- **AND** no route SHALL bypass the daily/range/pre-move/score short gate semantics

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
