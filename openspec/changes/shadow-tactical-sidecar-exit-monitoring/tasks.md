## 1. Symbol and ownership plumbing

- [x] 1.1 Add internal and exchange symbol fields to sidecar owner records with backward-compatible loading for legacy rows.
- [x] 1.2 Resolve sidecar opens to the exchange swap symbol before order submission while preserving the internal symbol for ownership and audit.
- [x] 1.3 Update same-symbol exposure checks to compare canonical internal symbols so open and stop guards stay stable.

## 2. Shared Tactical exit evaluator

- [x] 2.1 Extract a reusable Tactical exit decision helper from `executor.py` and keep `check_stop_loss_take_profit()` as a wrapper.
- [x] 2.2 Route Tactical TP1, TP2, invalidation, weakened-no-progress, and max-hold through the shared helper.
- [x] 2.3 Add unit tests for Tactical exit intent, partial-reduce sizing, and max-hold close reasons.

## 3. Sidecar monitor loop

- [ ] 3.1 Add a per-poll scan of open sidecar-owned positions in `scripts/shadow_tactical_live_sidecar.py`.
- [ ] 3.2 Prove ownership, fetch price, evaluate the Tactical exit intent, and call `reduce_position()` or `close_position()` accordingly.
- [ ] 3.3 Record audit events and update owner status for partial exits, closes, skips, and failures.

## 4. Stop, migration, and regression coverage

- [ ] 4.1 Refactor `cmd_stop` to reuse the same proven-owner drain path as the monitor.
- [ ] 4.2 Add regression tests for legacy owner rows and ONDO-style internal symbol opens.
- [ ] 4.3 Verify the change with sidecar idle-loop and stop-path integration tests.
