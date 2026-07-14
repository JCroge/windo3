## ADDED Requirements

### Requirement: OKX attached SL resolution SHALL use bounded verification before terminal protection halt

When an OKX open order is submitted with an attached protective stop loss and the first lookup of the attached SL `algoId` by client order id fails, the executor SHALL enter a bounded protection-verification state before treating the position as terminally unprotected. During this bounded state the system MUST NOT open additional live risk. If verification finds the attached SL, the position SHALL be marked `protection_state="protected"` and no protection halt SHALL be triggered. If verification is exhausted without finding a valid protective SL, the executor SHALL retain the existing fail-closed behavior and trigger a protection halt.

#### Scenario: attached SL appears during bounded verification
- **WHEN** an OKX open fills and the first attached SL lookup by `attachAlgoClOrdId` returns no `algoId`
- **AND** a later bounded verification attempt finds an owner-matched protective SL for the position
- **THEN** the position MUST be saved with `protection_state="protected"`
- **AND** the system MUST NOT write global halt reason `okx_sl_algo_unresolved:<symbol>`

#### Scenario: attached SL remains missing after bounded verification
- **WHEN** an OKX open fills and all bounded verification attempts fail to find a valid protective SL
- **THEN** the position MUST be saved with `protection_state="unknown"`
- **AND** the executor MUST trigger the existing fail-closed protection halt for that symbol
- **AND** new live opens MUST remain blocked until the halt is resolved

### Requirement: protection-driven global halt SHALL self-heal after protection risk is proven gone

For allowlisted protection halt reasons caused by missing or unresolved protective stop loss, the system SHALL automatically clear the matching per-symbol halt and global halt only after exchange/local state proves that the protection risk is gone. The allowlist SHALL include `okx_sl_algo_unresolved:<symbol>` and MAY include existing migrate-missing-SL reasons that already have symbol-level self-heal semantics. Manual halts, daily hard stops, reconciliation mismatches, and unknown halt reasons MUST NOT auto-clear through this path.

#### Scenario: halted symbol is closed on exchange
- **WHEN** global halt reason is `okx_sl_algo_unresolved:WLD-USDT-SWAP`
- **AND** sync/reconciliation confirms WLD is no longer open on exchange
- **AND** local state has no WLD position with `protection_state` of `unknown` or `pending`
- **THEN** the WLD per-symbol halt MUST be cleared
- **AND** the global halt MUST be cleared if no other unresolved protection halt remains
- **AND** an audit log MUST record the automatic protection halt recovery

#### Scenario: halted symbol becomes protected
- **WHEN** global halt reason is `okx_sl_algo_unresolved:<symbol>`
- **AND** sync/reconciliation later finds a valid owner-matched protective SL for that symbol
- **THEN** the position MUST become `protection_state="protected"`
- **AND** the matching protection halt MAY be auto-cleared if no other unresolved protection halt remains

#### Scenario: non-protection halt remains sticky
- **WHEN** global halt reason is manual, daily hard-stop, reconciliation mismatch, or an unknown non-allowlisted reason
- **AND** positions are flat or protected
- **THEN** the system MUST NOT auto-clear the global halt
- **AND** recovery MUST still require the existing `/resume` or `/force_resume` path as appropriate
