---
comet_change: promote-shadow-tactical-v2-live
role: technical-design
canonical_spec: openspec
archived-with: 2026-08-05-promote-shadow-tactical-v2-live
status: final
---

# Shadow Tactical V2 Live Promotion Technical Design

## Canonical Scope

OpenSpec is the canonical requirement source for this change. This document defines the implementation architecture, state ownership, concurrency model, recovery rules, and verification strategy. It does not replace the delta specs under `openspec/changes/promote-shadow-tactical-v2-live/specs/`.

The implementation promotes the plan population currently produced for Shadow Tactical. It does not re-enable the legacy `tactical_shadow_only=false` execution path and does not turn the sidecar into the new strategy owner.

## Chosen Architecture

`MultiExecutor` will own one `TacticalV2Controller` that shares the existing `ContractExecutor` instance. The controller is a deep module with a narrow API; it is not a second `BaseAgent` and does not construct a second exchange or positions client.

```text
MultiJudge
  -> tactical_candidate.v2
       -> MessageBus critical journal
       -> MultiExecutor
            -> TacticalV2Controller
                 -> TacticalStore
                 -> EpisodeRegistry
                 -> TacticalGovernor
                 -> ShadowLane
                 -> LiveExecutionAdapter
                       -> the one ContractExecutor
                       -> the one positions file
                       -> the one OKX account view
```

This boundary is required by OKX net mode. A separate Tactical agent with its own `ContractExecutor` would create a second local interpretation of a symbol-keyed net position. Embedding Tactical behavior directly in the existing Main execution branches would avoid that split but would preserve the coupling that caused live/shadow divergence. The controller shares infrastructure without sharing Main strategy policy.

The legacy sidecar remains a separate owner only during shadow observation and the explicit drain. It is never an input position source for the controller.

## Module Boundaries

The implementation will create a focused `utils/tactical_v2/` package with these responsibilities:

- `models`: immutable candidate/intent and validated lifecycle event schemas.
- `store`: append-only event persistence, atomic recovery snapshot, replay, and a single serialized writer.
- `episodes`: structural epoch tracking, episode identity, reset evidence, and terminal attempt policy.
- `entry`: pure executable-price and pending-entry transition functions.
- `governor`: rolling PnL, streak cooldown, capacity, and integrity admission decisions.
- `status`: pure read-model construction and atomic snapshot writing.
- `controller`: orchestration of state transitions and adapter commands.
- `adapters`: live exchange operations and deterministic shadow execution without policy decisions.

Consumers use controller methods such as candidate receipt, price tick, technical structure update, periodic reconcile, final PnL receipt, and status projection. They do not mutate Tactical state dictionaries directly.

`ContractExecutor` remains responsible for exchange primitives, symbol normalization, account position state, order submission, owner-bound cancellation, the normalized-symbol exit lock, ledger close recording, and exchange reconciliation. Tactical-specific policy is passed into narrow new methods; it is not inferred by legacy Main drift or trailing code.

## Durable Inputs And State

### Candidate delivery

Judge will publish `tactical_candidate.v2` instead of sending a Tactical open through `trade_decision`. The payload contains the exact Shadow Tactical plan values, source shadow id, source reason, technical structure metadata, and a deterministic candidate id. The corresponding Main decision remains `hold`, so the normal Main executor cannot also open the candidate.

The topic is added to the MessageBus high-priority and important-topic sets and to `EventJournal.CRITICAL_TOPICS`. The journal write occurs before queue delivery. Each candidate includes its state namespace. On restart, the controller may replay unimported candidate messages by journal `msg_id`, filtered to the current namespace and the original 900-second intent window; importing an already-seen candidate is idempotent. A journal failure is allowed to lose an unreceived signal, but it cannot create exposure because the controller persists its own intent before admission or exchange I/O.

### Tactical persistence

`StatePaths` gains namespace-aware paths equivalent to:

```text
data/{ns_}tactical_v2_events.jsonl
data/{ns_}tactical_v2_state.json
data/{ns_}tactical_v2_status.json
data/{ns_}sidecar_retirement.json
```

The event file is authoritative and append-only. Each append is flushed and fsynced. The atomic state snapshot contains the last applied event sequence and accelerates startup; replay from the ledger remains able to rebuild the same state. The status snapshot is a read model only.

All Tactical state mutation is protected by one `asyncio.Lock` because `BaseAgent` runs message and tick loops concurrently. The lock is held only for deterministic transitions and persistence. Exchange I/O follows a three-step command protocol:

1. Under the lock, validate current state and persist `submitting`, `canceling`, or `closing` with a deterministic command id.
2. Release the lock and perform blocking exchange work through `asyncio.to_thread`.
3. Reacquire the lock, reconcile the returned and observed exchange state, then append the next transition.

Other callbacks observing an in-flight command see its persisted state and do not issue a duplicate command.

## Domain Identity

### Candidate, intent, plan, and episode

- `candidate_id` identifies one Judge emission and supports bus/journal deduplication.
- `intent_id` identifies the frozen V2 plan admitted into lifecycle evaluation.
- `plan_hash` hashes canonical symbol, side, entry, SL, full TP1, leverage, and source metadata for audit only.
- `episode_id` identifies one symbol/direction/15m structural opportunity and is the live-attempt boundary.
- `position_id` identifies confirmed exchange exposure and remains stable through close/PnL resolution.

Exact plan prices never create a new episode.

### Structural epoch

The current technical payload lacks a stable 15m closed-bar or structure identity. `MultiTechAnalyst` will add observational metadata without changing its signal thresholds:

- last closed 15m bar timestamp,
- current 15m bias and block/confirm state,
- a stable confirmed pivot or structure-break token derived from closed bars.

The episode registry keeps a monotonic epoch sequence per normalized symbol and side. A same-side repeated candidate remains in the active epoch. A new epoch may be allocated only after persisted evidence of an opposing block, neutral followed by renewed direction, or a new confirmed structure token after the prior episode terminated. Missing or stale 15m metadata cannot manufacture a reset.

The registry retains every assigned episode by `episode_id` in addition to the current symbol/side epoch. An older in-flight intent may reach a terminal exit after a newer epoch has become current; that terminal event updates only the historical episode and cannot replace or roll back the current epoch. Ledger replay reconstructs both the historical lookup and the highest monotonic current epoch.

`episode_id` is a deterministic digest of namespace, normalized symbol, side, and epoch sequence. A terminal attempt, capacity skip, same-symbol skip, miss, expiry, or close consumes the episode until reset evidence advances the sequence.

## Lifecycle State Machine

The controller records transitions rather than overwriting one status field without history.

```text
candidate_seen
  -> duplicate_episode
  -> rejected_invalid
  -> capacity_skipped
  -> same_symbol_skipped
  -> intent_ready
       -> pending_entry
       -> submitting_entry
            -> canceling_entry
            -> partial_fill
            -> filled_unverified
                 -> protected
                      -> closing
                      -> exchange_closed_pending_pnl
                           -> closed_final
                 -> integrity_halt
            -> entry_terminal
```

Terminal states never transition back to an entry-eligible state. Recovery may correct an observation, for example from unknown submission to confirmed fill, but uses a reconciliation event rather than retrying the episode.

## Entry Execution

### Executable price rule

The controller consumes `price_tick` bid/ask data. It rejects non-finite or stale executable prices. For a long, worse-side drift is `max(0, ask-entry_ref)`; for a short it is `max(0, entry_ref-bid)`. Immediate execution is permitted only when worse-side drift is at most `0.10R`, where `R=abs(entry_ref-stop_loss)`, and no pre-fill terminal boundary has been reached.

If worse-side drift exceeds `0.10R`, the only allowed order is a limit at the frozen entry. The limit has an absolute expiry of `intent_created_at + 900 seconds`; a restart does not reset its TTL. There is no limit-to-market fallback and no SL/TP translation.

For shadow execution, long limits fill only when executable ask touches or improves through the entry; short limits fill only when executable bid touches or improves through it. Long exits use executable bid and short exits use executable ask. This avoids assuming that a chart last price was executable.

### Pending terminal conditions

Before fill, frozen TP touch, frozen SL touch, opposing structure, structural reset, or TTL expiry starts cancellation. The controller does not mark the episode terminal or release its slot until exchange cancellation is confirmed and reconciliation proves zero fill. If cancellation races a fill, confirmed filled quantity wins and enters protection handling. An unknown cancellation or remaining-order state activates integrity halt and retains the slot.

Partial fill causes immediate cancellation of the remainder. Failure to prove the remainder canceled is an integrity failure because later fills could exceed locally protected quantity.

### Capacity and same-symbol exclusion

Live and shadow projections maintain separate three-slot views so the comparison lane does not consume real capacity. The live view counts pending, submitting, partial, filled, and closing exposure until exchange-flat proof. Main admission checks the controller's pending symbol registry before opening. Tactical admission checks ContractExecutor positions, Tactical pending state, and legacy sidecar ownership/exchange state. Any occupied normalized symbol terminally skips the candidate episode.

Free balance and shared account risk checks remain exchange/account gates and can produce attributed live-only mismatches. Tactical fixed margin does not change Main `MAX_TRADE_AMOUNT`.

## Exchange Identity And Protection

Tactical owner ids use the existing namespace and bot-instance owner prefix plus a deterministic truncated digest of `intent_id` and purpose. Separate purposes identify entry, TP, and SL while remaining within OKX client-id length and character limits. Restart derives the same values.

Both immediate and original-entry orders request attached full-quantity TP and SL when OKX supports that shape. After any fill, the live adapter queries attached/pending algos and proves:

- exact Tactical owner identity,
- frozen trigger prices within exchange precision,
- both TP and SL legs,
- protected quantity equal to confirmed filled quantity,
- either one combined OCO representation or two separate algo representations.

If attached protection cannot satisfy the proof, the controller cancels only proven residual orders, attempts an owner-bound close of confirmed exposure, and activates the non-expiring integrity halt. Ambiguous Main, sidecar, other-bot, or manual orders are preserved.

The position stored in the common positions file includes `strategy_owner=tactical_v2`, `exit_profile=tactical_v2`, intent/episode/plan ids, deterministic client ids, exchange algo ids, protected quantity, protection state, and frozen plan values.

## Exit Ownership

Exchange TP and SL are authoritative. The controller also owns the 90-minute max-hold timer and reconciliation. All local Tactical close commands use the existing normalized-symbol exit lock and reduce-only exchange behavior.

Before a local close, the adapter re-fetches exchange position quantity and protection. If exchange TP/SL already flattened the symbol, it records the external close and does not send another order. If quantity remains, it closes only the proven Tactical net position and cleans only proven Tactical protection.

Generic Main exit paths must branch on `strategy_owner`, not merely `track`:

- Position Analyst emits no add, reduce, or close for V2.
- Main early review is skipped for V2.
- Main break-even, profit trailing, partial TP, legacy `tactical_invalidated`, and weakened/no-progress are skipped for V2.
- The generic root `_update_trailing` does not implement V2 max hold or TP.
- Global drawdown, flash move, protection integrity, manual emergency, and exchange safety closes remain allowed and are recorded as `risk_forced`.

Legacy sidecar and Tactical V1 positions retain their captured legacy exit behavior during drain; they are not silently migrated by the V2 owner guard.

## PnL And Tactical Governor

The controller, not `PortfolioRiskGuard` or Judge file reads, is the V2 admission authority. `PortfolioRiskGuard` retains global account protections but its legacy Tactical daily/quality/concurrency logic is disabled for V2.

Final resolution handling uses two keys:

- `resolution_id` deduplicates repeated delivery of the same final resolution.
- stable close identity, preferably `position_id` and otherwise `entry_request_id`, identifies corrections to one closed episode.

For each close identity the store retains only the latest final PnL truth. A new resolution applies `new_final-old_final` to rolling accounting. After every correction the governor deterministically rebuilds its rolling 24-hour sum and ordered final-outcome projection from the ledger. Pending, estimated, mismatch, or non-finite PnL never enters the governor.

Final corrections also form a durable producer outbox. The producer persists the final before publication, appends an acknowledgement after the bus accepts it, and replays unacknowledged finals on restart. Governor, Judge, and Reviewer use `resolution_id` deduplication, so replay cannot double-apply risk or review state. A crash after downstream receipt but before the acknowledgement remains at-least-once and may repeat one Telegram notification; durable cross-restart Telegram consumer offsets are explicitly outside this design.

All final V2 closes, including `risk_forced`, affect the rolling loss and loss-streak risk view while retaining separate performance attribution. Legacy sidecar PnL is reconciled and archived but is not imported as a V2 episode.

When the rolling sum is `<= -15U`, only new Tactical admission pauses. Existing pending cancellation, protection, reconciliation, and position exits continue. The pause clears automatically when time-window eviction or a correction lifts the sum above the threshold.

Three consecutive final losses append a cooldown event, reset the active streak, and pause admission for 60 minutes. A zero or profitable final outcome resets an unconsumed streak. A later correction changes future reconstructed streak state but does not revoke a cooldown already issued as an operational risk decision; that cooldown ends at its recorded deadline.

Integrity halt is orthogonal to timed and rolling pauses. It has no deadline and clears only through a persisted successful reconciliation event.

Entry submission recovery distinguishes an exact order found, a successful exact lookup with no visible order, and a lookup error. A temporarily empty result before the persisted visibility deadline stays in `reconciling_entry` and is checked again by the periodic controller tick without another submit. Query errors and deadline exhaustion fail closed. The controller may automatically revisit only `entry_reconciliation_unknown` and `entry_cancel_unproven`; it clears either halt only from complete owner/order/position/quantity/protection proof. Deferred cancellation persists its original terminal reason so an observed-open order is canceled again instead of returning to ordinary pending entry.

## Shadow/Live Parity

One intent feeds a common transition reducer with lane-specific adapters:

- shadow lane consumes executable ticks and simulated order state,
- live lane consumes actual account capacity, order responses, fills, and protection state.

In shadow-only rollout, the live adapter is disabled and cannot submit exchange commands. In live mode, the shadow lane continues for per-intent comparison. Adapter outcomes are recorded with `lane=shadow|live`; common policy transitions have the same reason vocabulary.

Mismatch classification includes exchange fill, partial fill, account capacity, same-symbol account exposure, order rejection, protection failure, shared system risk, stale/missing tick, and process availability. Raw row count is never a performance denominator. Only deduplicated episodes with executable fills and final PnL count as filled outcomes.

The authoritative live governor consumes live final outcomes only. A shadow governor projection may be reported for parity but cannot pause or resume live admission.

## Sidecar Drain And Cutover

The existing uncommitted resident-run sidecar change is preserved and verified as part of this change. It is not rewritten during design.

Cutover uses these states:

```text
sidecar_admission_on + v2_shadow
  -> sidecar_admission_off + sidecar_monitoring
  -> sidecar_draining
  -> sidecar_flat_reconciled
  -> sidecar_state_archived
  -> v2_live_admission_on
```

Admission stop is persisted before event polling can open another sidecar trade. Monitoring stays resident until pending entries are canceled where ownership is proven, proven exposure is exchange-flat, protection ambiguity is resolved, and pending final PnL is resolved or explicitly documented. Unknown exchange state never counts as flat.

The drain command/report records every owner, pending order, exchange position, protection object, PnL resolution, exception, and archive hash. V2 requested-live mode fails closed when the retirement proof is missing, stale, malformed, or reports unresolved objects.

Rollback disables new V2 intents and cancels V2 pending entries where ownership is proven. Filled V2 positions remain under exchange protection and controller monitoring until flat. Rollback never auto-enables sidecar admission.

## Telegram Status

The controller writes the status snapshot after material transitions and at least every 30 seconds. `TelegramNotifier` is the only formatter and reads no Tactical fields from legacy risk-guard state after V2 activation.

The Tactical section includes mode/version, configured `100U x 3`, active/pending/free slots and symbols, rolling final PnL and threshold, streak and cooldown, integrity state, episode outcome counts, protection/reconciliation health, parity mismatches, and `updated_at`.

The default stale threshold is 90 seconds. Missing, malformed, stale, or non-finite data displays `STALE` or unknown and never implies healthy state. Global halt, per-symbol halt, and Tactical admission/integrity state remain separate lines. Telegram cannot mutate controller or governor state.

## Configuration And Compatibility

New resolved configuration uses explicit V2 names and validated types, including mode, fixed margin, slot limit, max leverage, `0.10R`, 900-second entry TTL, 90-minute max hold, `-15U` rolling threshold, three-loss count, 60-minute cooldown, and 90-second status freshness.

`off`, `shadow`, and `live` are the only deployment modes. Admission pause and integrity halt are state, not extra configuration modes. Live mode requires production namespace, bot owner identity, and valid sidecar retirement proof.

Legacy Tactical flags remain readable during migration but cannot cause both V1 and V2 opens. Startup logs the resolved owner, mode, sizing, slot, risk, and cutover-gate values. Unsupported or contradictory live configuration fails closed to no new Tactical admission.

## Verification Strategy

### Pure state tests

Use a fake clock and table-driven reducers for intent validation, episode reset, exact `0.10R` boundaries, favorable prices, TP/SL-before-entry, TTL, capacity terminality, correction deltas, rolling eviction, streak consumption, and integrity reconciliation.

### Exchange adapter tests

Use a stateful fake OKX adapter that models deterministic client ids, accepted-but-response-lost orders, partial fills, cancel/fill races, combined and separate OCO shapes, protection lookup failure, external TP/SL, and reduce-only close races.

Inject failure after every durable/exchange boundary:

- before and after entry submission,
- after partial fill and before remainder cancellation,
- before and after protection verification,
- before and after local close,
- after exchange close and before local persistence,
- before pending PnL and after final/correction delivery.

Every restart assertion checks no duplicate entry, no duplicate close, no stale retry, no premature slot release, and either verified protection or integrity halt plus safe-close attempt.

### Integration tests

Run Judge -> MessageBus -> MultiExecutor candidate flow with Main and Tactical pending/positions on overlapping symbols. Publish Position Analyst and technical invalidation messages against V2 positions and prove no Main strategy action reaches `ContractExecutor`. Verify global safety still reaches the owner-bound close path.

Test status snapshots and Telegram formatting for healthy, rolling paused, timed paused, integrity halted, stale, missing, malformed, and non-finite states.

### Historical and cloud gates

Replay the reproduced window by intent and transition rather than aggregate rows. The gate requires one attempt per episode, no market chase beyond `0.10R`, no TP-before-entry fill, full TP1, and explicit mismatch attribution.

After local regression passes, deploy V2 shadow-only for at least 24 hours with executable bid/ask ticks. Only after fresh status, deterministic recovery, parity classification, and a verified sidecar drain may live admission switch directly to `100U x 3`.

The first live cohort is rejected if any duplicate order, stale chase, Main strategy exit, unprotected fill, unexplained owner ambiguity, or unclassified shadow/live mismatch is observed.

## Implementation Order

1. Add pure models, store, episode registry, governor, and replay fixtures.
2. Add candidate publishing and controller integration in shadow-only mode.
3. Add bid/ask shadow lifecycle and historical parity tooling.
4. Add deterministic live entry/protection/reconciliation adapter behind disabled live mode.
5. Add V2 exit isolation and final-PnL governor wiring.
6. Add status snapshot and Telegram formatting.
7. Add sidecar admission stop, drain report, archive proof, and live cutover gate.
8. Run failure injection, repository regression, 24-hour cloud shadow, drain, then live cohort gates.

## Rejected Alternatives

### Separate Tactical BaseAgent with its own executor

Rejected because two `ContractExecutor` instances would read and write symbol-keyed local state around one OKX net position. Reconciliation and protection cleanup could disagree about ownership even if both processes used tags.

### Extend legacy Main Tactical V1 branches

Rejected because the existing branches contain percentage drift, partial TP, post-fill thesis exits, legacy PortfolioRiskGuard state, and shared trailing behavior. Conditional guards throughout those branches would not create one auditable lifecycle.

### Promote the sidecar itself

Rejected because file tailing and an independent process preserve the population, position-state, and process-ownership split. The sidecar remains valuable only as a bounded drain owner for its existing exposure.

## Spec Review

The design introduces no new user-facing capability beyond the current OpenSpec artifacts and requires no additional delta spec patch. There are no placeholders or unresolved behavioral choices. Real/fake OKX verification may select combined or separate algo representation internally, but both implement the same specified ownership contract.
