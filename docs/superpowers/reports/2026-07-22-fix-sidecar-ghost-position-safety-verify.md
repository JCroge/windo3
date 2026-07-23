# Verification Report: fix-sidecar-ghost-position-safety

Date: 2026-07-23
Change: `fix-sidecar-ghost-position-safety`
Mode: full

## Result

PASS. The ADA failure class is covered by regression tests and conservative runtime guards:

- Main migration preserves ambiguous/manual TP/SL/OCO protection for sidecar-owned symbols when exchange exposure is present or unknown.
- Sidecar admission blocks same-symbol stacking in OKX `net_mode`; this change does not add an aggregate/per-lot sidecar position model.
- Sidecar monitoring fails closed on ghost or ambiguous net-mode exposure and does not auto close or reduce unproven exchange exposure.
- Sidecar entry drift rejects stale opens before `create_order()` unless the decision is `accept`.
- Sidecar drift anchors, explicit drift percentage anchors, and TP anchors are normalized to finite positive floats; invalid `0`, `nan`, and `inf` anchors are rejected before order submission.
- Drift rejection audit events are attributed to the matching `shadow_id`; unrelated pending alerts are retained.
- Sidecar admission now rejects when exchange-position guard fetch fails, instead of assuming an empty account-position list.

## Summary

| Dimension | Status |
|-----------|--------|
| Completeness | 20/20 tasks complete; 4/4 delta spec capabilities checked |
| Correctness | Requirements covered by implementation and regression tests |
| Coherence | Matches OpenSpec design and Superpowers design doc |

## Requirement Evidence

- Protection preservation: `executor.py` preserves ambiguous TP/SL/OCO algos for sidecar-owned present/unknown exchange exposure and records `sidecar_protected_algos`.
- Same-symbol admission: `utils/shadow_tactical_live.py` and `scripts/shadow_tactical_live_sidecar.py` reject active owner and account-exposure stacks before `open_sidecar_plan()`.
- Ghost monitoring: `scripts/shadow_tactical_live_sidecar.py` emits `monitor_ghost_exposure`, halts the symbol, records operator-action metadata, and does not call close/reduce in unproven branches.
- Ambiguous stacks: monitor emits `monitor_ambiguous_net_mode_stack` and skips exit action unless exchange-flat reconciliation is proven.
- Entry drift: `executor.py` rejects non-`accept` drift decisions and non-finite entry/SL/live-price anchors before `create_order()` and persists accepted drift metadata.
- Review fixes: net-mode ambiguous owner stacks are grouped by symbol, not side, unless OKX is explicitly in `long_short_mode`.

## Commands

```bash
pytest tests/test_shadow_tactical_live_core.py tests/test_shadow_tactical_live_cli.py tests/test_shadow_tactical_live_executor.py tests/test_shadow_tactical_owner_isolation.py tests/test_shadow_tactical_exit_monitoring.py tests/test_entry_drift_hybrid_policy.py test_partial_tp_lifecycle.py -q
openspec validate fix-sidecar-ghost-position-safety --strict
```

Result:

```text
142 passed in 7.00s
Change 'fix-sidecar-ghost-position-safety' is valid
```

Additional verify checks:

```text
openspec status --change fix-sidecar-ghost-position-safety --json: all_done, 20/20 tasks
openspec instructions apply --change fix-sidecar-ghost-position-safety --json: proposal/design/specs/tasks available
secret scan on changed runtime/spec/report files: no hardcoded credential literals found
git diff --check: clean
code review re-check: no Critical/Important issues; ready to merge
```

## Issues

### Critical

None found.

Resolved during verify:

- Sidecar drift anchor validation could accept non-finite `entry_ref`, `stop_loss`, or live price before order submission. Fixed with finite-positive normalization and regression coverage.
- Explicit `sl_pct`/`tp_pct` anchors could be non-finite or non-positive and still be treated as missing/derivable. Fixed so missing anchors derive, while invalid-present anchors reject before order submission.
- OKX `net_mode` ambiguous owner stacks were grouped by `(symbol, side)`, which could allow a proven opposite-side legacy row to close while another same-symbol owner was unproven. Fixed by symbol-level grouping for net-mode monitoring.

### Warning

- Sidecar admission treated exchange-position fetch failure as an empty position list. Fixed to reject with `same_symbol_exposure_unknown` before `open_sidecar_plan()`.

### Suggestion

None.

## Operational Constraints

- Existing ghost/stacked exposure remains an operator action item; the system now halts/blocks and audits instead of making an unproven close/reduce decision.
- Any pre-existing sidecar owner stack should be inspected before sidecar is resumed.
- This change intentionally avoids reconstructing local sidecar positions from exchange state; that would be a separate repair capability.

## Rollout

1. Deploy the Main migration preservation before resuming sidecar live opens.
2. Verify `shadow_tactical_live_sidecar status` and owner registry state show no current ghost or ambiguous same-symbol exposure.
3. If exchange-present ghost exposure exists, manually reconcile/protect it in OKX first.
4. Resume sidecar after confirming same-symbol stacking is blocked and no unmanaged sidecar exposure remains.
