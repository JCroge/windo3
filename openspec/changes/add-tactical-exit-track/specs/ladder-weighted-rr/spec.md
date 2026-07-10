## ADDED Requirements

### Requirement: Tactical R:R isolation from ladder-weighted Main R:R
The system SHALL keep Main Trend ladder-weighted `effective_risk_reward_ratio` separate from Tactical R:R. Tactical plans MUST expose their own Tactical R:R and EV fields and MUST NOT use Main Trend TP2/TP3 ladder assumptions for Tactical acceptance, sizing, ranking, or EV gates.

#### Scenario: Main ladder remains Main-only
- **WHEN** a candidate is classified as `track=main`
- **THEN** existing ladder-weighted R:R behavior MAY be used according to the Main Trend configuration
- **AND** the plan SHALL remain compatible with the existing ladder-weighted R:R requirements

#### Scenario: Tactical uses Tactical R:R
- **WHEN** a candidate is classified as `track=tactical`
- **THEN** acceptance and ranking SHALL use Tactical R:R and Tactical EV fields
- **AND** `effective_risk_reward_ratio` from Main ladder math SHALL NOT be the deciding Tactical acceptance value

#### Scenario: Reclassification recalculates payoff fields
- **WHEN** a Main candidate is downgraded into Tactical
- **THEN** the system SHALL recalculate stop distance, TP profile, net profit, net loss, R:R, and EV using the Tactical profile
- **AND** the plan SHALL retain both original Main diagnostic R:R and final Tactical R:R for audit
