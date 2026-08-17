## 1. Frozen Policy Contract

- [x] 1.1 Add failing unit tests for clean full-tier, warning reduced-tier, exhaustion rejection, gate rejection, malformed evidence, and unsupported versions.
- [x] 1.2 Implement the pure versioned Sidecar admission classifier and canonical policy evidence model.
- [x] 1.3 Propagate explicit Tactical quality flags into Judge plans and freeze the Sidecar policy before ledger persistence.
- [x] 1.4 Persist and map every policy stamp/evidence field without removing ineligible rows from counterfactual tracking.

## 2. Sidecar Verification And Sizing

- [x] 2.1 Add failing Sidecar tests for policy integrity, five-second freshness, historical unstamped rows, and no exchange calls on policy rejection.
- [x] 2.2 Enforce policy verification and freshness before dry-run, capacity, account exposure, and executor calls.
- [x] 2.3 Apply full/reduced sizing from the verified risk tier and persist tier, requested size, policy version, and rejection evidence in audits.
- [x] 2.4 Enforce a startup capacity range of one through three and retain existing same-symbol/exchange fail-closed guards.

## 3. Dedicated Sidecar Risk Ceiling

- [x] 3.1 Add failing tests proving a Sidecar 100U override is honored while an executor without the override keeps Main's configured limit.
- [x] 3.2 Add a validated optional `ContractExecutor` maximum-trade-amount override and pass the Sidecar base size from `_build_executor()`.
- [x] 3.3 Verify 100U full-tier and 50U reduced-tier plans persist the intended margin amount after all existing execution checks.

## 4. Replay And Verification

- [x] 4.1 Create a sealed local fixture from the read-only 53-row audit cohort with only policy and audited PnL fields.
- [x] 4.2 Add deterministic replay tests asserting nine eligible rows, stable tier identities/reasons, approved tiered PnL, and repeated-loop stability.
- [x] 4.3 Run focused Shadow/Sidecar/Judge/executor tests and the relevant repository regression suite.
- [x] 4.4 Update operator documentation with the frozen-policy contract, `100U x 3` Sidecar command, fail-closed migration, and no-live-claim replay boundary.
- [x] 4.5 Collect current cloud owner, position, protection, process, and admission facts before any deployment or restart decision.
