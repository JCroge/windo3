# High-Level Design: Protective SL Halt Recovery

## Context

The current executor treats several protection failures as fail-closed events by calling `_halt_symbol(...)`, which also writes the persistent global `halt_state`. This is safe when a live position may be without exchange-side stop loss, but it creates an availability problem when the risky condition later disappears: the per-symbol halt can be cleared on `/resume`, but the global halt remains until manual action even if the position has already closed and no unprotected position remains.

The WLD event showed this chain:

1. Tactical WLD short filled.
2. Attached OKX SL algo id could not be resolved.
3. `_halt_symbol(reason='sl_algo_unresolved')` wrote global halt `okx_sl_algo_unresolved:WLD-USDT-SWAP`.
4. WLD later closed via `tactical_invalidated`.
5. `/status` still showed a generic "熔断", which was easy to confuse with Tactical circuit loss control.

## Goals / Non-Goals

**Goals:**

- Keep fail-closed behavior when a live position might be unprotected.
- Avoid converting short-lived OKX attached-algo visibility delays into long manual outages when the protection condition resolves.
- Automatically recover protection-driven global halts only after exchange/local state proves there is no unresolved unprotected position.
- Make Telegram status distinguish global halt, per-symbol halt, and Tactical circuit state.

**Non-Goals:**

- Do not change Tactical entry parameters, Tactical PnL accounting, or Tactical circuit thresholds.
- Do not auto-clear manual halts, daily hard stops, reconciliation mismatches, or unknown halt reasons.
- Do not keep trading normally while protection state is pending.

## Decisions

### Decision 1: Treat attached SL lookup failure as a bounded pending state first

When `open_position_with_plan` submits an OKX order with `attachAlgoOrds`, the first lookup of `attachAlgoClOrdId` can be stale. Instead of immediately classifying the position as terminal `unknown`, the executor will perform a bounded verification loop:

- retry by `attachAlgoClOrdId`;
- fetch open algo orders for the symbol;
- optionally run the existing migration/owner-tag matching path.

During this bounded window, new risk is blocked. If the SL is found, the position becomes `protected`. If the bounded window is exhausted, the existing fail-closed halt path remains.

Downstream impact: transient OKX visibility delay should not create a manual outage, but true missing-SL risk still prevents new positions.

### Decision 2: Auto-clear only allowlisted protection halt reasons

The auto-clear path is limited to halt reasons that encode protection uncertainty, such as:

- `okx_sl_algo_unresolved:<symbol>`;
- `okx_migrate_missing_sl:<symbol>` if existing migrate-missing-SL recovery rules apply.

Auto-clear requires all of these:

- the halted symbol is no longer present on exchange, or it is present and has a verified owner-matched protective SL;
- local positions contain no `protection_state in {'unknown', 'pending'}` for that symbol;
- there are no other halted symbols with unresolved protection reasons;
- the global halt reason still matches the same allowlisted protection reason.

Manual, daily hard-stop, force-close, and mismatch reasons remain manual/reconciliation controlled.

Downstream impact: a WLD-style closed position can recover without waiting for `/resume`; genuine global risk halts remain sticky.

### Decision 3: Telegram status becomes a status matrix, not one ambiguous flag

`/status` should still show the global halt state, but label it as global. It should also show:

- per-symbol halt count/list from `agent_health.json`;
- Tactical circuit state from persisted `riskguard_state.tactical_circuit`;
- reconciliation status if present.

This is a formatting and observability change. It must not change trading behavior.

## Risks / Trade-offs

- **Risk: auto-clear could hide a true unprotected position.** Mitigation: only clear after exchange/local sync proves the symbol is closed or protected, and only for allowlisted protection reasons.
- **Risk: retry window delays open confirmation.** Mitigation: keep the retry bounded and block new risk during the pending window.
- **Risk: `/status` grows noisy.** Mitigation: keep it compact: one line each for global halt, per-symbol halt, Tactical circuit.

## Migration Plan

1. Implement test coverage for pending SL verification and protection-halt auto-clear.
2. Implement bounded attached-SL verification.
3. Implement allowlisted protection-halt auto-clear during sync/reconciliation.
4. Update Telegram status formatting.
5. Deploy to cloud, restart, and verify `/status` plus a synthetic/state-file scenario before relying on it live.

Rollback: revert the code changes. Existing manual `/resume` and fail-closed behavior remains the fallback.

## Open Questions

- Exact retry budget for attached SL verification should be chosen in deep design after reading current OKX fetch cadence and rate-limit constraints.
- Whether auto-clear should call an explicit new `HaltState.auto_clear_if_reason(...)` helper or use existing `confirm_resume(...)` with a distinct result string should be settled in deep design.
