## ADDED Requirements

### Requirement: Tactical candidate handling SHALL persist a durable receipt
The V2 candidate consumer SHALL append one handling receipt for every consumed `tactical_candidate.v2` message. The receipt MUST contain candidate ID, source Shadow ID, message ID when available, normalized symbol, side, accepted flag, reason, episode ID when assigned, intent ID when created, evaluated timestamp, and replay flag. Receipt writes SHALL be append-only and replay-safe.

#### Scenario: Accepted candidate has a receipt
- **WHEN** a candidate passes validation, episode assignment, governor admission, and intent creation
- **THEN** the system SHALL persist an accepted receipt with the episode ID and intent ID

#### Scenario: Duplicate candidate has a receipt
- **WHEN** a candidate is rejected because its episode is already consumed
- **THEN** the system SHALL persist a rejected receipt with `reason=duplicate_episode`
- **AND** the receipt SHALL reference the existing episode ID

#### Scenario: Validation or admission rejection has a receipt
- **WHEN** a candidate is invalid, expired, blocked, over capacity, or otherwise rejected before intent creation
- **THEN** the system SHALL persist a rejected receipt with the exact reason
- **AND** it SHALL NOT fabricate an intent ID

#### Scenario: Historical absence remains unknown
- **WHEN** an old candidate has no persisted handling receipt
- **THEN** replay/reporting SHALL classify its handling evidence as unknown
- **AND** it SHALL NOT infer that the message was consumed or lost

#### Scenario: Receipt replay is idempotent
- **WHEN** the V2 event ledger is replayed or the process restarts
- **THEN** receipt history SHALL remain ordered and unchanged
- **AND** replay SHALL NOT create a second intent or a second handling decision
