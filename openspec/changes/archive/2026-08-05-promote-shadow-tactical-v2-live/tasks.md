## 1. Baseline And Fixtures

- [x] 1.1 Capture the reproduced live-versus-shadow window as deterministic intent, market-tick, fill, exit, and final-PnL fixtures.
- [x] 1.2 Add episode fixtures covering repeated rows, opposing-block reset, neutral-then-renewed direction, confirmed new structure, and slot-release non-backfill.
- [x] 1.3 Add executable bid/ask fixtures for immediate `0.10R`, original-entry wait, TP-before-entry, SL-before-entry, expiry, and partial-fill cases.

## 2. Tactical V2 Durable Model

- [x] 2.1 Implement the immutable versioned Tactical intent schema, validation, canonical symbol handling, plan hash, and deterministic intent/order identities.
- [x] 2.2 Implement the persisted structural episode registry, reset evidence, one-attempt terminal outcomes, and restart recovery.
- [x] 2.3 Implement a namespaced append-only Tactical lifecycle/PnL event ledger with atomic recovery snapshots and correction-safe `resolution_id` deduplication.
- [x] 2.4 Implement the atomic Tactical operational status snapshot derived from durable state, including transition-triggered and periodic refresh.

## 3. Signal And Entry Integration

- [x] 3.1 Change Judge Tactical promotion to emit the exact eligible Shadow plan into the canonical V2 intent factory without later Main mutation.
- [x] 3.2 Integrate a Tactical V2 engine into the Main process lifecycle with explicit shadow-only, live, admission-disabled, and integrity-halted modes.
- [x] 3.3 Implement fixed `100U`, maximum 5x leverage, three active-or-pending Tactical slots, and independent Main slot accounting.
- [x] 3.4 Implement same-symbol Main/Tactical/pending exposure rejection and terminal capacity skips without queueing or slot-release backfill.
- [x] 3.5 Implement executable ask/bid `0.10R` immediate admission and one original-entry limit with a 900-second terminal TTL and no market fallback.
- [x] 3.6 Implement pre-fill TP/SL/structure/expiry cancellation, partial-fill remainder cancellation, and terminal no-retry episode outcomes.
- [x] 3.7 Persist `submitting` before exchange I/O and reconcile deterministic client ids after crash before permitting any retry or new admission.
- [x] 3.8 Add durable entry-visibility grace, periodic proof-based self-heal, and deferred-cancel reason recovery without entry resubmission.

## 4. Tactical Risk Governor

- [x] 4.1 Replace natural-day Tactical accounting with one persistent rolling 24-hour final-PnL governor using a `-15U` new-admission threshold.
- [x] 4.2 Implement three consecutive final losses as a consumed/reset streak with a persisted 60-minute new-admission pause.
- [x] 4.3 Implement non-expiring execution/protection/ownership integrity halt and proof-based reconciliation clearing.
- [x] 4.4 Route all Tactical admission decisions through the new governor while preserving management and exits for already filled positions.
- [x] 4.5 Remove or disable legacy quality-window, volatility-dependent concurrency, and duplicate Judge/file-read Tactical admission authorities for V2.

## 5. Protection And Exit Ownership

- [x] 5.1 Extend OKX owner-tag generation to deterministic Tactical entry, TP, and SL client identities within exchange length/format constraints.
- [x] 5.2 Install and verify full-quantity exchange TP plus SL after every confirmed fill, supporting combined-OCO and separate-algo response shapes.
- [x] 5.3 Fail closed on incomplete protection by cleaning only proven orders, safely closing confirmed unprotected exposure, and activating integrity halt.
- [x] 5.4 Implement Tactical V2 full TP1, full SL, and 90-minute max-hold exits through the normalized-symbol exit lock.
- [x] 5.5 Reconcile exchange fills, max-hold, global safety, cleanup, restart, and final-PnL publication idempotently under Tactical ownership.
- [x] 5.6 Persist final-PnL publication as a retryable outbox and serialize concurrent routing so governor/review consumers apply one resolution once.

## 6. Main Strategy Isolation

- [x] 6.1 Guard Position Analyst close/reduce/add paths so they cannot act on `strategy_owner=tactical_v2` positions.
- [x] 6.2 Guard Main break-even, profit trailing, partial-TP, thesis invalidation, and weakened/no-progress paths from Tactical V2 positions.
- [x] 6.3 Preserve global drawdown, flash-move, protection-integrity, manual emergency, and exchange safety authority with `risk_forced` attribution.
- [x] 6.4 Propagate Tactical V2 owner, intent, episode, plan, admission, protection, and final close metadata through execution, Reviewer, and PnL events.

## 7. Shared Shadow And Replay Parity

- [x] 7.1 Make shadow and live adapters consume the same episode, entry, and exit state machine, differing only at exchange I/O boundaries.
- [x] 7.2 Require executable-price touch for shadow fills and report non-filled terminal intents separately from filled performance.
- [x] 7.3 Add per-intent shadow/live transition comparison with attributed mismatch categories and deduplicated episode metrics.
- [x] 7.4 Replay the historical fixture and verify zero duplicate attempts, zero stale chase fills, full-TP1 parity, and classified execution variance.

## 8. Telegram Operational Status

- [x] 8.1 Extend `/status` to render Tactical V2 mode/version, `100U x 3`, slots/symbols, rolling PnL, streak/circuit, episode outcomes, protection, and parity.
- [x] 8.2 Enforce snapshot-only Tactical status reads, 90-second default freshness, and safe `STALE`/unknown rendering for missing, malformed, or non-finite data.
- [x] 8.3 Keep global halt, per-symbol halt, and Tactical admission/integrity circuits visually and semantically distinct in Telegram tests.

## 9. Sidecar Drain And Cutover

- [x] 9.1 Preserve and verify the existing resident-until-manual-stop sidecar CLI changes and their tests without rewriting unrelated behavior.
- [x] 9.2 Add an admission-stop mode that leaves owner-bound sidecar monitoring and proven-exposure exits running during drain.
- [x] 9.3 Implement a drain report covering pending entries, owners, exchange exposure, protection ambiguity, final PnL, and documented exceptions.
- [x] 9.4 Block Tactical V2 live admission until the drain barrier passes and prevent legacy sidecar rows or positions from being adopted as V2.
- [x] 9.5 Archive the final sidecar state and cutover evidence, disable sidecar admission, and ensure V2 rollback cannot auto-reactivate it.

## 10. Verification And Rollout

- [x] 10.1 Add unit tests for intent immutability, episode identity/reset, R drift boundaries, capacity terminality, rolling/corrected PnL, streak consumption, and integrity halt.
- [x] 10.2 Add integration tests for message-bus ownership isolation, three pending/active slots, Main-action rejection, full exits, and final metadata propagation.
- [x] 10.3 Add failure-injection tests for each crash window around entry, partial fill, protection install, exchange TP/SL, local close, cleanup, and PnL correction.
- [x] 10.4 Run the focused Tactical, sidecar, owner-isolation, PnL, TG status, and replay suites plus the repository regression suite.
- [x] 10.5 Deploy V2 in cloud shadow-only mode and collect at least 24 hours of executable-price lifecycle, freshness, protection simulation, and parity evidence.
- [x] 10.6 Stop sidecar admission, complete and archive the proven-owner drain, then enable live Tactical V2 at fixed `100U x 3` only after every cutover gate passes.
- [x] 10.7 Verify the first live cohort has no duplicate orders, stale chase, Main strategy exits, or unprotected fills and that every shadow/live mismatch is classified.
- [x] 10.8 Reconcile the full dirty worktree, update operational documentation/configuration, and submit the preserved sidecar changes together with the completed Comet implementation.
