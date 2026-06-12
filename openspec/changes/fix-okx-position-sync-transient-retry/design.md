## Context

`sync_positions()` (`executor.py:2636-2735`) is the exchange→local position reconciliation entry. Its only network call is `self.exchange.fetch_positions()` (line 2639), unguarded by retry. The whole body is wrapped in one `try/except Exception` (line 2732) that logs `仓位同步失败: {e}` at ERROR, sets `_last_sync_result=[]`, and returns `self.positions.copy()`. Called via `await asyncio.to_thread(self.executor.sync_positions)` (`agents/trading/executor.py:971`) — i.e. on a worker thread.

## Goals / Non-Goals

**Goals:** absorb transient OKX network blips on `fetch_positions` with a bounded retry; stop escalating single blips to ERROR; capture exception type for diagnosis.

**Non-Goals:** reconciliation/algo-migration changes; other `fetch_*` calls; connection-pool tuning; new config keys; async refactor.

## Decisions

### D1 — Retry on `ccxt.NetworkError` (base class), not an enumerated list
`ccxt.RequestTimeout`, `ExchangeNotAvailable`, `DDoSProtection` all subclass `ccxt.NetworkError`. Catching the base covers every transient network/timeout case in one clause, while letting non-transient errors (`AuthenticationError`, `BadRequest`, code bugs) fail straight through to ERROR. Verified: `ccxt` exposes all four.

### D2 — Synchronous retry with `time.sleep`, hardcoded constants
`sync_positions` runs in a worker thread (`asyncio.to_thread`), so `time.sleep` backoff does **not** block the event loop. Constants in `executor.py` (`_POS_SYNC_RETRY_ATTEMPTS = 3`, backoffs `0.5, 1.0`) — no config surface (hotfix scope; config-ization deferred). Mirrors the existing `market_scanner._fetch_tickers_with_retry` intent but sync, since this path is sync.

### D3 — Helper raises on exhaustion; outer `except` stays the single ERROR sink
New `_fetch_positions_with_retry()` loops attempts, logs WARNING per transient retry (`[仓位同步] fetch_positions 第N/M次失败({type})，{delay}s后重试`), and re-raises the last exception if all attempts fail. The existing outer `except Exception` then logs ERROR — improved to `仓位同步失败: {type(e).__name__}: {e}`. This keeps exactly one ERROR sink and preserves the benign terminal behavior (local positions kept).

## Risks / Trade-offs

- **[Added latency on failure]** worst case ~1.5s extra (0.5+1.0) on a fully-failing sync — acceptable for a periodic background sync on a worker thread. → bounded at 3 attempts.
- **[Masking a real outage]** if OKX is truly down, retries still exhaust → ERROR (now with type), so persistent outages still surface. → only transient blips are absorbed.

## Migration Plan

Pure resilience change, no state/schema. Deploy by restart. Rollback = revert.
