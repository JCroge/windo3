## Context

The existing Shadow Tactical and Main Tactical live paths share a name but not a population or lifecycle. In the reproduced window, seven live Tactical closes totaled `-1.4437U`, while 143 eligible-looking shadow rows reduced to only 14 repeated plan clusters. Live accepted the earliest candidate before its single slot filled, while shadow continued recording later candidates. Shadow assumed the recorded entry had filled and ended the entire trade at TP1; live reduced 50% at TP1 and allowed the remainder to close through thesis invalidation, weakened/no-progress, max hold, or SL. Three live `tactical_invalidated` closes accounted for `-3.2773U` while the other four closes totaled `+1.8336U`.

The user selected promotion approach B: preserve the exact Shadow Tactical plan at the point it is emitted, but prevent Main strategy logic from modifying it after that point. The first live cohort uses fixed `100U` margin and three Tactical slots, full close at TP1, one attempt per structural episode, rolling 24-hour final-PnL admission stop at `-15U`, and a three-loss 60-minute pause. Existing sidecar exposure must be drained and reconciled before cutover. Telegram `/status` must expose the same persisted Tactical state used operationally without becoming a risk authority.

Constraints include OKX position aggregation, existing one-position-per-symbol local state, attached-order ownership, asynchronous final PnL correction, crash windows around order submission, and an already dirty worktree containing separately approved sidecar resident-run changes that must be preserved.

## Goals / Non-Goals

**Goals:**

- Give shadow and live one canonical Tactical intent, episode identity, entry state machine, and exit state machine.
- Prevent stale price chasing, slot-release backfill, repeated attempts within one market episode, and restart-driven duplicate orders.
- Isolate Tactical strategy exits from Main Position Analyst, Main trailing, and Main add/reduce behavior while preserving global safety authority.
- Make Tactical sizing, slots, rolling loss, loss streak, protection integrity, and operational status explicit and persistent.
- Drain and retire sidecar live admission without adopting ambiguous sidecar account objects into Tactical V2.
- Produce replay, failure-injection, cloud shadow, and cutover evidence before live enablement.

**Non-Goals:**

- Designing a new standalone Tactical signal or SL/TP algorithm. V2 freezes the existing Shadow Tactical plan; an independent plan calculator is a later shadow experiment.
- Changing Main sizing, Main exits, global `MAX_TRADE_AMOUNT`, or global emergency risk behavior.
- Supporting Main and Tactical stacking on the same symbol or per-lot ownership inside an aggregated OKX net position.
- Treating a 24-hour observation or 30 trades as proof of durable strategy edge.
- Deleting sidecar code or historical state during cutover.

## Decisions

### Canonical immutable Tactical intent inside the Main process

Judge remains the upstream producer of Shadow Tactical plan values. A new intent factory validates and freezes those values into `tactical_intent.v2`; it does not recalculate SL/TP or re-run Main strategy admission later. The Tactical engine runs under `run_agents.py` and owns entry, position, exit, risk, and status state through a narrow interface. This preserves historical strategy meaning while removing post-classification Main interference.

Alternative rejected: re-enable legacy Main Tactical with `TACTICAL_SHADOW_ONLY=false`. It preserves the exact parity defects being fixed. Alternative rejected for V2: build an independent 15m/ATR Tactical strategy, because old shadow evidence would no longer describe the promoted strategy.

### Structural episode registry instead of exact-plan deduplication

`episode_id` is stable across repeated rows in the same symbol, direction, and 15m structure epoch. Exact entry/SL/TP values form a separate `plan_hash` for audit, not identity. An episode resets only after an opposing 15m block, a return to neutral followed by renewed direction, or a newly confirmed pivot/structure break after the prior episode terminates. Attempt, capacity skip, miss, or close all make the episode ineligible for later retry.

This prevents repeated plans with slightly changed prices from bypassing an exact hash and prevents a released slot from opening an old episode.

### R-based entry state machine with no Tactical drift recalculation

The engine uses executable ask for longs and bid for shorts. It may execute immediately only when the entry price is no more than `0.10R` worse than the frozen entry, where `R=abs(entry_ref-stop_loss)`. Otherwise it places a limit at the original entry for at most 900 seconds. It never shifts SL/TP to current price.

Pending entry is canceled permanently if TP is reached first, SL is reached, the 15m thesis invalidates, the episode resets, or the TTL expires. Capacity-full episodes are skipped, not queued. A partial fill cancels the remainder and protects only confirmed filled size. This is stricter and more comparable than the Main percentage drift policy.

### One post-fill strategy lifecycle

After fill, Tactical V2 has only full-position TP1, full-position SL, and a 90-minute max hold. Post-fill thesis invalidation and weakened/no-progress exits are removed from V2 because current shadow accounting never modeled them and they drove the largest reproduced live loss bucket. Main Position Analyst, Main break-even/profit trailing, and add/reduce actions must ignore Tactical V2.

Global drawdown, flash-move, protection-integrity, manual emergency, and exchange safety paths retain authority. Their closes are attributed as `risk_forced` and remain separate from normal strategy outcome buckets.

### Exchange-owned Tactical OCO and serialized local exits

Full TP makes exchange-owned TP+SL OCO practical and avoids missing TP while the process is unavailable. The OCO and entry order carry deterministic, owner-tagged identities derived from the intent and are persisted on the position. A fill is not considered safely open until both protection legs can be verified. Failure closes confirmed exposure when possible and enters a non-expiring integrity halt.

Max-hold and global close paths use the existing symbol exit lock and owner-bound cleanup. Exchange fills remain authoritative; concurrent local observations reconcile rather than submit a second close.

### Fixed Tactical capacity with shared account safety

Tactical uses `TACTICAL_MARGIN_USDT=100` and `TACTICAL_MAX_CONCURRENT=3`; it does not change `MAX_TRADE_AMOUNT`. Pending entries count toward the three slots. Tactical slots are independent of the three Main slots, but exchange free balance, one-position-per-symbol ownership, and global account exposure can still reject an intent. No additional Tactical correlation gate is introduced in the first cohort so the live sample is not silently narrowed.

### One persistent Tactical risk governor

The governor is the only Tactical admission authority. It reconstructs a rolling 24-hour window from final PnL ledger events, keyed by `resolution_id`. A correction applies only its delta. At `<= -15U`, new opens pause until the rolling sum recovers; existing positions continue. Three consecutive final losing episodes pause opens for 60 minutes and consume/reset the streak at pause start. A non-loss resets the active streak. Protection or ownership ambiguity enters an integrity halt that cannot expire on a timer.

This replaces the unused/duplicated `can_open_tactical()` and Judge file-read implementations with one persisted state model.

### Append-only lifecycle plus atomic read model

Intent and circuit transitions append to a namespaced Tactical V2 event ledger. Atomic snapshots accelerate recovery but are not the sole source of truth. Before exchange submission, state advances to `submitting` with a deterministic client order id and a durable exchange-visibility deadline. A successful exact lookup that is temporarily empty stays in reconciliation and is periodically rechecked without resubmitting; query failure or deadline exhaustion fails closed. Only `entry_reconciliation_unknown` and `entry_cancel_unproven` are eligible for this automated proof loop, and an original deferred-cancel reason remains durable until cancellation or fill is proven.

Final PnL corrections use a durable publication outbox. The governor, Judge, and Reviewer deduplicate by `resolution_id`; the producer records a publication acknowledgement only after the bus accepts the final and replays unacknowledged corrections on restart. This is intentionally at-least-once delivery across a crash between downstream receipt and durable acknowledgement; cross-restart exactly-once Telegram delivery is outside this change.

A compact atomic Tactical status snapshot is the only Tactical data source for Telegram formatting. It includes `updated_at`, mode/version, sizing, slot occupancy, rolling PnL, streak/pause, episode aggregates, active/pending symbols, protection/reconciliation state, and shadow/live parity counts. Telegram is read-only; stale, missing, malformed, or non-finite data is shown as `STALE` or unknown rather than healthy.

### Sidecar drain before cutover

Sidecar admission is stopped first, but its monitor remains running until all proven sidecar owners are exchange-flat, all OCO/protection ownership is reconciled, and pending final PnL is resolved or explicitly documented. Sidecar state is archived, not deleted. Tactical V2 refuses to adopt old sidecar positions or owner rows. Only then can V2 live admission be enabled.

## Risks / Trade-offs

- **[Three Tactical slots can coexist with three Main slots]** -> Keep free-balance and global exposure checks authoritative; show account-capacity skips explicitly; do not increase global limits implicitly.
- **[A rolling `-15U` admission threshold is not a final loss ceiling]** -> Existing positions retain their exits after the threshold; `/status` states this as an admission pause, and global emergency controls remain available.
- **[Fixed Shadow plan values still originate from Main diagnostic analysis]** -> Record source and plan hash; guarantee immutability after intent creation; evaluate a standalone calculator only as a later versioned shadow experiment.
- **[Exchange OCO semantics may differ from local tick simulation]** -> Pin trigger-price type, record exchange order evidence, model bid/ask in shadow, and classify execution variance instead of hiding it.
- **[Partial fills produce less than 100U exposure]** -> Cancel remainder, protect actual filled amount, and report partial size; never chase the remainder.
- **[Episode reset is stateful and can over- or under-deduplicate]** -> Persist the reset evidence and add historical repeated-row plus structure-reset fixtures.
- **[Crash between exchange action and local persistence]** -> Persist `submitting` before I/O, derive deterministic client ids, and query exchange state before retry.
- **[Main strategy leakage can reappear through shared subscribers]** -> Enforce owner/track guards at Position Analyst, trailing, add/reduce, and risk-alert call sites; test forbidden actions through the message bus.
- **[TG snapshot can be stale while the engine is healthy]** -> Show freshness explicitly and alert on status writer failure; never use the snapshot as a trading gate.

## Migration Plan

1. Replay the reproduced historical window through the episode and entry lifecycle; compare per-intent transitions, not raw row or aggregate PnL alone.
2. Implement and run unit, message-bus integration, crash-recovery, exchange-fake OCO, PnL-correction, and TG formatting tests.
3. Deploy Tactical V2 shadow-only for at least 24 hours using live executable prices while legacy sidecar behavior remains unchanged.
4. Stop sidecar new admissions, drain and reconcile every sidecar owner/protection/PnL record, and archive the state snapshot.
5. Enable Tactical V2 live directly with fixed `100U` margin and three slots; keep the shadow adapter active for parity audit.
6. Accept rollout only with zero duplicate orders, zero stale chase fills, zero Main strategy exits on Tactical positions, verified protection for every fill or immediate safe close, and every shadow/live mismatch classified.

Rollback disables new Tactical intents and cancels pending Tactical entry orders. Filled Tactical positions remain managed by their verified OCO and Tactical exit controller until flat; rollback must not kill their monitor or automatically restart sidecar admission.

## Open Questions

No behavioral questions remain before implementation planning. Exchange fake/real OKX acceptance must still confirm whether the account exposes attached TP and SL under one algo id or separate ids; the ownership model must support either representation without changing the external lifecycle contract.
