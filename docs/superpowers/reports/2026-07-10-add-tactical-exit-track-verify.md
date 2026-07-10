# Verification Report: add-tactical-exit-track

Date: 2026-07-10
Mode: full
Change: add-tactical-exit-track

## Summary

| Dimension | Status |
| --- | --- |
| Completeness | 11/11 tasks complete; 19 requirements reviewed |
| Correctness | 19/19 requirements covered; 46 scenarios reviewed |
| Coherence | Follows proposal, design.md, and Tactical design doc |

Final assessment: PASS. No critical issues found. Ready for branch handling.

## Evidence

Commands run from `.worktrees/add-tactical-exit-track`:

- `pytest test_tactical_track_classifier.py test_tactical_plan_math.py test_tactical_risk_governor.py test_tactical_exit_lifecycle.py test_tactical_metadata_flow.py tests/test_tactical_wld_replay.py -q`
  - Result: 21 passed in 2.59s
- `pytest test_ladder_weighted_rr.py test_low_rr_slots.py test_short_side_guard.py test_ranking_slots.py test_partial_tp_lifecycle.py test_pnl_resolved_event_contract.py test_counterfactual_ledger.py tests/test_counterfactual_pnl.py -q`
  - Result: 118 passed, 3 warnings in 4.12s
- `openspec validate add-tactical-exit-track --strict`
  - Result: Change is valid

## Requirement Mapping

Covered implementation areas:

- Tactical config defaults, hard limits, and env overrides: `utils/config_loader.py:52`, `utils/config_loader.py:188`
- Judge Tactical flags and dedicated slot setup: `agents/trading/judge.py:125`, `agents/trading/judge.py:189`
- Main quality gate, hard vetoes, and classifier: `agents/trading/judge.py:3041`, `agents/trading/judge.py:3081`, `agents/trading/judge.py:3103`
- Tactical plan math, capped stop, TP1, sizing, leverage cap, cost coverage, Tactical R:R and EV: `agents/trading/judge.py:3142`
- Tactical path is classified before final Main R:R/EV gates; Main R:R/EV gates are skipped only for Tactical: `agents/trading/judge.py:1527`, `agents/trading/judge.py:1590`, `agents/trading/judge.py:1606`, `agents/trading/judge.py:1671`
- Tactical ranker slot and Judge slot gate: `utils/candidate_ranker.py:28`, `utils/candidate_ranker.py:125`, `agents/trading/judge.py:1969`
- Tactical risk governor for daily loss, volatility concurrency, loss streak, execution failure, and quality pause: `agents/trading/portfolio_risk_guard.py:44`, `agents/trading/portfolio_risk_guard.py:66`, `agents/trading/portfolio_risk_guard.py:82`, `agents/trading/portfolio_risk_guard.py:95`, `agents/trading/portfolio_risk_guard.py:100`
- Tactical position metadata persisted by executor: `executor.py:2459`
- Tactical TP1, max-hold, invalidated-thesis, and weakened-no-progress lifecycle: `executor.py:1994`
- 15m opposing block marks Tactical thesis invalidated from tech events: `agents/trading/executor.py:158`, `agents/trading/executor.py:175`
- Tactical no-add enforcement: `agents/trading/executor.py:254`
- Tactical metadata propagation through execution result and reviewer metrics: `agents/trading/executor.py:768`, `agents/trading/reviewer.py:202`, `agents/trading/reviewer.py:271`, `agents/trading/reviewer.py:528`
- Counterfactual track/profile metadata and Tactical max-hold replay reason: `utils/counterfactual_ledger.py:57`, `utils/counterfactual_pnl.py:20`, `utils/counterfactual_pnl.py:42`
- Replay config installs Tactical fields for deterministic decision replay: `utils/decision_replay.py:229`

Test coverage highlights:

- Classifier Main/Tactical/shadow/hard-veto: `test_tactical_track_classifier.py:84`, `test_tactical_track_classifier.py:97`, `test_tactical_track_classifier.py:119`
- Tactical profile math and Main ladder R:R isolation: `test_tactical_plan_math.py:4`, `test_tactical_plan_math.py:25`
- Tactical governor breakers: `test_tactical_risk_governor.py:21`, `test_tactical_risk_governor.py:33`, `test_tactical_risk_governor.py:45`
- Tactical TP1/max-hold/SL lifecycle: `test_tactical_exit_lifecycle.py:44`, `test_tactical_exit_lifecycle.py:51`, `test_tactical_exit_lifecycle.py:112`
- Tactical invalidated-thesis and weakened-no-progress exits: `test_tactical_exit_lifecycle.py:61`, `test_tactical_exit_lifecycle.py:72`
- Tactical 15m opposing tech invalidation: `test_tactical_exit_lifecycle.py:87`
- Metadata and counterfactual persistence: `test_tactical_metadata_flow.py:32`, `test_tactical_metadata_flow.py:78`
- WLD-style replay outcomes: `tests/test_tactical_wld_replay.py:24`, `tests/test_tactical_wld_replay.py:49`, `tests/test_tactical_wld_replay.py:74`

## Issues

### CRITICAL

None.

### WARNING

- `datetime.utcnow()` deprecation warning remains in `agents/trading/judge.py:2165`. This is unrelated to Tactical behavior and did not fail tests.

### SUGGESTION

- Keep `tactical_track_enabled=False` and `tactical_shadow_only=True` until replay/live-shadow samples are sufficient to evaluate Tactical win rate, profit factor, TP1 hit rate, and invalidation quality separately from Main.
