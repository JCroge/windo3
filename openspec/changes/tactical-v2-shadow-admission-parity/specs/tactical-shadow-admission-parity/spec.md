## ADDED Requirements

### Requirement: Tactical admission parity SHALL use normalized episodes
The system SHALL compare Legacy Shadow Tactical and Tactical V2 structural eligibility at the unique candidate/episode level. Repeated Shadow rows sharing the same candidate identity and active structure SHALL be reported as duplicate observations, not as separate required V2 positions. Every normalized candidate SHALL have an explicit normalized eligibility outcome of accepted, intentional duplicate, other rejection, or unknown historical handling evidence. Normalized eligibility is a research projection that MAY terminate a prior normalized opportunity at an audited opportunity boundary; it SHALL be reported separately from real Controller admission, intent creation, exchange entry, fill, and settlement outcomes.

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
- **AND** a fresh real Controller replay SHALL run for every iteration and report its own stable intent, receipt, result, sequence, and integrity projection

#### Scenario: Normalized eligibility and Controller admission are separate
- **WHEN** normalized opportunity boundaries produce five eligible episodes but the fixture has no terminal lifecycle evidence to release same-symbol exposure
- **THEN** the system SHALL report five normalized eligible opportunities separately from two real Controller intents
- **AND** it SHALL report the other three Controller outcomes as `same_symbol_exposure`
- **AND** it SHALL NOT claim five Controller admissions, orders, fills, or settlements

#### Scenario: Executable entry remains a separate gate
- **WHEN** admission parity accepts a normalized candidate
- **THEN** V2 SHALL still evaluate executable bid/ask price, entry drift, TTL, capacity, and protection state before live exposure
- **AND** admission parity SHALL NOT claim that the candidate was exchange-filled

### Requirement: Tactical V2 live operation SHALL require one active Main
Until the complete live lifecycle has a cross-process lease and fencing token, one state namespace and exchange account SHALL have exactly one active Main process. The ledger and candidate-admission lock SHALL NOT be represented as fencing quote handling, submit/reconcile/cancel, protection, close, PnL, or status projection operations.

#### Scenario: Controlled Main restart does not overlap processes
- **WHEN** Tactical V2 Main is restarted for a namespace and exchange account
- **THEN** operations SHALL confirm the old Main has exited before starting its replacement
- **AND** overlapping Main processes SHALL remain unsupported and NO-GO

#### Scenario: Candidate concurrency evidence is not lifecycle fencing evidence
- **WHEN** the two-process candidate regression proves one intent and one receipt for a duplicate delivery
- **THEN** the evidence SHALL be scoped to ledger sequence and candidate admission/receipt serialization
- **AND** it SHALL NOT authorize overlapping Main live lifecycle execution
