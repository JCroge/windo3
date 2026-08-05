## Purpose

Define Shadow Tactical sidecar ownership, monitoring, exit isolation, exchange-flat reconciliation, and ghost-exposure fail-closed behavior.

## Requirements

### Requirement: Sidecar SHALL persist canonical ownership identity for Tactical shadow positions
The sidecar SHALL persist both the internal shadow symbol and the exchange execution symbol for each open Tactical shadow position. The exchange execution symbol SHALL be used for market data and order calls, while the internal symbol SHALL be used for ownership lookup, audit, and cross-process matching. The sidecar SHALL fail closed if it cannot resolve the execution symbol for a new open.

#### Scenario: Internal shadow symbol resolves to exchange execution symbol
- **WHEN** the sidecar receives a Tactical shadow plan whose symbol is the internal form
- **THEN** it SHALL resolve the exchange execution symbol before submitting the order
- **AND** it SHALL persist both symbols in the owner record and position state

#### Scenario: Unresolvable symbol is rejected
- **WHEN** the sidecar cannot resolve a plan symbol to a valid exchange execution symbol
- **THEN** it SHALL reject the open
- **AND** it SHALL record an audit event instead of falling back to spot execution

### Requirement: Sidecar SHALL monitor open Tactical shadow positions while running
The sidecar SHALL periodically scan open sidecar-owned Tactical positions and evaluate exit conditions even when no new shadow events arrive. Monitoring SHALL be independent from event ingestion so that open positions continue to be managed during quiet periods.

#### Scenario: Idle event stream still triggers monitoring
- **WHEN** at least one sidecar-owned Tactical position remains open
- **AND** no new shadow events arrive during the next poll window
- **THEN** the sidecar SHALL still evaluate the open position for exit conditions

#### Scenario: Closed positions are skipped
- **WHEN** a sidecar-owned position is already marked closed in owner state
- **THEN** the next monitor cycle SHALL NOT re-evaluate it

### Requirement: Sidecar Tactical exits SHALL reuse Tactical exit semantics
Legacy sidecar positions SHALL retain the legacy sidecar exit semantics captured when they opened: TP1 reduces 50 percent, legacy invalidated or weakened-without-progress conditions may close the remainder, max hold closes the remainder, and protective stop handling remains authoritative. During retirement drain, these legacy positions MUST NOT be converted to Tactical V2 full-TP1 or V2 post-fill semantics. No new sidecar position may open after admission stop, and every drain exit SHALL continue to use owner-bound reduce/close handling.

#### Scenario: Existing sidecar TP1 retains legacy partial reduce
- **WHEN** a proven sidecar-owned position opened before admission stop reaches TP1 during drain
- **THEN** the sidecar SHALL trigger its legacy 50 percent reduce action
- **AND** it SHALL preserve and protect the remaining legacy position according to its captured state

#### Scenario: Existing sidecar invalidation retains captured behavior
- **WHEN** a proven sidecar-owned position opened before admission stop receives its legacy invalidation condition during drain
- **THEN** the sidecar SHALL request an owner-bound close of the remainder
- **AND** it SHALL record the legacy invalidation reason without classifying the close as Tactical V2

#### Scenario: New V2 position uses no legacy sidecar exit
- **WHEN** a canonical Tactical V2 intent fills after cutover
- **THEN** the sidecar SHALL NOT monitor, reduce, or close that V2 position
- **AND** the Tactical V2 full-TP1, full-SL, and 90-minute controller SHALL be its strategy owner

### Requirement: Sidecar exit actions SHALL be ownership-bound and isolated
The sidecar SHALL only submit exit actions for positions that it can prove are sidecar-owned. If a registry row cannot be matched to a live sidecar-owned position, the sidecar SHALL NOT reduce or close exchange exposure. The sidecar SHALL check exchange-side position state for the row's symbol: when the exchange confirms the symbol is flat, the sidecar SHALL reconcile the owner row closed and record a pending external-close ledger event; when the exchange state is present, unknown, or unsupported, the sidecar SHALL skip the exit action and record the skip. Sidecar exit actions SHALL not mutate main-process positions or any other non-sidecar state.

#### Scenario: Unproven owner is reconciled when exchange is flat
- **WHEN** an owner record exists with `status=open`
- **AND** the live sidecar position cannot be proven against current sidecar-owned position state
- **AND** the exchange position check for the owner symbol returns flat
- **THEN** the sidecar SHALL mark the owner row closed
- **AND** it SHALL record an exchange-flat audit event
- **AND** it SHALL write a pending external-close ledger event for later PnL resolution

#### Scenario: Unproven position with exchange exposure is skipped
- **WHEN** an owner record exists with `status=open`
- **AND** the live sidecar position cannot be proven against current sidecar-owned position state
- **AND** the exchange position check for the owner symbol returns present or unknown
- **THEN** the sidecar SHALL skip the exit action
- **AND** it SHALL record a skip audit event
- **AND** it SHALL NOT submit a close or reduce order

#### Scenario: Main process state is untouched
- **WHEN** a main-process position exists for the same symbol
- **THEN** the sidecar SHALL not reduce or close it unless it is separately proven sidecar-owned

### Requirement: Sidecar stop SHALL drain proven sidecar-owned exposure only
On shutdown or explicit stop, the sidecar SHALL close only proven open sidecar-owned Tactical positions, then mark those owner rows closed. Failure to confirm a close SHALL be recorded and SHALL not affect unrelated positions.

#### Scenario: Stop closes proven exposure
- **WHEN** stop is requested and an owner row is proven open
- **THEN** the sidecar SHALL close the position
- **AND** it SHALL mark the owner row closed

#### Scenario: Stop skips unproven exposure
- **WHEN** stop is requested and the position cannot be proven
- **THEN** the sidecar SHALL leave it untouched
- **AND** it SHALL record a skip audit event

### Requirement: Sidecar SHALL block unsafe same-symbol net-mode stacking
The sidecar SHALL NOT open a new live Tactical sidecar position for a symbol when OKX `net_mode` cannot represent that new open as a separately provable sidecar position. An existing open sidecar owner row or present exchange exposure for the same normalized symbol and side SHALL reject the new open unless an explicit aggregate-position model proves and manages the whole net exposure.

#### Scenario: Existing sidecar owner blocks same-symbol open
- **WHEN** a sidecar Tactical shadow record targets a symbol and side that already has an owner row with `status=open`
- **AND** the executor is operating in OKX `net_mode`
- **THEN** the sidecar SHALL reject the new open before submitting an exchange order
- **AND** it SHALL write a sidecar audit event with reason `same_symbol_sidecar_active`

#### Scenario: Existing exchange exposure blocks unmodeled stack
- **WHEN** a sidecar Tactical shadow record targets a symbol whose exchange position is present
- **AND** the exposure is not represented by a single proven aggregate sidecar position model
- **THEN** the sidecar SHALL reject the new open before submitting an exchange order
- **AND** it SHALL NOT rely on `--max-active` to permit same-symbol stacking

### Requirement: Sidecar SHALL detect ghost exposure and fail closed
The sidecar SHALL treat open owner rows plus present exchange exposure plus missing or unproven local sidecar position metadata as ghost exposure. Ghost exposure SHALL block further sidecar opens for that symbol, produce an audit event, and require operator or later repair flow intervention. The sidecar SHALL NOT close or reduce unproven exchange exposure automatically.

#### Scenario: Unproven owner with present exposure triggers ghost guard
- **WHEN** an owner record exists with `status=open`
- **AND** the live sidecar position cannot be proven against current sidecar-owned position state
- **AND** the exchange position check for the owner symbol returns present
- **THEN** the sidecar SHALL record a ghost-exposure audit event
- **AND** it SHALL halt or block further sidecar opens for that symbol
- **AND** it SHALL NOT submit a close or reduce order

#### Scenario: Missing protection escalates ghost exposure
- **WHEN** ghost exposure is detected for a sidecar-owned symbol
- **AND** pending exchange TP/SL protection for that symbol is absent or cannot be verified
- **THEN** the sidecar SHALL mark the audit event as requiring operator action
- **AND** repeated monitor passes SHALL NOT silently emit only `monitor_skipped_unproven`

### Requirement: Sidecar monitor SHALL not partially close ambiguous net-mode owner stacks
The sidecar monitor SHALL NOT close or reduce one proven owner row for a same-symbol net-mode stack while other open owner rows for the same exchange symbol remain unproven. It SHALL either manage one proven aggregate position consistently or fail closed with ghost-exposure audit.

#### Scenario: Multiple owner rows with one local symbol position are ambiguous
- **WHEN** multiple sidecar owner rows are open for the same exchange symbol and side
- **AND** executor local position state contains only one symbol-keyed sidecar position
- **THEN** the sidecar monitor SHALL NOT close only the matching row and leave the remaining owner rows open against present exchange exposure
- **AND** it SHALL record an ambiguous net-mode stack or ghost-exposure audit event

#### Scenario: Exchange flat reconciliation remains allowed
- **WHEN** multiple open owner rows exist for a symbol
- **AND** the exchange position check confirms the symbol is flat
- **THEN** the sidecar MAY reconcile those owner rows closed using the existing exchange-flat pending-ledger path

### Requirement: Sidecar retirement SHALL use an admission-stop and drain barrier
Tactical V2 live admission SHALL remain disabled until sidecar new admissions are stopped and the sidecar drain barrier is satisfied. During drain, the sidecar monitor SHALL remain resident and continue owner-bound management of existing proven sidecar positions and orders. The barrier SHALL require no sidecar pending entries, no proven open sidecar exposure, no unresolved sidecar protection ownership, and no undocumented pending final-PnL resolution.

#### Scenario: Admission stops before monitoring stops
- **WHEN** the operator begins Tactical V2 cutover
- **THEN** the sidecar SHALL reject new live opens before its position monitor is stopped
- **AND** proven existing sidecar exposure SHALL continue to be managed until reconciled flat

#### Scenario: Unresolved owner blocks V2 live cutover
- **WHEN** a sidecar owner row or exchange object has present or unknown exposure or protection state that cannot be reconciled
- **THEN** the sidecar drain barrier SHALL remain unsatisfied
- **AND** Tactical V2 live admission SHALL remain disabled

#### Scenario: Pending final PnL requires resolution or documentation
- **WHEN** all sidecar exchange exposure is flat but an external-close ledger item has no final PnL resolution
- **THEN** cutover SHALL remain blocked until the item is resolved or explicitly recorded as an accepted reconciliation exception
- **AND** the exception SHALL remain visible in the archived drain evidence

### Requirement: Tactical V2 SHALL not adopt legacy sidecar ownership
Legacy sidecar owner rows, local positions, pending orders, protection orders, and PnL records SHALL NOT be reclassified or imported as Tactical V2 live positions. Same-symbol legacy or ambiguous exposure SHALL block a Tactical V2 episode through the shared exposure/integrity gate. Tactical V2 SHALL create live exposure only from a new canonical V2 intent after the drain barrier passes.

#### Scenario: Legacy sidecar position is not adopted
- **WHEN** startup finds an open sidecar owner row for a symbol
- **THEN** Tactical V2 SHALL NOT create a V2 position from that row or manage it as a V2 strategy position
- **AND** V2 live admission SHALL remain blocked until the sidecar path reconciles the row

#### Scenario: Archived sidecar record cannot consume a V2 slot
- **WHEN** the sidecar drain is complete and historical owner records are archived closed
- **THEN** those records SHALL remain available for audit
- **AND** they SHALL NOT appear as active or pending Tactical V2 slots

### Requirement: Sidecar retirement evidence SHALL be archived and reversible only by explicit rollout action
After the drain barrier passes, the system SHALL atomically archive the sidecar admission state, final owner/protection reconciliation summary, final PnL exceptions, and cutover timestamp before enabling Tactical V2 live. Disabling Tactical V2 later SHALL stop new V2 intents and preserve management of filled V2 positions, but MUST NOT automatically restart sidecar admission.

#### Scenario: Successful drain produces auditable cutover evidence
- **WHEN** all drain-barrier conditions are proven satisfied
- **THEN** the system SHALL persist an immutable or append-only sidecar retirement record before V2 live enablement
- **AND** the record SHALL identify the reconciled owners, protection result, PnL result, and cutover time

#### Scenario: V2 rollback does not reactivate sidecar
- **WHEN** Tactical V2 new admission is disabled after cutover
- **THEN** filled V2 positions SHALL remain under their verified protection and V2 exit controller until flat
- **AND** legacy sidecar admission SHALL remain disabled unless a separate explicit rollout action enables it
