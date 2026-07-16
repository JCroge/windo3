## Context

Shadow Tactical sidecar currently does one thing well: it tails `rejected_plan_created` events, maps them into a Tactical shadow plan, and opens positions through `ContractExecutor.open_sidecar_plan()`. After that, the sidecar stops caring. There is no polling pass over open sidecar-owned positions, so TP1/TP2, invalidation, weakened-thesis, and max-hold exits can sit open until a human intervenes.

The Tactical exit rules already exist in `executor.py` and are consumed by the main executor path. The missing piece is a sidecar consumer of that same lifecycle. The ONDO incident also showed that the sidecar symbol path is not canonicalized cleanly, which makes ownership proof and cleanup brittle.

Target shape:

```text
shadow event stream
  -> open sidecar-owned tactical position
  -> persist ownership record
  -> poll open sidecar-owned positions
     -> evaluate tactical exit intent
     -> reduce_position / close_position
  -> stop drains proven open rows only
```

Constraints:
- keep this sidecar-only
- do not change main strategy classification or main execution semantics
- reuse the existing exit lock / residual protection lifecycle
- preserve old owner rows where possible

## Goals / Non-Goals

**Goals:**
- Make the sidecar actively manage open Tactical shadow positions after entry.
- Reuse the existing Tactical exit semantics rather than inventing a second rule set.
- Canonicalize ownership vs execution symbols so the sidecar can open, monitor, and stop the same position consistently.
- Keep sidecar shutdown and cleanup proven-only, so unrelated positions are untouched.

**Non-Goals:**
- No change to main-track vs tactical-track classification.
- No new risk governor or portfolio policy.
- No rewrite of the main executor's trade lifecycle.
- No new service process or message bus topic.

## Decisions

### 1. Extract a shared Tactical exit evaluator

The sidecar should not duplicate the Tactical exit conditions inline. Instead, `executor.py` should expose a small Tactical exit evaluator that takes the current position, price, and timestamp and returns an exit intent such as `partial_tp_1`, `partial_tp_2`, `tactical_invalidated`, `tactical_weakened_no_progress`, or `tactical_max_hold`.

Why this over duplicating logic in `scripts/shadow_tactical_live_sidecar.py`:
- one source of truth for Tactical behavior
- easier unit testing
- main loop and sidecar remain aligned when Tactical thresholds change

The current `check_stop_loss_take_profit()` entry point can stay as a wrapper so existing callers keep working.

### 2. Split identity from execution symbol

Sidecar ownership should be keyed by the internal symbol, while execution should use the exchange swap symbol. The existing executor `symbol` field should continue to mean the exchange execution symbol so the rest of `ContractExecutor` stays stable. For OKX this means resolving the plan symbol through `utils.symbol.to_okx_inst()` before order submission.

Why this over keeping the raw plan symbol:
- raw internal symbols can resolve to the wrong instrument family
- the ONDO case shows that symbol ambiguity leaks into both open and stop paths
- ownership proof becomes explicit when internal and exchange symbols are both persisted

Proposed sidecar state split:
- `internal_symbol`: sidecar identity and audit symbol
- `exchange_symbol`: order/market-data symbol used by `ContractExecutor`

Legacy owner rows can be read by deriving both fields from the stored symbol when one is missing.

### 3. Add the monitor pass to the existing sidecar loop

The sidecar already has a long-running `cmd_run()` loop. The monitor should be an extra pass in that loop, not a second daemon or thread.

Why this over a separate worker:
- no extra lifecycle or shutdown handling
- one poll cadence already exists
- fewer moving pieces to keep in sync with the owner registry

The loop should:
1. ingest new shadow events
2. scan open sidecar-owned rows
3. prove each live position
4. evaluate exit intent
5. call `reduce_position()` or `close_position()`
6. persist the updated owner row and audit event

### 4. Reuse existing exit primitives

Sidecar exit actions should call the shared executor primitives:
- `reduce_position(..., tp_advance=1/2)` for Tactical partial exits
- `close_position(...)` for invalidation, weakened/no-progress, max hold, and cleanup

Why this over bespoke sidecar order code:
- the executor already owns exit lock serialization
- protective SL replacement and residual cleanup stay centralized
- reduce/close results are already ledger-aware

### 5. Make ownership proof strict

The monitor and stop path should only act on positions that are proven sidecar-owned. If a registry row cannot be matched to a live sidecar-owned position, the sidecar should skip it and record the skip.

Why this over best-effort closing:
- avoids touching main positions
- avoids guessing when symbol history is incomplete
- keeps the sidecar fail-closed instead of improvising

## Risks / Trade-offs

- [More price fetches per poll] -> Keep monitoring bounded to open sidecar rows only and reuse the existing poll interval.
- [Refactor risk in Tactical exit logic] -> Keep the old wrapper behavior unchanged and cover the extracted evaluator with fixtures.
- [Legacy owner-row ambiguity] -> Derive missing symbol fields on read, then rewrite them on the next save.
- [Sidecar and main symbol conventions diverge] -> Keep internal symbol for identity and exchange symbol for execution, and document that split in tests.

## Migration Plan

1. Ship the sidecar changes with backward-compatible owner-row loading.
2. On new opens, persist both `internal_symbol` and `exchange_symbol`.
3. When legacy rows are loaded, derive missing symbols and continue monitoring.
4. Rollback is straightforward: stop the sidecar or revert the change; the main process remains untouched.

## Testing

- Unit test symbol resolution for internal Tactical symbols to exchange swap symbols.
- Unit test the shared Tactical exit evaluator for TP1, TP2, invalidation, weakened/no-progress, and max-hold.
- Integration test the sidecar run loop with no new events but an open position.
- Regression test the ONDO-style case to ensure the sidecar opens and monitors the swap instrument, not spot.
- Regression test stop handling so unproven positions are skipped and proven positions close.

## Open Questions

None. The existing Tactical exit contract and the current sidecar poll loop are sufficient for this change.
