## ADDED Requirements

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

## MODIFIED Requirements

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
