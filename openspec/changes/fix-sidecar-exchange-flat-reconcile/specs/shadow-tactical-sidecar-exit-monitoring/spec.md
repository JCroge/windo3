## MODIFIED Requirements

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
