# Verification Report: short-main-path-risk-guard-parity

Date: 2026-06-05
Branch: `short-main-path-risk-guard-parity`
Base ref: `2023e464bbe2da71223b5753336157a4f2fe120b`

## Summary

| Dimension | Status |
|---|---|
| Completeness | 13/13 tasks done; 4/4 spec requirements implemented |
| Correctness | 4/4 requirements covered; 8/8 declared scenarios covered |
| Coherence | Design Doc, OpenSpec design.md, delta spec, and code aligned |

Full pytest baseline: `1008 passed / 4 deselected / 1 warning`. Targeted suite `tests/test_short_main_path_risk_guard.py` 14/14 PASS. Compileall PASS.

## Completeness

- `tasks.md`: 13/13 marked `[x]`.
- Delta spec requirements:
  1. Route-Consistent Short Risk Gate — `agents/trading/judge.py:1529` (main) and `agents/trading/judge.py:792, 911, 1032` (deferred 15m / pullback / chase).
  2. Hard RSI Threshold Preservation — existing `rsi <= 30` blocks preserved at `agents/trading/judge.py:853, 978, 1404`.
  3. LLM Reversal Risk Tightening — keyword detection inlined inside `_classify_short_entry_risk` at `agents/trading/judge.py:2646`.
  4. Short Gate Attribution Versioning — `_apply_short_gate_attribution` at `agents/trading/judge.py:3057`, fields `short_gate_version / short_gate_decision / short_gate_reason / llm_short_reversal_risk`.

## Correctness

| Scenario | Evidence |
|---|---|
| Main path rejects bullish daily short | `tests/test_short_main_path_risk_guard.py::test_main_short_gate_rejects_daily_bullish_near_shape` |
| Deferred path matches main path rejection | `tests/test_short_main_path_risk_guard.py::test_deferred_and_main_short_gate_return_same_rejection` |
| RSI hard threshold remains unchanged | `agents/trading/judge.py:853, 978, 1404` plus `test_rsi_hard_threshold_is_not_renamed_or_moved` |
| RSI above hard threshold can still fail structural gate | `test_rsi_above_hard_threshold_can_fail_structural_gate_without_changing_hard_gate` |
| Parsed do-not-short text is attributed | `test_main_short_gate_rejects_daily_bullish_near_shape` asserts `llm_short_reversal_risk=True` |
| LLM parse failure does not allow structural-risk short | `test_parse_failure_default_hold_still_rejects_structural_risk` |
| Rejected short includes gate metadata | `_apply_short_gate_attribution` reject path + main path rejection attribution |
| Accepted short includes pass metadata | `test_structurally_clean_short_passes_with_versioned_attribution` + main path attribution merge at `agents/trading/judge.py:1700` |

## Coherence

- OpenSpec design.md decisions D1–D5 are upheld:
  - Single helper `_classify_short_entry_risk` handles main and deferred routes.
  - No new silent daily-bias pass-through introduced; main path rejection happens before ranking.
  - `RSI <= 30` hard blocks remain at three callsites and are not collapsed into the structural gate.
  - LLM text is attribution/tightening only; structural reasons are the rejection drivers.
  - `short_gate_version="short_main_path_parity_v1"` is emitted on accept and reject paths.
- Superpowers design doc frontmatter contains `comet_change`, `role: technical-design`, `canonical_spec: openspec`.
- Test patterns mirror existing Judge tests (`tests/test_pullback_atr_policy.py`, `tests/test_judge_plan_anchor_fields.py`).

## Issues

- CRITICAL: none.
- WARNING: none.
- SUGGESTION: none required for archive. Future work (out of scope): event replay across the 52-sample risk-text dataset can be added to Reviewer slicing once `short_gate_version` accumulates samples.

## Final Assessment

All checks passed. Ready for archive after branch handling.
