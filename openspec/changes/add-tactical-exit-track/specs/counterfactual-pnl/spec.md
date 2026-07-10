## ADDED Requirements

### Requirement: Counterfactual records distinguish Main and Tactical outcomes
The system SHALL include track and exit-profile metadata in accepted and rejected counterfactual records. Replay and counterfactual reports SHALL be able to compare Main-only, Tactical-only, and incremental Tactical outcomes without mixing their PnL or win-rate samples.

#### Scenario: Rejected Tactical shadow carries track metadata
- **WHEN** a rejected or shadowed Tactical candidate is recorded
- **THEN** the counterfactual record SHALL include `track=tactical`, `exit_profile=tactical_v1`, Tactical R:R, Tactical EV, and Tactical source reason

#### Scenario: Main and Tactical replay are separable
- **WHEN** a replay report calculates PnL deltas
- **THEN** it SHALL provide separate buckets for Main and Tactical
- **AND** it SHALL NOT treat Tactical wins as Main Trend evidence

### Requirement: Tactical counterfactual exit model matches Tactical lifecycle
The system SHALL resolve Tactical counterfactual outcomes with Tactical max hold, Tactical TP/SL profile, and Tactical cost assumptions. Tactical counterfactuals MUST NOT use the 24h Main Trend hold window unless explicitly configured as a diagnostic comparison.

#### Scenario: Tactical shadow expires at Tactical max hold
- **WHEN** a Tactical counterfactual position remains unresolved past the configured Tactical max hold
- **THEN** the resolver SHALL close or mark it according to the Tactical max-hold rule
- **AND** the result SHALL record `tactical_max_hold` as the resolution reason

#### Scenario: Diagnostic Main comparison is labelled
- **WHEN** a report compares the same signal under Main and Tactical exit models
- **THEN** each result SHALL be labelled with the exit model used
- **AND** aggregate conclusions SHALL use the Tactical-labelled result for Tactical decisions

### Requirement: Tactical sample honesty gates
The system SHALL apply the existing counterfactual honesty gate principles to Tactical buckets. Tactical conclusions with fewer than 30 samples MUST be marked insufficient sample, 30-99 samples MUST be low confidence, and actionable Tactical conclusions MUST require sufficient sample size and net PnL confidence interval not crossing zero.

#### Scenario: Thin Tactical sample refuses conclusion
- **WHEN** a Tactical replay bucket has fewer than 30 samples
- **THEN** the report SHALL mark it insufficient sample
- **AND** it SHALL NOT recommend increasing Tactical exposure from that bucket

#### Scenario: Tactical actionable requires confidence
- **WHEN** a Tactical replay bucket has sufficient samples but its net PnL confidence interval crosses zero
- **THEN** the report SHALL NOT mark the bucket actionable
