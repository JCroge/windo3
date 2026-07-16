## 1. Shadow Event Mirror Sidecar

- [x] 1.1 Add a sidecar runner that tails `data/rejected_signal_events.jsonl` and processes only new events after its start watermark by default.
- [x] 1.2 Filter eligible events to `rejected_plan_created` records with Tactical identity (`track=tactical` or `exit_profile=tactical_v1`).
- [x] 1.3 Persist durable sidecar state with `started_at`, `stop_at`, last processed event offset/id, and per-shadow id execution status.
- [x] 1.4 Write sidecar audit events and sidecar ownership records to separate files.

## 2. Plan Mapping and Execution

- [x] 2.1 Map shadow record fields directly into a live plan: symbol, side, entry, SL, TP, leverage, Tactical max hold, exit profile, source, and attribution metadata.
- [x] 2.2 Bypass Main Judge, CandidateRanker, Tactical RR/EV/cost gates, Tactical quality gates, slot gates, and Tactical circuit admission gates for sidecar admission.
- [x] 2.3 Keep mechanical fail-closed checks for malformed fields, invalid SL side, missing SL/TP, OKX posMode unknown, max trade amount, effective balance cap, min-size/precision rejection, balance shortage, slippage/depth failure, and protective SL verification failure.
- [x] 2.4 Ensure duplicate shadow ids do not create duplicate live orders.
- [x] 2.5 Add a sidecar active exposure cap using the configured `MAX_CONCURRENT_POSITIONS` default.

## 3. Isolation and Ownership

- [x] 3.1 Add a supported sidecar namespace or explicit state paths for sidecar positions, risk state, halt state, live order events, and lifecycle files.
- [x] 3.2 Use a distinct sidecar `BOT_INSTANCE_ID` or client-order prefix for sidecar-owned orders.
- [x] 3.3 Add a sidecar ownership registry for shadow id, symbol, side, amount, order id, entry clOrdId, SL algo id, and SL algo clOrdId.
- [x] 3.4 Patch Main `sync_positions()` so it does not backfill sidecar-owned positions into Main state.
- [x] 3.5 Patch Main OKX algo migration so it never cancels, replaces, or adopts foreign owner-tag SL algos.
- [x] 3.6 Add a same-account same-symbol guard to avoid sidecar/Main exposure aggregation that cannot be split.

## 4. Stop and 24-Hour Operation

- [x] 4.1 Add a 24-hour duration/stop time so the sidecar stops accepting new events automatically.
- [x] 4.2 Add a stop command/runbook that cancels sidecar-owned pending orders and closes sidecar-owned open exposure when ownership can be proven.
- [x] 4.3 Deploy the Main owner-ignore safety patch, then start the sidecar as a separate cloud process.
- [x] 4.4 Verify Main does not adopt sidecar positions or cancel sidecar SL algos during sync.

## 5. Verification

- [x] 5.1 Add tests for Tactical event filtering and non-Tactical event ignore behavior.
- [x] 5.2 Add tests for shadow-record-to-live-plan mapping fidelity.
- [x] 5.3 Add tests for watermark/idempotency duplicate prevention.
- [x] 5.4 Add tests for missing-field and invalid-SL fail-closed behavior.
- [x] 5.5 Add tests proving mechanical hard limits are enforced by the sidecar open path.
- [x] 5.6 Add tests proving Main sync skips sidecar-owned positions.
- [x] 5.7 Add tests proving Main migration does not cancel or adopt foreign owner-tag SL algos.
- [x] 5.8 Run OpenSpec validation for `promote-shadow-tactical-live-48h`.
