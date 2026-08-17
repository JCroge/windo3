## Purpose

Define Judge-owned frozen Sidecar admission decisions, Sidecar policy verification, freshness, tiered sizing, executor risk override, active-cap enforcement, and deterministic replay requirements for Tactical Shadow Sidecar live admission.

## Requirements

### Requirement: Judge SHALL freeze Sidecar admission on every Tactical Shadow row
Before a Tactical Shadow row is appended, Judge SHALL derive and attach a versioned Sidecar admission stamp from explicit Tactical policy evidence. The stamp SHALL include `sidecar_live_eligible`, `sidecar_policy_version`, `sidecar_risk_tier`, `sidecar_rejection_reason`, `sidecar_decided_at`, and canonical evidence for Tactical track gate, trend exhaustion, weak volume/OI, and weak provenance. Recording an ineligible row SHALL NOT remove it from counterfactual tracking.

#### Scenario: Clean gate-pass row receives full tier
- **WHEN** Judge records a Tactical Shadow row whose Tactical track gate passes and whose trend-exhaustion, weak-volume/OI, and weak-provenance evidence are all false
- **THEN** the row SHALL be stamped eligible with risk tier `full`
- **AND** it SHALL remain available to the counterfactual ledger

#### Scenario: Warning row receives reduced tier
- **WHEN** Judge records a Tactical Shadow row whose Tactical track gate passes, trend exhaustion is false, and weak volume/OI or weak provenance is true
- **THEN** the row SHALL be stamped eligible with risk tier `reduced`

#### Scenario: Exhausted or gate-failed row remains research-only
- **WHEN** the Tactical track gate fails or trend exhaustion is true
- **THEN** the row SHALL be stamped ineligible with a stable rejection reason
- **AND** the row SHALL still be appended for counterfactual analysis

### Requirement: Sidecar SHALL verify the frozen policy without recomputing strategy
Sidecar SHALL accept admission input only when the policy version is supported, all required stamp and evidence fields are present, and re-deriving the versioned policy from persisted canonical evidence exactly matches the frozen eligibility, tier, and rejection reason. Sidecar MUST NOT fetch indicators, call an LLM, derive provenance confidence, or recompute Tactical economics for admission.

#### Scenario: Valid frozen decision proceeds to execution safety checks
- **WHEN** a supported, internally consistent, eligible policy stamp is consumed
- **THEN** Sidecar SHALL proceed to freshness, capacity, exposure, drift, balance, exchange, and protection checks
- **AND** policy verification SHALL NOT itself claim an exchange fill

#### Scenario: Missing or mismatched stamp fails closed
- **WHEN** a Tactical Shadow row lacks a required policy field or its frozen outcome disagrees with its canonical evidence
- **THEN** Sidecar SHALL reject it before any exchange call
- **AND** it SHALL record a policy-integrity audit reason

#### Scenario: Unsupported policy version fails closed
- **WHEN** a Tactical Shadow row carries an unknown `sidecar_policy_version`
- **THEN** Sidecar SHALL reject it before any exchange call
- **AND** it SHALL retain the row for non-live historical analysis

### Requirement: Sidecar SHALL enforce decision freshness
An otherwise eligible Sidecar decision SHALL be rejected when more than five seconds have elapsed between `sidecar_decided_at` and Sidecar evaluation. Missing, non-finite, future-skewed beyond the accepted clock tolerance, or malformed timestamps SHALL fail closed.

#### Scenario: Fresh decision is evaluated
- **WHEN** a valid eligible stamp is no more than five seconds old
- **THEN** Sidecar SHALL continue to execution safety checks

#### Scenario: Stale decision is rejected
- **WHEN** a valid eligible stamp is more than five seconds old
- **THEN** Sidecar SHALL reject it before capacity and exchange calls
- **AND** it SHALL record `sidecar_policy_stale` with the measured age

### Requirement: Eligible Sidecar rows SHALL use the frozen risk tier
For a production base size of 100U, Sidecar SHALL request 100U for risk tier `full` and 50U for risk tier `reduced`. It SHALL reject unknown tiers and SHALL persist the tier and requested size in the open or rejection audit trail.

#### Scenario: Full tier requests 100U
- **WHEN** a valid fresh eligible row has risk tier `full` and the configured Sidecar base size is 100U
- **THEN** Sidecar SHALL request 100U from the executor

#### Scenario: Reduced tier requests 50U
- **WHEN** a valid fresh eligible row has risk tier `reduced` and the configured Sidecar base size is 100U
- **THEN** Sidecar SHALL request 50U from the executor

### Requirement: Sidecar SHALL have a dedicated bounded executor risk ceiling
The Sidecar executor SHALL support an explicit process-local maximum-trade-amount override validated against existing hard limits. Main executor construction SHALL remain unchanged when no override is supplied. The Sidecar override SHALL be at least the configured full-tier base size so an explicit 100U request is not silently clamped to Main's 30U limit.

#### Scenario: Sidecar 100U request is not capped by Main
- **WHEN** Main configuration has `MAX_TRADE_AMOUNT=30` and Sidecar is constructed with a validated 100U override
- **THEN** a full-tier Sidecar plan SHALL retain a 100U requested and executed margin amount subject to remaining risk and exchange checks
- **AND** Main executors SHALL continue to use 30U

#### Scenario: Invalid risk override refuses startup
- **WHEN** the Sidecar risk override is non-finite, non-positive, or outside existing hard limits
- **THEN** Sidecar construction SHALL fail before order admission

### Requirement: Sidecar active capacity SHALL not exceed three
Sidecar SHALL allow an operational active-position limit from one through three and SHALL refuse startup when configured above three. Existing same-symbol and account-exposure guards SHALL remain authoritative within that capacity.

#### Scenario: Three active positions block the next row
- **WHEN** three Sidecar owner rows are active and another eligible row arrives
- **THEN** Sidecar SHALL reject the row before exchange calls with `sidecar_active_cap`

#### Scenario: Oversized capacity configuration fails closed
- **WHEN** Sidecar is started with `--max-active` greater than three
- **THEN** startup SHALL fail without processing live admission events

### Requirement: Frozen admission replay SHALL be deterministic
A sealed local replay fixture derived from the audited 53-row cohort SHALL test the policy without cloud credentials or exchange I/O. Repeated replay SHALL produce nine eligible rows with stable full/reduced identities and the approved 100U/50U arithmetic.

#### Scenario: Sealed cohort reproduces approved projection
- **WHEN** the sealed 53-row cohort is replayed under policy version one
- **THEN** exactly nine rows SHALL be eligible
- **AND** clean rows SHALL be full tier while weak-volume/OI or weak-provenance rows without trend exhaustion SHALL be reduced tier
- **AND** the tiered replay net PnL SHALL equal the sealed approved result within fixture precision

#### Scenario: Replay is stable across loops
- **WHEN** the same sealed cohort is replayed repeatedly
- **THEN** eligible identities, rejection reasons, risk tiers, and aggregate PnL SHALL be identical in every loop
