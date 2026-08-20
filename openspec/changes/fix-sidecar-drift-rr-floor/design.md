## Context

The Sidecar maps frozen Tactical shadow records into executable plans. Its entry-drift guard recomputes SL/TP when the live price moves beyond the accept band. The recomputation currently reads `gate_metadata.rr_floor` and falls back to `2.0`. Frozen Tactical records instead expose `tactical_min_rr_for_track` and may legitimately pass with a lower floor, such as `0.75`.

The fix must preserve the existing generic executor behavior and the medium-band `+0.20` bump. Only Sidecar plans with a frozen Tactical floor should use that floor during drift recalculation.

## Goals / Non-Goals

**Goals:**

- Use `tactical_min_rr_for_track` as the Sidecar drift base floor when it is finite and positive.
- Keep the existing `gate_metadata.rr_floor` and `2.0` fallback behavior for non-Tactical plans.
- Accept `recalc_pass` Sidecar plans by applying the classifier's recomputed SL/TP before downstream execution checks.
- Make the behavior deterministic and covered by a regression test.
- Restart the cloud Sidecar with `--max-active 3` after deployment.

**Non-Goals:**

- Do not change the frozen Sidecar policy, risk tiers, active-cap semantics, or Main execution path.
- Do not lower the Tactical admission policy itself.
- Do not bypass drift protection: `drift_too_large`, invalid anchors, failed R:R, balance, slippage, exchange precheck, and protection failures still reject.

## Decisions

1. **Read the Tactical floor from the mapped plan metadata.**
   The Sidecar mapper already preserves `tactical_min_rr_for_track` inside `gate_metadata`, so no new public API or schema is needed.

2. **Prefer the Tactical floor only for Sidecar drift plans.**
   The generic `_recompute_plan_for_drift` helper is shared by Main paths. The Sidecar drift plan will carry the selected floor through its attribution metadata, while generic callers retain the current fallback.

3. **Reject malformed floors safely.**
   Non-numeric, non-finite, or non-positive Tactical floors are ignored and fall back to the existing floor selection rather than weakening the guard.

4. **Use recomputed protection only after `recalc_pass`.**
   The classifier's new SL/TP are copied to the Sidecar plan before order construction. The original entry reference remains unchanged for attribution, and the hard drift bands remain unchanged.

5. **Deploy by process restart with explicit bounded arguments.**
   The cloud Sidecar will be restarted using `--size-usdt 100 --max-active 3`; Main will not be restarted or reconfigured.

## Risks / Trade-offs

- [Risk] A lower Tactical floor and bounded recomputation admit more drifted candidates than the previous strict reject path. → Mitigation: the candidate already passed frozen Tactical policy, the hard drift bands remain unchanged, and SL/TP side validation, slippage, precheck, and protective-SL checks remain active.
- [Risk] Cloud SSH instability can make deployment verification intermittent. → Mitigation: use short read-only probes, verify process command/PID, status, and audit output after restart.

## Migration Plan

1. Add the failing regression test.
2. Implement the narrow floor-selection change and run focused tests.
3. Run the relevant Sidecar test suite and commit.
4. Sync the commit to `/opt/crypto-arbitrage`.
5. Stop the stale Sidecar process and start the deployed script with `--max-active 3`.
6. Verify exactly one Sidecar process, `admission_enabled=true`, `active=0`, and no startup exception.
7. Observe the next eligible candidates and confirm drift audit reasons.

Rollback is to restore the previous commit and restart the Sidecar with the same bounded command, leaving Main untouched.

## Open Questions

- Whether the current PUMP stream continues producing candidates whose post-drift R:R remains above the Tactical floor; this will be answered by post-restart audit data.
