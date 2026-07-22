## Why

The 2026-07-22 ADA sidecar incident exposed a split-brain failure between sidecar owner rows, sidecar local position metadata, OKX net-mode exposure, and Main algo migration. The system can currently leave sidecar exchange exposure unmanaged while Main cancels manual protection orders and the sidecar skips unproven owners.

This must be fixed before resuming sidecar scale-up because the failure mode is not ADA-specific: it follows from current same-symbol sidecar stacking, symbol-keyed local position state, and owner-proof gaps.

## What Changes

- Prevent Main OKX algo migration from canceling manual, foreign, or ambiguous TP/SL algos on a symbol that is currently sidecar-owned and still has exchange exposure.
- Prevent sidecar same-symbol stacking in OKX `net_mode` unless the implementation has an explicit aggregate-position model that can prove and manage the whole net exposure.
- Make sidecar monitoring fail closed when `owners.open > 0`, exchange position is present, but sidecar executable position metadata is missing or unproven.
- Add an operational guard/audit signal for sidecar ghost exposure: open owner rows plus exchange exposure plus no proven local sidecar position and no pending TP/SL protection.
- Add regression coverage for the ADA class:
  - manual OCO/conditional protection survives Main migration while the symbol is sidecar-owned,
  - repeated sidecar same-symbol opens are blocked or modeled as one aggregate position,
  - monitor cannot close only one owner row and leave remaining same-symbol net exposure unmanaged,
  - unproven present exposure produces a halt/alert instead of silent `monitor_skipped_unproven` loops.

## Capabilities

### New Capabilities

- None expected. This change tightens existing sidecar ownership and exit safety semantics rather than introducing a new trading capability.

### Modified Capabilities

- `shadow-tactical-sidecar-exit-monitoring`: tighten sidecar owner proof, ghost-exposure handling, same-symbol sidecar admission, and net-mode monitor semantics.
- `tactical-exit-track`: ensure Tactical hard veto and no-stacking requirements also apply to the live sidecar admission path.
- `protective-sl-owner-tag`: clarify Main migration must preserve ambiguous/manual protection on sidecar-owned symbols instead of treating it as orphan residual.
- `entry-drift-policy`: apply stale-entry protection to sidecar live opens instead of bypassing the drift guard entirely.

## Impact

- Affected code:
  - `scripts/shadow_tactical_live_sidecar.py`
  - `utils/shadow_tactical_live.py`
  - `executor.py`
- Affected tests:
  - `tests/test_shadow_tactical_exit_monitoring.py`
  - `tests/test_shadow_tactical_live_core.py`
  - `tests/test_shadow_tactical_owner_isolation.py`
  - likely a focused migration regression near existing partial-TP/algo migration tests.
- No dependency, public API, database schema, or cloud `.env` change is expected.
- Operationally, sidecar scale-up remains paused until this change is implemented and verified.
