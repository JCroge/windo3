## MODIFIED Requirements

### Requirement: Tactical episodes SHALL deduplicate one structural market opportunity
The system SHALL assign a durable `episode_id` by symbol, direction, and active 15m structure epoch. Exact plan prices SHALL be represented by a separate `plan_hash` and MUST NOT define episode identity. An attempted, missed, invalidated, capacity-skipped, or closed episode MUST NOT become eligible for another live attempt until reset evidence creates a new episode. A terminal episode MAY renew when structure data is available, the candidate side is not blocked, and either the closed 15m bar is newer than the episode's recorded bar or the structure token has changed. A neutral bias SHALL be accepted for this fresh-evidence renewal; neutral bias without fresh evidence SHALL remain ineligible.

#### Scenario: Repeated plans remain one episode
- **WHEN** repeated Tactical rows have the same symbol, direction, and active 15m structure but slightly different entry, SL, or TP values
- **THEN** they SHALL share one episode id
- **AND** at most one live attempt SHALL occur

#### Scenario: Fresh neutral evidence creates a new episode
- **WHEN** a prior episode is terminal
- **AND** a later candidate has available 15m structure, the candidate side is not blocked, and the closed-bar timestamp or structure token is newer
- **AND** the 15m bias is neutral
- **THEN** the system SHALL persist reset evidence
- **AND** it SHALL create a new episode id for the candidate

#### Scenario: Neutral without fresh evidence remains a duplicate
- **WHEN** a terminal episode receives a neutral candidate with the same closed-bar timestamp and structure token
- **THEN** the system SHALL reject the candidate as `duplicate_episode`
- **AND** it SHALL NOT create a new intent

#### Scenario: Blocked neutral evidence cannot renew
- **WHEN** a terminal episode receives a neutral candidate while the candidate side is blocked
- **THEN** the system SHALL reject the candidate as `opposing_block`
- **AND** it SHALL NOT create a new episode

#### Scenario: Structure reset creates a new episode
- **WHEN** an opposing 15m block occurs, direction returns to neutral before reforming, or a new confirmed pivot/structure break appears after the prior episode terminates
- **THEN** the system SHALL create a new episode id for a later compatible signal
- **AND** the reset evidence SHALL be persisted

#### Scenario: Historical episode terminates after a newer epoch exists
- **WHEN** an in-flight intent belongs to an older episode and a reset has already made a newer episode current for the same symbol and direction
- **AND** the older intent later reaches TP, SL, max hold, or another terminal outcome
- **THEN** the older episode SHALL be consumed exactly once by its own episode id
- **AND** the newer current epoch SHALL remain unchanged across event replay and process restart
