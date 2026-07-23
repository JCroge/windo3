## ADDED Requirements

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
