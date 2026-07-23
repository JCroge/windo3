# Verification Report: fix-sidecar-ghost-position-safety

Date: 2026-07-23
Change: `fix-sidecar-ghost-position-safety`
Mode: focused

## Result

PASS. The ADA failure class is covered by regression tests and conservative runtime guards:

- Main migration preserves ambiguous/manual TP/SL/OCO protection for sidecar-owned symbols when exchange exposure is present or unknown.
- Sidecar admission blocks same-symbol stacking in OKX `net_mode`; this change does not add an aggregate/per-lot sidecar position model.
- Sidecar monitoring fails closed on ghost or ambiguous net-mode exposure and does not auto close or reduce unproven exchange exposure.
- Sidecar entry drift rejects stale opens before `create_order()` unless the decision is `accept`.
- Sidecar TP anchors are normalized to finite positive floats; invalid `0`, `nan`, and `inf` anchors are rejected.
- Drift rejection audit events are attributed to the matching `shadow_id`; unrelated pending alerts are retained.

## Commands

```bash
pytest tests/test_shadow_tactical_live_core.py tests/test_shadow_tactical_live_cli.py tests/test_shadow_tactical_live_executor.py tests/test_shadow_tactical_owner_isolation.py tests/test_shadow_tactical_exit_monitoring.py tests/test_entry_drift_hybrid_policy.py test_partial_tp_lifecycle.py -q
openspec validate fix-sidecar-ghost-position-safety --strict
```

Result:

```text
138 passed in 6.41s
Change 'fix-sidecar-ghost-position-safety' is valid
```

## Operational Constraints

- Existing ghost/stacked exposure remains an operator action item; the system now halts/blocks and audits instead of making an unproven close/reduce decision.
- Any pre-existing sidecar owner stack should be inspected before sidecar is resumed.
- This change intentionally avoids reconstructing local sidecar positions from exchange state; that would be a separate repair capability.

## Rollout

1. Deploy the Main migration preservation before resuming sidecar live opens.
2. Verify `shadow_tactical_live_sidecar status` and owner registry state show no current ghost or ambiguous same-symbol exposure.
3. If exchange-present ghost exposure exists, manually reconcile/protect it in OKX first.
4. Resume sidecar after confirming same-symbol stacking is blocked and no unmanaged sidecar exposure remains.
