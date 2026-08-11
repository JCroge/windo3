## ADDED Requirements

### Requirement: Tactical admission parity SHALL use normalized episodes
The system SHALL compare Legacy Shadow Tactical and Tactical V2 admission at the unique candidate/episode level. Repeated Shadow rows sharing the same candidate identity and active structure SHALL be reported as duplicate observations, not as separate required V2 positions. Every normalized candidate SHALL have an explicit V2 outcome of accepted, intentional duplicate, other rejection, or unknown historical handling evidence.

#### Scenario: PUMP neutral candidates renew after fresh evidence
- **WHEN** the replay contains the terminal PUMP episode followed by unblocked neutral candidates on two newer closed 15m bars
- **THEN** the normalized parity result SHALL contain two eligible PUMP episodes
- **AND** repeated rows on each bar SHALL be marked `duplicate_episode`

#### Scenario: Raw Shadow rows are not counted as independent V2 positions
- **WHEN** multiple Legacy Shadow rows map to one candidate identity and structure epoch
- **THEN** parity SHALL count one normalized opportunity
- **AND** it SHALL retain every source Shadow ID as supporting evidence

#### Scenario: Replay result is deterministic
- **WHEN** the same candidate sequence and initial episode state are replayed 100 times
- **THEN** accepted episode IDs and rejection reasons SHALL be identical in every run

#### Scenario: Executable entry remains a separate gate
- **WHEN** admission parity accepts a normalized candidate
- **THEN** V2 SHALL still evaluate executable bid/ask price, entry drift, TTL, capacity, and protection state before live exposure
- **AND** admission parity SHALL NOT claim that the candidate was exchange-filled
