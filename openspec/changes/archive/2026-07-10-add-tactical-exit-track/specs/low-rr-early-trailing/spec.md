## ADDED Requirements

### Requirement: Tactical exit profile does not reuse low_rr early trailing semantics
The system SHALL treat Tactical as a separate exit profile from `low_rr_extra`. Existing low-R:R early trailing behavior SHALL continue to apply only to low-R:R positions unless a position explicitly has `track=tactical`, in which case the Tactical exit lifecycle SHALL control protection, partial exits, thesis invalidation, and max hold.

#### Scenario: Low-R:R behavior remains unchanged
- **WHEN** a position has `slot_type=low_rr_extra` and no `track=tactical`
- **THEN** existing low-R:R early trailing activation and distance settings SHALL apply unchanged

#### Scenario: Tactical profile takes precedence
- **WHEN** a position has `track=tactical`
- **THEN** the Tactical exit lifecycle SHALL decide early protection and exit actions
- **AND** the generic low-R:R early trailing branch SHALL NOT override Tactical thesis-health or max-hold decisions

#### Scenario: Tactical is not reported as low-R:R by default
- **WHEN** a Tactical trade is recorded by Reviewer
- **THEN** it SHALL be segmented by `track=tactical`
- **AND** it SHALL NOT be counted as `is_low_rr=true` unless it also explicitly satisfies the low-R:R policy
