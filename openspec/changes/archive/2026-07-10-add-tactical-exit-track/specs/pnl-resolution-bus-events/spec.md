## ADDED Requirements

### Requirement: PnL resolution events carry track and exit-profile metadata
The system SHALL propagate `track`, `exit_profile`, `slot_type`, and Tactical close/protection reason through `pnl_resolved` and `pnl_mismatch` events when those fields are known from the position, correction, execution result, or resolver evidence.

#### Scenario: Tactical pnl_resolved includes track fields
- **WHEN** a Tactical position receives a final `pnl_resolved` event
- **THEN** the event payload SHALL include `track=tactical` and `exit_profile=tactical_v1`
- **AND** Reviewer and Judge SHALL be able to consume the event without falling back to Main attribution

#### Scenario: Missing legacy fields remain backward compatible
- **WHEN** an older event lacks `track` or `exit_profile`
- **THEN** consumers SHALL default to existing Main-compatible behavior
- **AND** they SHALL NOT fail processing because the new fields are absent

### Requirement: Tactical close cause is preserved through resolution
The system SHALL preserve Tactical local close causes through asynchronous PnL resolution. Tactical close causes MUST coexist with existing `close_cause`, `final_close_cause`, `is_strategy_stop`, `close_evidence`, and `resolution_id` fields.

#### Scenario: Tactical invalidation close survives resolver upgrade
- **WHEN** a Tactical position is locally closed because of thesis invalidation
- **AND** the PnL is later upgraded by resolver
- **THEN** the final event SHALL still expose the Tactical invalidation reason
- **AND** existing final close cause fields SHALL remain present

#### Scenario: Exchange SL preserves Tactical attribution
- **WHEN** a Tactical position closes through exchange protective SL
- **THEN** the final PnL event SHALL include the exchange SL cause
- **AND** it SHALL still include `track=tactical` so Tactical risk metrics receive the loss
