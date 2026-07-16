## Why

Shadow Tactical sidecar currently opens live positions, but once a position is open there is no periodic sidecar-owned exit evaluation. That means Tactical TP, invalidation, weakened-thesis, and max-hold exits can be missed until manual intervention, and the ONDO case showed the sidecar can also carry the wrong execution symbol through its open/close path.

## What Changes

- Add a sidecar poller that scans open sidecar-owned Tactical positions on a fixed cadence and applies the existing Tactical exit semantics.
- Canonicalize sidecar symbol handling so execution uses the exchange swap instrument while ownership/audit state keeps the internal symbol.
- Keep all behavior scoped to shadow Tactical sidecar state; main strategy, main live process, and symbol classification are unchanged.
- Preserve the current stop command as a shutdown path that only closes proven sidecar-owned positions.
- Emit audit and lifecycle records for monitoring, partial exits, closes, and skip/fail-safe cases.

## Capabilities

### New Capabilities
- `shadow-tactical-sidecar-exit-monitoring`: sidecar-owned Tactical position monitoring, symbol canonicalization, and exit execution.

### Modified Capabilities
- None.

## Impact

- `scripts/shadow_tactical_live_sidecar.py`
- `executor.py`
- `utils/shadow_tactical_live.py`
- sidecar state files under `data/shadow_tactical_live_*`
- tests covering shadow open, monitor, and stop paths

