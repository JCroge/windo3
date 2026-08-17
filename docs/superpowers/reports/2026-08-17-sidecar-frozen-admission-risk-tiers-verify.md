# Verification Report: sidecar-frozen-admission-risk-tiers

Date: 2026-08-17
Branch: `change/sidecar-frozen-admission-risk-tiers`
Change: `sidecar-frozen-admission-risk-tiers`
Workflow: full
Verify mode: full

## Final Assessment

PASS for the scoped local implementation and deterministic replay contract.
Judge freezes the Sidecar admission decision on Tactical Shadow rows, the ledger
persists the frozen stamp and canonical evidence without removing ineligible rows
from counterfactual tracking, and Sidecar verifies the frozen policy before dry
run, capacity, exchange exposure, or executor work. Eligible `full` rows use the
configured full-tier base size and eligible `reduced` rows use half that base.
The Sidecar executor receives a process-local 100U risk ceiling override while
Main executor construction keeps the configured Main limit.

This verification is local. It does not authorize cloud deployment, process
restart, admission restoration, or a live PnL claim. The sealed replay proves
policy classification and counterfactual tier arithmetic only.

Branch handling and the final Comet verify guard are still pending the required
Comet finishing-branch user decision.

## Scorecard

| Dimension | Status | Evidence |
| --- | --- | --- |
| Completeness | PASS | OpenSpec reports 16/16 tasks complete; both delta capabilities are present and `openspec validate sidecar-frozen-admission-risk-tiers --strict` passes |
| Correctness | PASS | Fresh focused verification: `191 passed` for Sidecar/Judge/executor/replay and `33 passed` for neighboring Tactical V2 isolation tests |
| Coherence | PASS | Proposal, OpenSpec design, technical Design Doc, and delta specs agree on Judge-owned strategy admission, Sidecar-owned verification/execution safety, 100U/50U tiers, max-active 1..3, and no-live-claim replay boundary |

## Requirement Mapping

| Requirement | Implementation Evidence | Test / Verification Evidence |
| --- | --- | --- |
| Judge freezes every Tactical Shadow row with version, eligibility, tier, rejection, timestamp, and canonical evidence | `agents/trading/judge.py`, `utils/shadow_sidecar_policy.py`, `utils/counterfactual_ledger.py` | `tests/test_shadow_sidecar_policy.py`, `tests/test_shadow_sidecar_policy_judge.py`, `test_tactical_track_classifier.py` |
| Ineligible rows remain available for counterfactual tracking | `CounterfactualLedger.record_rejection()` persists policy fields while preserving `_active` tracking | Judge/ledger tests assert stamped ineligible records remain recorded |
| Sidecar verifies frozen policy without strategy recomputation | `scripts/shadow_tactical_live_sidecar.py` calls `verify_sidecar_policy()` immediately after admission-enabled/duplicate checks; `utils/shadow_sidecar_policy.py` verifies only persisted canonical evidence | CLI tests assert gate failure, exhaustion, missing stamp, unsupported version, mismatch, and stale decisions reject before exchange or executor calls |
| Five-second TTL is fail-closed | `SIDECAR_POLICY_MAX_AGE_SECONDS = 5.0`; stale verification returns `sidecar_policy_stale` | Policy tests cover exact TTL boundary, stale age, invalid/future timestamps; CLI tests assert stale rejection before exchange work |
| Full and reduced tiers request 100U/50U from a 100U base | `_policy_tier_size_usdt()` feeds `executor.open_sidecar_plan(..., size_usdt=requested_size_usdt)` | CLI tests assert full size `100.0`, reduced size `50.0`, and audit persistence of `requested_size_usdt` |
| Sidecar has a dedicated bounded executor risk ceiling and Main remains unchanged | `ContractExecutor(max_trade_amount_override=...)`; `_build_executor()` passes the resolved Sidecar base size | Executor tests assert 100U override is honored, no-override Main limit remains 30U, and invalid overrides fail startup |
| Sidecar active capacity is bounded to 1..3 | `resolve_sidecar_max_active()` accepts only 1 through 3 before state/executor creation | CLI tests cover accepted and rejected startup values, plus active-cap rejection |
| Deterministic replay locks the 53-row audited cohort projection | `tests/fixtures/shadow_sidecar_policy_53_trade_window.json`, `tests/test_shadow_sidecar_policy_replay.py` | Replay tests assert 53 rows, 9 eligible, stable tiers/reasons, all-100U net `4.47024185`, tiered net `9.086859325`, and 100-loop stability |
| Existing Sidecar safety gates remain authoritative after policy pass | `_process_event()` still enforces max-active, same-symbol exposure, account exposure, drift/executor/protection outcomes after verification | CLI/executor/exit-monitoring tests cover pre-exchange rejection, same-symbol/exposure behavior, drift audit preservation, and owner isolation |
| No live deployment or restart is performed by this change | Docs preserve rollout boundary and cloud facts; no production-write scripts were run | Read-only cloud facts were documented separately; local tests and replay use fixture data only |

## Verification Commands

| Check | Result |
| --- | --- |
| Comet verify entry | `bash "$COMET_STATE" check sidecar-frozen-admission-risk-tiers verify` -> PASS |
| OpenSpec strict validation | `openspec validate sidecar-frozen-admission-risk-tiers --strict` -> `Change 'sidecar-frozen-admission-risk-tiers' is valid` |
| Build guard | `bash "$COMET_GUARD" sidecar-frozen-admission-risk-tiers build --apply` -> build metadata checks PASS, configured `python3.12 -m pytest -q` PASS, transitioned to `phase: verify` |
| Focused Sidecar/Judge/executor/replay set | `python3.12 -m pytest -q tests/test_shadow_sidecar_policy.py tests/test_shadow_sidecar_policy_judge.py tests/test_shadow_sidecar_policy_replay.py test_tactical_track_classifier.py tests/test_judge_decision_tape_wiring.py tests/test_shadow_tactical_live_core.py tests/test_shadow_tactical_live_cli.py tests/test_shadow_tactical_live_executor.py tests/test_shadow_tactical_exit_monitoring.py` -> `191 passed in 10.29s` |
| Neighboring Tactical V2 isolation set | `python3.12 -m pytest -q tests/test_tactical_v2_candidate_bus.py tests/test_tactical_v2_shadow.py tests/test_tactical_v2_main_isolation.py tests/test_shadow_tactical_owner_isolation.py` -> `33 passed in 2.28s` |
| Full repository regression | `python3.12 -m pytest -q` was executed by the build guard through `build_command`; earlier manual full-suite evidence for this build recorded `2267 passed, 5 skipped, 4 deselected` |
| Credential/path scan | Added diff contains env variable names and test dummy values only; no actual secret-like value or credential file was added |

## Issues

- CRITICAL: none found in local verification.
- WARNING: branch handling is pending. Comet verify cannot transition to archive until the required finishing-branch option is chosen and `branch_status: handled` is recorded.
- WARNING: independent code review is running and should be incorporated before final archive if it reports material findings.
- WARNING: the cloud snapshot collected during build showed the resident Sidecar command was still `--max-active 5`; that is not this change's contract and must be handled as a separate rollout decision. No cloud restart or deployment was performed here.

## Operational Boundary

The approved production command remains:

```bash
scripts/shadow_tactical_live_sidecar.py run --poll-seconds 2 --size-usdt 100 --max-active 3
```

Historical unstamped rows, malformed stamps, unsupported versions, stale
decisions, and policy/evidence mismatches fail closed for live Sidecar admission.
Main `.env`, Main risk limits, Main process ownership, and live deployment state
are outside this local verification.
