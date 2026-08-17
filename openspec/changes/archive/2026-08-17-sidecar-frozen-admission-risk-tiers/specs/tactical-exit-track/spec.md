## MODIFIED Requirements

### Requirement: Live sidecar admission SHALL enforce Tactical hard vetoes
The live sidecar admission path SHALL enforce Tactical hard vetoes that protect against strategy drift, stale decisions, same-symbol stacking, and unbounded duplicate exposure. A Tactical Shadow event SHALL create live Sidecar exposure only when it carries a supported, fresh, internally consistent frozen admission decision produced by Judge. The Sidecar SHALL NOT recompute indicators or strategy gates. A Tactical Shadow event that would create inseparable same-symbol exposure in the live Sidecar SHALL be rejected before order submission and recorded with attribution.

#### Scenario: Frozen policy rejection blocks live admission
- **WHEN** a Tactical Shadow event is stamped ineligible, stale, malformed, unsupported, or inconsistent with its canonical policy evidence
- **THEN** live Sidecar admission SHALL reject the event before capacity or exchange calls
- **AND** the rejection SHALL preserve the frozen policy version, tier, evidence, and specific failure reason

#### Scenario: Existing sidecar owner blocks duplicate live admission
- **WHEN** a Tactical Shadow event targets a symbol and side with an already open sidecar owner row
- **THEN** live Sidecar admission SHALL reject the event before order submission
- **AND** the rejection SHALL preserve attribution identifying same-symbol sidecar activity

#### Scenario: Existing account exposure blocks sidecar admission
- **WHEN** a Tactical Shadow event targets a symbol that already has Main, manual, unknown, or otherwise non-sidecar account exposure
- **THEN** live Sidecar admission SHALL reject the event with same-symbol exposure attribution
- **AND** it SHALL NOT convert the candidate into a sidecar add-to-position action

#### Scenario: Verified policy pass retains execution safety gates
- **WHEN** a fresh eligible frozen decision passes policy verification
- **THEN** Sidecar SHALL still enforce active capacity, account exposure, symbol halt, balance, entry drift, slippage, order capability, geometry, and attached protective-stop verification
- **AND** no policy field SHALL bypass those safety checks
