## 1. Regression Test

- [x] 1.1 Add a regression test proving a Sidecar Tactical plan uses `tactical_min_rr_for_track` during small-band drift recalculation.
- [x] 1.2 Add coverage for the medium-band Tactical floor bump and generic fallback behavior.

## 2. Implementation

- [x] 2.1 Select the Sidecar Tactical floor from `gate_metadata.tactical_min_rr_for_track` when valid, preserving existing fallback behavior.
- [x] 2.2 Apply recomputed SL/TP when Sidecar drift returns `recalc_pass`; retain fail-closed behavior for all other decisions.
- [x] 2.3 Run focused and relevant Sidecar tests and confirm no Main drift regression.

## 3. Deployment

- [ ] 3.1 Commit and sync the fix to cloud.
- [ ] 3.2 Restart Sidecar with `--size-usdt 100 --max-active 3` and verify runtime state.
- [ ] 3.3 Observe post-restart audit for new opens, executor rejects, and safety anomalies.
