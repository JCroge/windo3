---
comet_change: shadow-tactical-sidecar-exit-monitoring
role: technical-design
canonical_spec: openspec
---

# Shadow Tactical Sidecar Exit Monitoring Technical Design

## Context

Shadow Tactical sidecar consumes Tactical shadow events and opens live sidecar positions through `ContractExecutor.open_sidecar_plan()`. The missing runtime piece is post-entry monitoring: once opened, sidecar positions are not polled for Tactical TP, thesis invalidation, weakened-no-progress, or max-hold exits. Existing Tactical exit semantics already live in `executor.py`; this change makes sidecar consume the same semantics instead of inventing a second exit controller.

The ONDO manual TP case also exposed a symbol-boundary issue. The sidecar currently carries the raw shadow symbol into execution and ownership state. That makes it too easy to confuse internal identity (`BASE-USDT`) with exchange execution instruments (`BASE-USDT-SWAP`), which then weakens stop/monitor ownership proof.

## Technical Approach

### Symbol Identity

Sidecar state will distinguish:

- `internal_symbol`: canonical identity for registry, audit, and cross-process matching.
- `exchange_symbol`: symbol used by `ContractExecutor` for ticker/order calls.

The executor `position["symbol"]` field remains the exchange symbol so existing executor internals stay stable. Owner rows persist both symbols. Legacy owner rows are normalized on load from the stored `symbol` value.

### Tactical Exit Evaluation

`executor.py` will expose a reusable Tactical exit evaluator behind `check_stop_loss_take_profit()`. The wrapper keeps legacy callers unchanged, while the sidecar can request the same trigger values:

- `tactical_tp1`
- `partial_tp_2` or Tactical TP2 equivalent when the second level is reached
- `tactical_invalidated`
- `tactical_weakened_no_progress`
- `tactical_max_hold`
- existing stop-loss and price-fetch protection triggers

The sidecar will not place custom reduce/close orders. It routes exit intents into existing executor primitives:

- TP1: `reduce_position(symbol, 0.5, tp_advance=1, action_kind="sidecar_tactical_tp1")`
- TP2: `reduce_position(symbol, 0.25, tp_advance=2, action_kind="sidecar_tactical_tp2")`
- invalidated/weakened/max-hold/stop: `close_position(symbol, action_kind=<reason>)`

This keeps exit locks, ledger updates, residual SL replacement, and protective cleanup centralized.

### Monitor Loop

`scripts/shadow_tactical_live_sidecar.py::cmd_run()` will add a monitor pass after ingesting new shadow events:

```text
while active:
  ingest new events
  scan owner rows with status=open
  prove row matches local executor position
  evaluate Tactical exit trigger
  execute reduce/close if needed
  persist state and audit event
```

The scan is intentionally synchronous and bounded to sidecar-owned rows. No extra daemon, thread, or main-process coupling is needed.

### Ownership Proof

Every monitor or stop action must prove ownership before touching exposure:

- row status is `open`
- row `shadow_id` matches local position `shadow_id`
- row side matches position side
- row exchange symbol matches the executor position key or position `symbol`
- position source is `shadow_tactical_live`

If proof fails, sidecar records a skip event and leaves the position untouched. This is more conservative than best-effort closing because the user explicitly wants the sidecar isolated from main.

## Risks and Mitigations

- Symbol migration ambiguity -> keep backward-compatible derivation and rewrite owner rows on save.
- Refactoring Tactical exit logic -> leave `check_stop_loss_take_profit()` behavior intact and add regression tests around extracted helper output.
- Duplicate exits during polling -> reuse executor exit locks and action ids.
- Sidecar quietly misses a close -> audit every monitor close, partial reduce, skip, and failure.

## Test Strategy

- Unit-test symbol canonicalization for internal, ccxt, and OKX swap-like symbols.
- Unit-test owner registry migration and ownership proof for legacy and new rows.
- Unit-test Tactical exit evaluator for TP1, TP2, invalidated, weakened-no-progress, and max-hold.
- Integration-test the sidecar idle loop: no new shadow events, open sidecar position, monitor still triggers exit.
- Regression-test stop behavior: proven rows close, unproven rows skip.

