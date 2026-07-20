## Context

`monitor_sidecar_owned_exposure()` currently calls `_sidecar_position_for_owner()` first. If the local sidecar position is missing or cannot be proven, the monitor records `monitor_skipped_unproven` and continues. That is safe for exchange actions, but it leaves owner rows open even when OKX reports no matching position. Those rows are counted by `_active_owner_count()` and can block future strict Tactical signals via `sidecar_active_cap`.

## Goals / Non-Goals

**Goals:**

- Close stale unproven owner rows when the exchange confirms the owner symbol is flat.
- Keep fail-closed behavior when exchange position state is unknown or present.
- Keep ledger and audit trails sufficient for later PnL reconciliation.

**Non-Goals:**

- Do not close, reduce, or mutate any unproven exchange exposure.
- Do not change sidecar entry filters or active-cap sizing.
- Do not change OKX `net_mode` position modeling in this hotfix.

## Decisions

- Check exchange state for unproven owners before skipping them. If `_sidecar_exchange_position_state()` returns `flat`, mark the owner closed and write an external-close pending ledger event using owner metadata. If it returns `present`, `unknown`, or `unsupported`, keep the existing skip behavior.
- Reuse the existing `_record_exchange_flat_close()` helper so lifecycle and ledger semantics stay aligned with the proven flat path.
- Use owner row fields as the metadata source when local position is unavailable. This preserves `shadow_id`, symbol, side, amount, opened time, and protection IDs in the pending close event.

## Risks / Trade-offs

- [Risk] Exchange fetch failures could cause false decisions. -> Mitigation: only reconcile when exchange state is explicitly `flat`; unknown states still skip.
- [Risk] An unproven main-process position for the same symbol could exist. -> Mitigation: the sidecar does not submit close/reduce orders on this path; it only closes sidecar owner metadata after exchange-side flat confirmation for that symbol.
- [Risk] Pending PnL may require later resolver work. -> Mitigation: use the existing pending external-close ledger path instead of fabricating final PnL.
