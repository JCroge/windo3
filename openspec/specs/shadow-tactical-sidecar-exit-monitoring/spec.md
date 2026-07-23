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
For each proven open sidecar-owned Tactical position, the sidecar SHALL apply the same Tactical exit semantics as the Tactical exit profile. TP1 SHALL reduce the position by 50 percent. TP2 SHALL reduce the remaining position by 25 percent. Positions marked invalidated, weakened without progress, or timed out by max hold SHALL close the remaining position. Protective stop handling SHALL remain authoritative and the sidecar SHALL use the shared reduce/close lifecycle for all exit actions.

#### Scenario: TP1 triggers partial reduce
- **WHEN** a proven open sidecar-owned Tactical position reaches TP1
- **THEN** the sidecar SHALL trigger a 50 percent reduce action
- **AND** it SHALL preserve the remaining position with updated protection state

#### Scenario: Invalidated thesis exits fast
- **WHEN** a proven open sidecar-owned Tactical position is marked invalidated
- **THEN** the sidecar SHALL request an immediate close
- **AND** it SHALL record the invalidation reason in the audit trail

#### Scenario: Max hold closes the remainder
- **WHEN** a proven open sidecar-owned Tactical position reaches its max-hold window
- **THEN** the sidecar SHALL close the remaining position
- **AND** it SHALL record `tactical_max_hold` as the close reason

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
