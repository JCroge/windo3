## Why

`ContractExecutor.sync_positions()` (`executor.py:2636`) calls `self.exchange.fetch_positions()` with **no in-cycle retry**. OKX's `/account/positions` REST endpoint occasionally returns a transient network/timeout error — **9 times in 16h** of continuous run (`okx GET https://www.okx.com/api/v5/account/positions`). Each blip:
- aborts the *entire* sync cycle (the loop's only recovery is the next periodic tick),
- is logged at **ERROR** (alarming / triggers the failed-task & ops noise we just built), and
- captures only `{e}` — the **exception type is lost**, so timeout vs rate-limit vs pool-exhaustion is undiagnosable.

The failure is benign today (it keeps local positions and recovers next cycle), but a single transient hiccup shouldn't escalate to ERROR or skip a whole sync. This is a small resilience hardening of one function.

## What Changes

- Add a synchronous bounded-retry helper for `fetch_positions()`: retry on `ccxt.NetworkError` (the base class of `RequestTimeout` / `ExchangeNotAvailable` / `DDoSProtection`) up to 3 attempts with short `time.sleep` backoff (0.5s, 1.0s). Safe because `sync_positions` runs via `asyncio.to_thread` (worker thread, not the event loop).
- Log each transient retry at **WARNING** with the exception **type name**; only a fully-exhausted retry (or a non-transient exception) reaches the existing outer `except` → **ERROR**, now also including `type(e).__name__`.
- Behavior on terminal failure is unchanged (keep local positions, `_last_sync_result=[]`, return copy).

## Capabilities

### New Capabilities
- `position-sync-resilience`: `sync_positions` must tolerate transient OKX network errors via bounded retry, escalating to ERROR only after retries are exhausted.

### Modified Capabilities
<!-- none -->

## Impact

- **Modified**: `executor.py` (`sync_positions` + new private helper `_fetch_positions_with_retry`). `ccxt` and `time` already imported.
- **Test**: new `test_position_sync_retry.py`.
- **Behavioral**: fewer spurious ERROR logs; transient blips absorbed silently (WARNING); ERROR carries exception type.
- **Non-goals**: do NOT change reconciliation/algo-migration logic, other `fetch_*` calls, the ccxt connection-pool config, or add new config keys (constants hardcoded). 2 files, single function — hotfix scope.
