## 1. Shadow Event Mirror Sidecar

- [ ] 1.1 Add a sidecar runner that tails `data/rejected_signal_events.jsonl` and processes only new events after its start watermark by default.
- [ ] 1.2 Filter eligible events to `rejected_plan_created` records with Tactical identity (`track=tactical` or `exit_profile=tactical_v1`).
- [ ] 1.3 Persist durable sidecar state with `started_at`, `stop_at`, last processed event offset/id, and per-shadow id execution status.
- [ ] 1.4 Write sidecar audit events and sidecar ownership records to separate files.

## 2. Plan Mapping and Execution

- [ ] 2.1 Map shadow record fields directly into a live plan: symbol, side, entry, SL, TP, leverage, Tactical max hold, exit profile, source, and attribution metadata.
- [ ] 2.2 Bypass Main Judge, CandidateRanker, Tactical RR/EV/cost gates, Tactical quality gates, slot gates, and Tactical circuit admission gates for sidecar admission.
- [ ] 2.3 Keep mechanical fail-closed checks for malformed fields, invalid SL side, missing SL/TP, OKX posMode unknown, max trade amount, effective balance cap, min-size/precision rejection, balance shortage, slippage/depth failure, and protective SL verification failure.
- [ ] 2.4 Ensure duplicate shadow ids do not create duplicate live orders.
- [ ] 2.5 Add a sidecar active exposure cap using the configured `MAX_CONCURRENT_POSITIONS` default.

## 3. Isolation and Ownership

- [ ] 3.1 Add a supported sidecar namespace or explicit state paths for sidecar positions, risk state, halt state, live order events, and lifecycle files.
- [ ] 3.2 Use a distinct sidecar `BOT_INSTANCE_ID` or client-order prefix for sidecar-owned orders.
- [ ] 3.3 Add a sidecar ownership registry for shadow id, symbol, side, amount, order id, entry clOrdId, SL algo id, and SL algo clOrdId.
- [ ] 3.4 Patch Main `sync_positions()` so it does not backfill sidecar-owned positions into Main state.
- [ ] 3.5 Patch Main OKX algo migration so it never cancels, replaces, or adopts foreign owner-tag SL algos.
- [ ] 3.6 Add a same-account same-symbol guard to avoid sidecar/Main exposure aggregation that cannot be split.

## 4. Stop and 24-Hour Operation

- [ ] 4.1 Add a 24-hour duration/stop time so the sidecar stops accepting new events automatically.
- [ ] 4.2 Add a stop command/runbook that cancels sidecar-owned pending orders and closes sidecar-owned open exposure when ownership can be proven.
- [ ] 4.3 Deploy the Main owner-ignore safety patch, then start the sidecar as a separate cloud process.
- [ ] 4.4 Verify Main does not adopt sidecar positions or cancel sidecar SL algos during sync.

## 5. Verification

- [ ] 5.1 Add tests for Tactical event filtering and non-Tactical event ignore behavior.
- [ ] 5.2 Add tests for shadow-record-to-live-plan mapping fidelity.
- [ ] 5.3 Add tests for watermark/idempotency duplicate prevention.
- [ ] 5.4 Add tests for missing-field and invalid-SL fail-closed behavior.
- [ ] 5.5 Add tests proving mechanical hard limits are enforced by the sidecar open path.
- [ ] 5.6 Add tests proving Main sync skips sidecar-owned positions.
- [ ] 5.7 Add tests proving Main migration does not cancel or adopt foreign owner-tag SL algos.
- [ ] 5.8 Run OpenSpec validation for `promote-shadow-tactical-live-48h`.
