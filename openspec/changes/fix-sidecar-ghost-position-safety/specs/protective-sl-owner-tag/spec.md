## ADDED Requirements

### Requirement: Main migration SHALL preserve protection on sidecar-owned present exposure
Main OKX algo migration SHALL preserve pending TP/SL protection for symbols that are currently sidecar-owned and have present or unknown exchange exposure, even when Main has no local position for that symbol. Manual or ambiguous OCO/conditional algos SHALL NOT be canceled as orphan residuals in this state.

#### Scenario: Manual OCO survives sidecar-owned migration
- **WHEN** Main OKX algo migration scans a symbol with no local Main position
- **AND** the sidecar owner registry has an open owner row matching that symbol and side
- **AND** exchange position state for that symbol is present or unknown
- **AND** a pending manual OCO algo exists without a sidecar owner tag
- **THEN** Main SHALL preserve the algo
- **AND** it SHALL record the preservation or ambiguity in the migration summary

#### Scenario: Manual conditional SL survives sidecar-owned migration
- **WHEN** Main OKX algo migration scans a sidecar-owned symbol with present or unknown exchange exposure
- **AND** a pending conditional SL algo exists without a recognized Main owner tag
- **THEN** Main SHALL preserve the algo
- **AND** it SHALL NOT count the algo as an orphan SL cancellation

#### Scenario: Exchange-flat orphan cleanup is not weakened
- **WHEN** Main OKX algo migration scans a symbol with no local Main position
- **AND** there is no active sidecar owner row for the symbol or exchange state is confirmed flat
- **THEN** existing orphan cleanup behavior MAY still cancel residual Main-owned or unowned algos according to the migration policy
