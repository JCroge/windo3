## ADDED Requirements

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
The sidecar SHALL only act on positions that it can prove are sidecar-owned. If a registry row cannot be matched to a live sidecar-owned position, the sidecar SHALL skip the exit action and record the skip. Sidecar exit actions SHALL not mutate main-process positions or any other non-sidecar state.

#### Scenario: Unproven position is skipped
- **WHEN** an owner record exists but the live position cannot be proven against the current sidecar-owned position
- **THEN** the sidecar SHALL skip the exit action
- **AND** it SHALL record a skip audit event

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
