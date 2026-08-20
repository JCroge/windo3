## Why

Restored Sidecar admission is currently rejecting Tactical candidates after modest price drift because the generic drift recalculation falls back to an R:R floor of `2.0`. Tactical Sidecar candidates are admitted under their frozen Tactical floor, commonly below `2.0`, so the live path can reject candidates that passed the Sidecar policy before any order is submitted.

## What Changes

- Make Sidecar entry-drift recalculation use the candidate's frozen Tactical R:R floor when available.
- Preserve the existing generic `2.0` fallback for plans that do not carry a Tactical floor.
- Accept a bounded `recalc_pass` Sidecar plan with recomputed SL/TP, while retaining the hard drift bound and all downstream execution safety checks.
- Add regression coverage for a Tactical plan with `effective_rr` below `2.0` that passes drift recalculation.
- Deploy the fix to cloud and restart Sidecar with the bounded `--max-active 3` command.

## Capabilities

### New Capabilities

### Modified Capabilities
- `entry-drift-policy`: Sidecar drift recalculation must honor the frozen Tactical R:R floor instead of always defaulting to the generic floor.

## Impact

- Affects `executor.py` Sidecar drift classification and its focused tests.
- Changes only Sidecar entry admission after bounded price drift; Main and Tactical V2 paths remain unchanged.
- Requires cloud process restart so the running Sidecar loads the deployed code.
