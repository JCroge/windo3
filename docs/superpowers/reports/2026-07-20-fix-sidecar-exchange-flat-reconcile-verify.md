# Verification Report: fix-sidecar-exchange-flat-reconcile

Date: 2026-07-20
Change: `fix-sidecar-exchange-flat-reconcile`
Mode: light

## Checks

- PASS: `tasks.md` all tasks completed.
- PASS: changed files match the hotfix scope: sidecar monitor implementation, regression tests, and OpenSpec artifacts.
- PASS: root cause removed. `monitor_sidecar_owned_exposure()` now checks exchange state before skipping an unproven owner and reconciles the owner when the exchange confirms flat.
- PASS: fail-closed behavior preserved. Present or unknown exchange state keeps the owner open and does not submit close/reduce actions.
- PASS: no hardcoded secrets or new destructive exchange actions added.

## Commands

```bash
openspec validate fix-sidecar-exchange-flat-reconcile --strict
pytest tests/test_shadow_tactical_exit_monitoring.py tests/test_phantom_position_resync.py test_halt_resume_ownership.py -q
```

Result:

```text
Change 'fix-sidecar-exchange-flat-reconcile' is valid
31 passed in 3.10s
```

## Notes

This hotfix does not change sidecar entry filters or active-cap sizing. It only prevents stale owner rows from consuming active cap after the exchange is already flat.
