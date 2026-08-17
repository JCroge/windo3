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
