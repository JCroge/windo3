## Why

Sidecar owner rows can remain `open` after the exchange position is already flat when the local sidecar position record is missing or no longer provable. Those stale owner rows consume `MAX_CONCURRENT_POSITIONS`, causing valid strict Tactical shadow signals to be rejected with `sidecar_active_cap`.

This happened with OKX `net_mode` where several same-symbol Tactical opens shared one exchange-side net exposure, then local owner and lifecycle state stopped closing in lockstep.

## What Changes

- Add automatic exchange-flat reconciliation for unproven sidecar owner rows during the sidecar monitor loop.
- Preserve ownership safety: the sidecar still must not close or reduce unproven exposure that exists on the exchange.
- Record audit and pending ledger close metadata when an unproven owner is reconciled flat.
- Add regression tests for the stale-owner cap leak and the non-flat safety path.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `shadow-tactical-sidecar-exit-monitoring`: unproven owner rows must be auto-closed only when the exchange confirms the sidecar symbol is flat.

## Impact

- Affected code: `scripts/shadow_tactical_live_sidecar.py`
- Affected tests: `tests/test_shadow_tactical_exit_monitoring.py`
- No public API changes, new dependencies, schema migrations, or main-process behavior changes.
