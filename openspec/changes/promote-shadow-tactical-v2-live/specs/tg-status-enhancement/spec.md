## ADDED Requirements

### Requirement: Tactical V2 SHALL publish one atomic operational status snapshot
The Tactical V2 engine SHALL atomically write a namespace-aware status snapshot at least every 30 seconds and after material lifecycle or governor transitions. The snapshot SHALL include `updated_at`, mode and version, configured margin and slot limit, active/pending/free slot counts and symbols, rolling 24-hour final PnL, active loss streak, timed pause and integrity-halt state, episode outcome counts, protection and reconciliation health, and shadow/live parity mismatch counts. This snapshot SHALL be a read model only and MUST NOT become an admission or exit authority.

#### Scenario: Material transition updates the snapshot
- **WHEN** an intent enters pending, fills, terminates, closes, changes a circuit state, or changes protection integrity
- **THEN** Tactical V2 SHALL atomically refresh the operational snapshot
- **AND** the snapshot SHALL describe state derived from the durable Tactical ledger and current reconciliation result

#### Scenario: Telegram status cannot change risk state
- **WHEN** Telegram reads or formats the Tactical V2 snapshot
- **THEN** it SHALL NOT mutate a slot, episode, PnL record, pause, or integrity halt
- **AND** Tactical admission SHALL continue to use the persistent governor rather than Telegram data

### Requirement: `/status` SHALL display compact Tactical V2 execution state
Telegram `/status` SHALL render the Tactical V2 snapshot as a compact section containing mode/version, `100U x 3` configuration, active/pending/free slots, rolling 24-hour final PnL versus the `-15U` admission threshold, loss streak and 60-minute circuit state, episode outcomes, active/pending symbols, protection/reconciliation health, and shadow/live parity mismatch count. Admission pauses SHALL be labeled as blocking new Tactical opens while existing positions remain managed.

#### Scenario: Healthy Tactical V2 state is fully visible
- **WHEN** a fresh snapshot reports live V2 mode, one active slot, one pending slot, no circuit, and verified protection
- **THEN** `/status` SHALL show Tactical V2 live, `100U x 3`, `1 active / 1 pending / 1 free`, rolling PnL, streak, and circuit clear
- **AND** it SHALL show the active and pending symbols plus protection and parity state

#### Scenario: Rolling loss pause is distinguished from forced close
- **WHEN** rolling 24-hour final Tactical PnL is at or below `-15U`
- **THEN** `/status` SHALL show new Tactical admission paused by rolling loss
- **AND** it SHALL state or clearly imply that existing Tactical positions remain managed rather than force-closed by this threshold

#### Scenario: Integrity halt is visible and non-timed
- **WHEN** the snapshot reports unresolved ownership or protection ambiguity
- **THEN** `/status` SHALL show Tactical integrity halt with the affected symbols or count
- **AND** it SHALL NOT display an automatic expiry time for that halt

### Requirement: Tactical status freshness SHALL fail visibly
Telegram SHALL treat the Tactical snapshot as stale when `updated_at` is older than the configured freshness threshold, defaulting to 90 seconds. A missing, malformed, stale, or non-finite Tactical snapshot SHALL render `STALE` or unknown values and MUST NOT be presented as healthy. Failure to read Tactical data MUST NOT prevent existing global halt, per-symbol halt, agent, or DLQ status from rendering.

#### Scenario: Stale snapshot is not shown as healthy
- **WHEN** the Tactical snapshot is older than 90 seconds under the default configuration
- **THEN** `/status` SHALL label the Tactical section `STALE`
- **AND** it SHALL NOT claim that slots, circuit, protection, or parity are current

#### Scenario: Non-finite PnL degrades safely
- **WHEN** the Tactical snapshot contains a non-finite rolling PnL value
- **THEN** `/status` SHALL render Tactical PnL as unknown or invalid
- **AND** the Telegram command SHALL continue rendering other status sections

## MODIFIED Requirements

### Requirement: `/status` SHALL distinguish global halt, per-symbol halt, and Tactical circuit

Telegram `/status` SHALL display global halt state, per-symbol halt state, and Tactical V2 circuit state as separate status lines. A global protection halt MUST NOT be presented in a way that implies the Tactical circuit is paused. Tactical circuit state SHALL be read only from the freshness-checked Tactical V2 operational snapshot; missing legacy risk-guard circuit data MUST NOT be interpreted as a healthy V2 circuit.

#### Scenario: global protection halt while Tactical circuit is not paused
- **WHEN** `halt_state.halted == true` with reason `okx_sl_algo_unresolved:WLD-USDT-SWAP`
- **AND** a fresh Tactical V2 snapshot reports no timed pause or integrity halt
- **THEN** `/status` MUST show global halt as active with the OKX protection reason
- **AND** `/status` MUST show Tactical circuit as not paused
- **AND** the message MUST NOT imply Tactical loss circuit caused the halt

#### Scenario: Tactical circuit paused while global halt is clear
- **WHEN** `halt_state.halted == false`
- **AND** a fresh Tactical V2 snapshot reports a future pause deadline or active integrity halt
- **THEN** `/status` MUST show global halt as inactive
- **AND** `/status` MUST show Tactical circuit as paused with its pause reason

#### Scenario: status data missing degrades safely
- **WHEN** the Tactical V2 snapshot is missing, unreadable, malformed, or stale
- **THEN** `/status` MUST still show global halt and per-symbol halt state
- **AND** Tactical circuit line MUST degrade to an unknown or `STALE` marker rather than failing the command
