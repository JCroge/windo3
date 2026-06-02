# Verification Report: entry-drift-hybrid-policy

**Verified at:** 2026-06-01
**Mode:** full (18 tasks / 1 capability / 20 changed files)
**Base ref:** `733c671f7f6e2437f07d36064b3db0ceaeb547fc` … HEAD (`9264242`)
**Test baseline:** `954 passed / 4 deselected / 1 warning` (was 921; +33 net)
**Build:** `python3 -m pytest -q` PASS · `compileall -q .` exit 0

## Summary

| Dimension    | Status                                                |
|--------------|-------------------------------------------------------|
| Completeness | 18/18 tasks done · 4/4 spec requirements implemented  |
| Correctness  | 4/4 requirements covered · 3/3 named scenarios passed |
| Coherence    | Design Doc & delta spec aligned · no drift detected   |

**Final assessment:** All checks passed. No CRITICAL issues. Ready for archive.

## Completeness

### Task completion

`openspec status --change entry-drift-hybrid-policy --json` reports 18/18 tasks
checked. The "后续（不在本 change 范围）" section explicitly captures two
follow-ups (Telegram custom drift copy, OKX testnet smoke) that are intentionally
out of scope for this change.

### Spec requirement → implementation map

OpenSpec delta spec at `openspec/changes/entry-drift-hybrid-policy/specs/entry-drift-policy/spec.md`
declares 4 ADDED Requirements. Each is backed by code:

| Requirement | Implementation evidence |
|---|---|
| Entry Drift Classification | `executor.py:1258` `_classify_entry_drift` (4 bands, constants at lines 18–22, recompute via `_recompute_plan_for_drift` at 1191) |
| Plan Field Fail-Safe | `executor.py:1268-1281` (missing entry_ref/sl_pct/tp_pct → `_enqueue_drift_alert('plan_missing_entry_ref')` + accept with drift_pct=0.0) |
| Two-Gate Execution | Gate 1 at `executor.py:2158`, Gate 2 at `executor.py:2483`. `orig_plan_for_gate2 = copy.deepcopy(plan)` at 2157 BEFORE Gate 1 ensures Gate 2's baseline is the original `plan.entry_ref`. |
| TP Field Single Source of Truth | `executor.py:2085` `_set_position_tp` setter; invariant at `_update_trailing` halts symbol on breach with `_halt_symbol(reason='tp_invariant_breach')` |

## Correctness

### Scenario coverage

The delta spec has 3 named scenarios under Entry Drift Classification, plus
implicit coverage for the other 3 requirements via behavior assertions. All
scenarios have direct test coverage:

| Scenario | Test case |
|---|---|
| 5/30 XLM stale plan abandons cleanly | `test_classify_drift_xlm_replay_72pct_abandon` + `test_gate1_abandons_xlm_replay` |
| medium band recalc passes when R:R clears bumped floor | `test_classify_drift_medium_band_recalc_pass_with_higher_rr` (drift=3%, R:R=2.40, floor=2.20) |
| medium band recalc fails when R:R below bumped floor | `test_classify_drift_medium_band_recalc_fail` (drift=3%, R:R=2.0, floor=2.20) |
| Two-Gate Execution: Gate 2 baseline is original plan | `test_gate2_basis_is_original_entry_ref_not_segmented` |
| TP invariant: violation halts symbol | `test_update_trailing_invariant_breach_halts_symbol` |
| Plan field fail-safe accept | `test_classify_drift_missing_entry_ref_failsafe_accept` + `test_event_backtest_drift_compat::test_old_plan_skips_drift_gate_failsafe` |

Test counts:
- `tests/test_entry_drift_hybrid_policy.py`: 28 cases
- `tests/test_judge_plan_anchor_fields.py`: 4 cases
- `tests/test_event_backtest_drift_compat.py`: 1 case

All 33 new cases pass; full suite passes 954.

### Boundary-inclusion regression

Each band boundary (0.005, 0.02, 0.05) is pinned by a dedicated test
(`test_classify_drift_boundary_*`) verifying the inclusive lower edge of the
next band. This protects against future edits that flip strict-vs-inclusive
comparison semantics.

## Coherence

### Design Doc adherence

Design Doc `docs/superpowers/specs/2026-06-01-entry-drift-hybrid-policy-design.md`
declares 5 key decisions (D1-D5). Implementation matches each:

| Decision | Evidence |
|---|---|
| D1: Plan missing → fail-safe accept + observable | `executor.py:1268-1281` enqueues `plan_missing_entry_ref` |
| D2: Medium band floor bump = +0.20 absolute | `executor.py:22` constant; applied at line 1218, 1304 |
| D3: Invariant halt + write-time setter discipline | `executor.py:2085` setter + `_update_trailing` invariant; failure path halts symbol |
| D4: Gate 2 baseline = original plan.entry_ref | `executor.py:2157` deepcopy, passed via `orig_plan` kwarg through both `_execute_limit_order` callsites |
| D5: deepcopy + add fields, not diff | `_recompute_plan_for_drift` lines 1198 deepcopy, lines 1227-1234 augment with recompute_reason / original_entry_ref / etc. |

### Delta spec / Design Doc / proposal alignment

- proposal.md scopes: Hybrid drift gate + plan anchors + double-truth fix + observability — all present.
- design.md (open phase placeholder) was superseded by the technical Design Doc; both reference the same capability `entry-drift-policy`.
- Delta spec language ("SHALL classify", "SHALL accept", "SHALL run twice", "SHALL go through a single setter") maps 1:1 to implementation guarantees.

No drift detected between spec / design / code. No "Implementation Divergence"
section needed in the Design Doc.

### Cross-task consistency

Final whole-implementation review (commit-by-commit reading) verified:

1. Drift threshold constants (0.005 / 0.02 / 0.05 / 0.20) appear ONLY at the
   module-level constants in `executor.py:18-22` and the agent-layer
   reconstruction in `_build_drift_attribution` (which now references
   `ENTRY_DRIFT_SMALL_PCT` after the final-review nit fix in `96df15d`).
2. TP writes route through `_set_position_tp` at the new construction point
   (`executor.py:2367`); `_update_trailing` invariant catches any bypass.
3. SL invariant fires AFTER Gate 1 recalc_pass rebinds `stop_loss`, so the
   invariant validates the recomputed (not stale) value.
4. All 5 new `risk_alert.type` values appear in both the executor enqueue
   sites AND the telegram_notifier `critical_types` tuple.
5. Reject reason mapping `entry_drift_abandoned → drift_too_large`,
   `entry_drift_rr_fail → drift_rr_floor_fail` is intact and inspected BEFORE
   drain in the agent-layer reject branch.

## Issues

### CRITICAL: none

### WARNING: none

### SUGGESTION: none net-new for this change

(The two follow-ups in the tasks "后续" section — Telegram custom copy and
OKX testnet smoke — are explicit out-of-scope items, not warnings.)

## Out-of-scope deliverables (recorded for the operations team)

- **Telegram custom drift copy**: 5 critical_types are registered. They reach
  `_handle_risk_alert` and fall through to the generic alert path. A future
  change can add type-specific Telegram message formatting; this change leaves
  the data plumbing complete.
- **OKX testnet smoke** (drift abandon / drift recalc): a manual runbook step
  documented in the acceptance doc AC-10. Does not block archive but is
  required before live extension.

## Verdict

**Ready for archive.** No CRITICAL or WARNING issues. All scenarios in delta
spec are test-covered. Design Doc is faithfully implemented with no drift.
