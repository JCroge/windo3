## ADDED Requirements

### Requirement: Live sidecar admission SHALL enforce Tactical hard vetoes
The live sidecar admission path SHALL enforce Tactical hard vetoes that protect against same-symbol stacking and unbounded duplicate exposure. A Tactical shadow event that would create inseparable same-symbol exposure in the live sidecar SHALL be rejected before order submission and recorded with attribution.

#### Scenario: Sidecar active owner is a hard veto
- **WHEN** a Tactical shadow event targets a symbol and side with an already open sidecar owner row
- **THEN** live sidecar admission SHALL reject the event before order submission
- **AND** the rejection SHALL preserve attribution identifying same-symbol sidecar activity

#### Scenario: Main or unknown same-symbol exposure remains blocked
- **WHEN** a Tactical shadow event targets a symbol that already has Main, manual, unknown, or otherwise non-sidecar account exposure
- **THEN** live sidecar admission SHALL reject the event with same-symbol exposure attribution
- **AND** it SHALL NOT convert the candidate into a sidecar add-to-position action
