# Comet Design Handoff

- Change: sidecar-frozen-admission-risk-tiers
- Phase: design
- Mode: full
- Context hash: e954b222a2c80a9d9fa6590eeec294361554b3c2371b66152ec2e9cb53bf4423

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/sidecar-frozen-admission-risk-tiers/proposal.md

- Source: openspec/changes/sidecar-frozen-admission-risk-tiers/proposal.md
- Lines: 1-31
- SHA256: 94b8f203621b50760d485ebaf49f27233dc4852a9883702842011dd7bc09b15c

```md
## Why

The live Sidecar currently treats every broad Tactical Shadow ledger row as executable and then relies on process-local recomputation and Main's global `MAX_TRADE_AMOUNT` cap. This allows Shadow and Sidecar admission to drift, admits known exhaustion warnings, and silently caps an explicit 100U Sidecar command to Main's 30U limit.

## What Changes

- Freeze the Sidecar admission decision when Judge records the Shadow Tactical row, while continuing to record every row for counterfactual analysis.
- Stamp each row with an eligibility decision, policy version, risk tier, rejection reason, decision timestamp, and the quality evidence used by that policy.
- Make Sidecar validate and execute the frozen decision without fetching indicators or recomputing strategy logic.
- Reject gate failures, trend-exhaustion warnings, stale decisions older than five seconds, malformed stamps, and policy/raw-evidence mismatches.
- Size eligible clean signals at 100U and eligible weak-volume/OI or weak-provenance signals at 50U, with at most three active positions and the existing 0.5 percent entry-drift boundary.
- Add an explicit Sidecar-only executor risk override so the requested 100U/50U tiers are not clamped by Main's global 30U cap.
- Add deterministic replay coverage for the sealed 53-trade audit cohort and require the approved nine-trade eligibility/tier projection to remain stable.
- Keep Main sizing, Shadow counterfactual coverage, protection ownership, same-symbol exposure guards, and exchange fail-closed behavior unchanged.

## Capabilities

### New Capabilities

- `shadow-sidecar-frozen-admission`: Defines the versioned Shadow decision stamp, Sidecar verification contract, tiered sizing, freshness, and deterministic replay acceptance criteria.

### Modified Capabilities

- `tactical-exit-track`: Tightens live Sidecar admission so only a verified frozen Shadow decision can create exposure while existing owner-bound exit behavior remains unchanged.

## Impact

- Affected code: `agents/trading/judge.py`, `utils/counterfactual_ledger.py`, `utils/shadow_tactical_live.py`, `scripts/shadow_tactical_live_sidecar.py`, `executor.py`, and focused replay/unit tests.
- Affected persisted data: new fields on future `rejected_plan_created` records and richer Sidecar audit events; historical rows without a policy stamp remain readable but are ineligible for live admission.
- Affected operations: Sidecar must run with `--size-usdt 100 --max-active 3`; Main `.env` and Main process risk limits remain unchanged.
- No exchange API, database schema, dependency, Main live sizing, or legacy Sidecar exit semantics change.
```

## openspec/changes/sidecar-frozen-admission-risk-tiers/design.md

- Source: openspec/changes/sidecar-frozen-admission-risk-tiers/design.md
- Lines: 1-72
- SHA256: 1456f6e2d8b1d053f99799b08456db2826ba9722fbf13e7a1b3e437717f9feed

```md
## Context

Shadow Tactical rows are created by Judge and persisted through `CounterfactualLedger`, while the legacy Sidecar tails the resulting JSONL and turns broad Tactical rows into exchange orders. The current consumer checks shape, capacity, same-symbol exposure, entry drift, and exchange protection, but it does not enforce the strategy gate or quality warnings that existed when Judge created the row. It also accepts `--size-usdt 100` while `ContractExecutor.open_sidecar_plan()` clamps that request to the executor's Main-derived `RiskManager.max_trade_amount`, which is currently 30U in production.

The audited 53-trade cohort showed that `tactical_track_gate=pass` with no `trend_exhaustion_warning` yields nine eligible trades. At 100U for clean rows and 50U for rows marked `weak_volume_oi` or `weak_provenance`, the replay produced `+9.09U` net with `6.52U` maximum drawdown, versus `+4.47U` and `13.05U` maximum drawdown when every eligible row used 100U. Event-to-open latency was 1.79 seconds median, 2.53 seconds maximum, and fill drift remained within the existing 0.5 percent acceptance boundary.

Constraints include preserving every Shadow row for counterfactual research, keeping Sidecar latency under the five-second freshness boundary, not changing Main's risk configuration, retaining owner/protection/same-symbol fail-closed behavior, and not treating local replay as exchange-fill or live-PnL proof.

## Goals / Non-Goals

**Goals:**

- Make Judge the single strategy decision owner for Sidecar admission.
- Persist a versioned, auditable admission stamp on every future Tactical Shadow row.
- Make Sidecar execute only valid, fresh, eligible stamps at the frozen full/reduced risk tier.
- Give the Sidecar executor a dedicated 100U risk ceiling without changing Main.
- Lock the approved 53-row replay projection and policy integrity behavior into deterministic tests.

**Non-Goals:**

- Recomputing indicators, LLM warnings, Tactical economics, or strategy gates in Sidecar.
- Removing rejected Shadow rows or changing their counterfactual settlement.
- Changing Tactical exit mathematics, leverage, TP/SL ownership, entry drift, or same-symbol exposure handling.
- Enabling admission, deploying, or restarting cloud processes before local verification and owner-state checks.
- Claiming that counterfactual PnL is realized exchange PnL.

## Decisions

1. **Use a pure versioned policy classifier at the Judge boundary.** Judge will pass explicit Tactical quality flags into the profiled plan and derive a `sidecar_live_eligible`, `sidecar_policy_version`, `sidecar_risk_tier`, `sidecar_rejection_reason`, and `sidecar_decided_at` stamp before the ledger append. The ledger only persists those fields. This keeps strategy ownership in Judge instead of turning the observability ledger or Sidecar into a second strategy engine.

2. **Persist canonical policy evidence and verify it in Sidecar.** The stamp includes canonical booleans for the Tactical gate, trend exhaustion, weak volume/OI, and weak provenance. Sidecar re-runs only the pure policy classifier over these persisted booleans and compares the result with the stamp. It does not fetch market data or provenance inputs. Missing fields, unsupported versions, or any mismatch reject fail-closed with a specific audit reason.

3. **Apply the approved two-tier sizing after verification.** A valid eligible `full` row requests the configured base size of 100U; a valid eligible `reduced` row requests 50 percent of that base. `trend_exhaustion_warning` and gate failure always reject. Reduced tier is selected when `weak_volume_oi` or `weak_provenance` is frozen true; other non-exhaustion diagnostic labels do not independently alter the tier in this policy version.

4. **Keep freshness and execution safety separate.** Sidecar rejects a policy decision older than five seconds before capacity or exchange calls. A fresh eligible row must still pass the existing maximum-three-active, same-symbol account exposure, symbol halt, balance, 0.5 percent entry drift, slippage, order capability, geometry, and attached-SL verification checks. A policy pass is not a fill guarantee.

5. **Use an explicit constructor override for Sidecar risk only.** `ContractExecutor` gains an optional `max_trade_amount_override` validated against existing hard limits. `_build_executor()` passes the Sidecar base size as this override. Main call sites omit it and retain `load_config()` values. This is preferred over mutating `.env` because Main and Sidecar share the host and because command-line sizing alone is currently clamped.

6. **Make three positions a hard Sidecar ceiling.** Runtime values below three remain valid for cautious operation; values above three fail startup rather than silently expanding risk. The production command remains `--size-usdt 100 --max-active 3`.

7. **Replay policy, not future exchange behavior.** A sealed local fixture derived read-only from the 53-row cloud cohort will contain only fields needed to classify the row and its audited resolved PnL. The replay asserts nine eligible rows, stable full/reduced assignments, and the approved 100U/50U arithmetic over repeated loops. Live latency, drift, protection, and realized PnL remain separate rollout evidence.

**Alternatives considered:**

- Recompute the strategy in Sidecar: rejected because duplicated indicator/LLM state introduces timing drift and recreates the parity failure.
- Filter only in Sidecar from `tactical_source`: rejected because string parsing becomes a second undocumented strategy contract and cannot prove producer/consumer agreement.
- Execute directly from Tactical V2 candidates: rejected for this change because it would replace the Sidecar lifecycle and ownership path rather than repair the approved Shadow Tactical strategy application.

## Risks / Trade-offs

- **[Risk] Five-second TTL rejects valid signals during host stalls.** Mitigation: audit `sidecar_policy_stale` separately and preserve the current two-second poll; do not extend TTL without latency evidence.
- **[Risk] Historical unstamped rows can no longer be backfilled live.** Mitigation: keep them readable for counterfactual analysis but fail closed for admission; start from new events after deployment.
- **[Risk] Producer and consumer code deploy at different revisions.** Mitigation: unsupported/missing policy versions reject and audit instead of falling back to broad Tactical detection.
- **[Risk] 100U raises order and drawdown exposure.** Mitigation: three-position ceiling, 50U warning tier, existing balance/daily-loss/drawdown checks, same-symbol guard, drift gate, and mandatory attached-SL verification remain authoritative.
- **[Risk] The 53-row result is in-sample.** Mitigation: use it as a deterministic correctness fixture, not a profitability guarantee; require live observation for realized performance.
- **[Risk] `weak_provenance` is confused with Sidecar reading observability metadata.** Mitigation: Sidecar consumes only the frozen Judge policy boolean and never reads or derives raw provenance confidence.

## Migration Plan

1. Add failing policy, ledger-stamp, Sidecar admission, risk-override, and replay tests.
2. Implement the pure classifier and stamp future Tactical Shadow plans at the Judge boundary.
3. Persist and map all stamp/evidence fields through the ledger and Sidecar plan.
4. Enforce version, integrity, TTL, tier sizing, and the three-position ceiling before exchange calls.
5. Add the dedicated executor risk override and prove Main defaults remain unchanged.
6. Run focused tests, the sealed replay repeatedly, and the relevant repository regression suite.
7. Before cloud deployment, collect read-only process, owner, position, protection, and admission state. Do not restart while an active owner cannot be recovered safely.
8. Deploy code without changing Main `.env`; restart Sidecar only through a controlled stop/start after owner recovery proof, then verify startup risk values and audit events.
9. Roll back by disabling Sidecar admission first, draining/protecting proven Sidecar exposure, and restoring the prior code revision. Historical stamped rows remain backward-compatible data.

## Open Questions

- None for implementation. Live enablement and restart remain separate operational gates based on current owner/protection truth.
```

## openspec/changes/sidecar-frozen-admission-risk-tiers/tasks.md

- Source: openspec/changes/sidecar-frozen-admission-risk-tiers/tasks.md
- Lines: 1-27
- SHA256: a0e9bba52fc647f980c32b174478df1e48271ad05666eda91b53d07b88fe2cbe

```md
## 1. Frozen Policy Contract

- [ ] 1.1 Add failing unit tests for clean full-tier, warning reduced-tier, exhaustion rejection, gate rejection, malformed evidence, and unsupported versions.
- [ ] 1.2 Implement the pure versioned Sidecar admission classifier and canonical policy evidence model.
- [ ] 1.3 Propagate explicit Tactical quality flags into Judge plans and freeze the Sidecar policy before ledger persistence.
- [ ] 1.4 Persist and map every policy stamp/evidence field without removing ineligible rows from counterfactual tracking.

## 2. Sidecar Verification And Sizing

- [ ] 2.1 Add failing Sidecar tests for policy integrity, five-second freshness, historical unstamped rows, and no exchange calls on policy rejection.
- [ ] 2.2 Enforce policy verification and freshness before dry-run, capacity, account exposure, and executor calls.
- [ ] 2.3 Apply full/reduced sizing from the verified risk tier and persist tier, requested size, policy version, and rejection evidence in audits.
- [ ] 2.4 Enforce a startup capacity range of one through three and retain existing same-symbol/exchange fail-closed guards.

## 3. Dedicated Sidecar Risk Ceiling

- [ ] 3.1 Add failing tests proving a Sidecar 100U override is honored while an executor without the override keeps Main's configured limit.
- [ ] 3.2 Add a validated optional `ContractExecutor` maximum-trade-amount override and pass the Sidecar base size from `_build_executor()`.
- [ ] 3.3 Verify 100U full-tier and 50U reduced-tier plans persist the intended margin amount after all existing execution checks.

## 4. Replay And Verification

- [ ] 4.1 Create a sealed local fixture from the read-only 53-row audit cohort with only policy and audited PnL fields.
- [ ] 4.2 Add deterministic replay tests asserting nine eligible rows, stable tier identities/reasons, approved tiered PnL, and repeated-loop stability.
- [ ] 4.3 Run focused Shadow/Sidecar/Judge/executor tests and the relevant repository regression suite.
- [ ] 4.4 Update operator documentation with the frozen-policy contract, `100U x 3` Sidecar command, fail-closed migration, and no-live-claim replay boundary.
- [ ] 4.5 Collect current cloud owner, position, protection, process, and admission facts before any deployment or restart decision.
```

## openspec/changes/sidecar-frozen-admission-risk-tiers/specs/shadow-sidecar-frozen-admission/spec.md

- Source: openspec/changes/sidecar-frozen-admission-risk-tiers/specs/shadow-sidecar-frozen-admission/spec.md
- Lines: 1-95
- SHA256: 1086e8944258be27147bf10177390687c632d58cc8efc504602956cb3f7c7537

```md
## ADDED Requirements

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
```

## openspec/changes/sidecar-frozen-admission-risk-tiers/specs/tactical-exit-track/spec.md

- Source: openspec/changes/sidecar-frozen-admission-risk-tiers/specs/tactical-exit-track/spec.md
- Lines: 1-24
- SHA256: 3b44f42f8348e4b91c87b09ef6912cfaedb724075ed50ebaa6304bd4c8df20b3

```md
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
```

