## ADDED Requirements

### Requirement: Shadow Tactical live mirror sidecar
The system SHALL provide a separate sidecar runner that can mirror new Tactical shadow records to live execution for a configured 24-hour window.

#### Scenario: Sidecar mirrors new Tactical shadow record
- **WHEN** the sidecar is running
- **AND** `data/rejected_signal_events.jsonl` receives a new `rejected_plan_created` event
- **AND** the event record has `track=tactical` or `exit_profile=tactical_v1`
- **AND** the record contains a valid symbol, side, entry price, stop loss, take profit, and leverage
- **THEN** the sidecar SHALL create a live execution plan from that record
- **AND** it SHALL record the shadow record id in sidecar state before or atomically with execution bookkeeping

#### Scenario: Sidecar ignores non-Tactical records
- **WHEN** the sidecar reads a `rejected_plan_created` event whose record is not Tactical
- **THEN** it SHALL NOT create a live execution plan
- **AND** it SHALL preserve its watermark so the event is not retried as an error

#### Scenario: Sidecar does not backfill by default
- **WHEN** the sidecar starts without an explicit backfill option
- **THEN** it SHALL process only events written after its start watermark
- **AND** it SHALL NOT place live orders for older shadow records already present in the file

### Requirement: Shadow record fields drive live plan mapping
The sidecar SHALL map the live order plan directly from the shadow record payload. The mapped plan SHALL preserve `symbol`, `side`, `entry_price`, `stop_loss`, `take_profit`, `leverage`, `exit_profile`, `tactical_source`, `tactical_max_hold_minutes`, and available attribution fields.

#### Scenario: Tactical fields are preserved
- **WHEN** a Tactical shadow record is mapped to a live sidecar plan
- **THEN** the live plan SHALL use the record's side, SL, TP list, leverage, Tactical max hold, and exit profile
- **AND** it SHALL include the shadow record id as the entry request id or equivalent audit key

#### Scenario: Tactical fields are persisted on live sidecar position
- **WHEN** a mapped Tactical shadow record is opened by the sidecar
- **THEN** the persisted sidecar position SHALL include `track=tactical`, `exit_profile=tactical_v1`, `tactical_source`, `tactical_max_hold_minutes`, `entry_ref`, and sidecar gate metadata
- **AND** local sidecar monitoring SHALL evaluate Tactical exit rules from the persisted position rather than treating it as a generic position

#### Scenario: Missing mechanical fields fail closed
- **WHEN** a Tactical shadow record is missing side, entry price, stop loss, take profit, or leverage
- **THEN** the sidecar SHALL reject that record without placing a live order
- **AND** it SHALL write a sidecar audit event with the missing-field reason

### Requirement: Strategy admission gates are bypassed for sidecar admission
The sidecar SHALL NOT use Main Judge, CandidateRanker, Tactical RR/EV/cost gates, Tactical slot gates, Tactical quality gates, Tactical daily-loss admission gates, or Tactical loss-streak admission gates to decide whether a Tactical shadow record is admitted to the sidecar live experiment.

#### Scenario: Low RR or failed Tactical gate metadata does not block sidecar admission
- **WHEN** a Tactical shadow record contains low RR/EV/cost-gate metadata or a Tactical gate failure reason
- **AND** the record has the mechanical fields required for execution
- **THEN** the sidecar SHALL still attempt to mirror the record live
- **AND** it SHALL include the original gate metadata in sidecar audit output

### Requirement: Mechanical execution checks remain fail-closed
The sidecar SHALL preserve mechanical exchange and protection checks needed to avoid malformed orders, unbounded exposure, or naked positions. These checks include valid SL side, valid symbol/side, configured max trade amount, effective balance cap, amount precision/min-size, free balance, orderbook spread/depth, known OKX position mode, order placement result, and protective stop-loss creation or verification.

#### Scenario: Invalid stop side blocks execution
- **WHEN** a mapped sidecar plan has a stop loss on the wrong side of the entry/live execution price
- **THEN** the sidecar SHALL NOT leave a live position open from that plan
- **AND** it SHALL write a sidecar audit event with `invalid_stop_side`

#### Scenario: Protective SL cannot be verified
- **WHEN** a sidecar entry order fills
- **AND** the protective SL cannot be created or verified
- **THEN** the sidecar SHALL fail closed by closing the sidecar-owned exposure or halting further sidecar opens for that symbol
- **AND** it SHALL write a sidecar audit event describing the protection failure

#### Scenario: Configured hard exposure limits are enforced
- **WHEN** a Tactical shadow record maps to a sidecar plan
- **AND** the requested margin would exceed configured max trade amount, effective balance cap, or free-balance requirements
- **THEN** the sidecar SHALL reject the record without placing a live order
- **AND** it SHALL write a sidecar audit event with the hard-limit reason

### Requirement: Sidecar state is separated from Main state
The sidecar SHALL use state and ledger paths separate from the Main process. It SHALL NOT write to Main `data/positions.json`, Main live order events, or Main live lifecycle files unless explicitly configured for a diagnostic-only dry run.

#### Scenario: Sidecar writes separate files
- **WHEN** the sidecar records an attempted, filled, rejected, closed, or skipped mirror event
- **THEN** it SHALL write to sidecar-specific state/audit files
- **AND** it SHALL NOT mutate Main position or ledger files

#### Scenario: Exchange-flat reconciliation records a sidecar close event
- **WHEN** sidecar monitoring proves that an active sidecar-owned local position is flat on the exchange
- **THEN** it SHALL close the sidecar owner record and remove the local sidecar position
- **AND** it SHALL write a sidecar ledger close event or pending external close event with the original shadow id, symbol, side, opened timestamp, closed timestamp, amount, leverage, and protection identifiers
- **AND** it SHALL leave the exchange-derived final PnL resolution pending when fills are not yet resolved locally

#### Scenario: Main process is not restarted
- **WHEN** the sidecar starts for the 24-hour run
- **THEN** the existing `run_agents.py` process SHALL remain running
- **AND** the sidecar start procedure SHALL NOT require changing Main Tactical `.env` gates or restarting Main

### Requirement: Same-account owner isolation
The system SHALL support same-account sidecar deployment by recording sidecar ownership and preventing Main from taking ownership of sidecar account objects.

#### Scenario: Main sync skips sidecar-owned position
- **WHEN** Main `sync_positions()` sees an OKX account-level position
- **AND** the position matches an active sidecar ownership record
- **THEN** Main SHALL NOT backfill that position into Main `positions.json`
- **AND** Main SHALL record or log that the position was ignored as sidecar-owned

#### Scenario: Main migration preserves foreign sidecar SL algo
- **WHEN** Main OKX algo migration sees a pending SL algo
- **AND** the algo has a sidecar or otherwise foreign owner tag
- **THEN** Main SHALL NOT cancel, replace, or adopt that algo
- **AND** Main SHALL continue processing Main-owned algos normally

#### Scenario: Same-account same-symbol guard blocks inseparable exposure
- **WHEN** a sidecar Tactical shadow record targets a symbol that already has non-sidecar account exposure
- **THEN** the sidecar SHALL reject or defer that record without opening new exposure
- **AND** it SHALL write a sidecar audit event with `same_symbol_account_exposure`

### Requirement: 24-hour stop semantics
The sidecar SHALL stop admitting new shadow records after the configured 24-hour window and SHALL provide a stop path for sidecar-owned exposure.

#### Scenario: Window expires
- **WHEN** the sidecar clock reaches `stop_at`
- **THEN** it SHALL stop processing new shadow records for live execution
- **AND** it SHALL leave a final sidecar audit event with processed, opened, rejected, and active counts

#### Scenario: Stop command handles sidecar-owned exposure
- **WHEN** a sidecar stop command is run
- **THEN** it SHALL cancel sidecar-owned pending orders where ownership can be proven
- **AND** it SHALL close sidecar-owned open positions where ownership can be proven
- **AND** it SHALL refuse to touch positions whose ownership cannot be proven from sidecar state/order tags
