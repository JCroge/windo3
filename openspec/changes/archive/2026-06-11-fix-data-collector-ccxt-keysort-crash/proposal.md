## Why

`MultiDataCollector.setup()` crashes on startup whenever OKX's `fetch_markets()` returns malformed markets. As of 2026-06-11 OKX returns **2 markets with `id=None`/`symbol=None`**; ccxt's `keysort` does `dict(sorted(markets_by_id.items()))`, and Python 3 cannot compare `None < str`, so `load_markets()` raises `TypeError`. The collector's `setup()` has no `try/except`, so `collector.run()` dies during setup (logs only "启动", never "就绪").

The failure is **totally silent**: the orchestrator health loop calls `task.exception()` purely to count `tasks_failed`, which marks the exception retrieved, so asyncio never prints a traceback. Result: no `market_data` → no `tech_analysis` → Judge makes zero decisions → **neither live nor paper opens any position**, and it reproduces on every restart with nothing in the logs to explain it. The same `'<' not supported between 'NoneType' and 'str'` TypeError also hits `market_scanner.fetch_tickers` and `OrderCapabilities` warmup (they survive only because they retry).

## What Changes

- **Add a ccxt `keysort` null-tolerance shim** (`utils/ccxt_compat.py`), installed once when `utils/exchange_factory` is imported. Sorts `None` keys deterministically instead of raising. Protects all **4** `create_exchange` call sites (data_collector, market_scanner, judge, telegram_notifier) and `OrderCapabilities` warmup. **No ccxt version upgrade** (respects the existing "no ccxt upgrade without testnet acceptance" red line).
- **Harden agent setup in `base.run()`**: wrap `await self.setup()` in `try/except` that logs the full traceback (CRITICAL) before re-raising, so **no agent's setup failure is ever silent** again.
- **Add failed-task alerting in `orchestrator._health_loop`**: map each failed task to its agent name + exception repr and publish a `telegram_alert` on new failures (reusing the `_maybe_alert_dlq_growth` pattern). This is the long-standing "Agent health supervisor" to-do item.

## Capabilities

### New Capabilities
- `exchange-client-resilience`: ccxt exchange clients created via `create_exchange` must tolerate malformed exchange market data (e.g. null-id markets) and complete `load_markets()` without raising.
- `agent-fault-visibility`: agent setup/run failures must be loud — setup exceptions are logged with a full traceback, and any failed agent task triggers an operator (Telegram) alert naming the agent and exception.

### Modified Capabilities
<!-- none: no existing capability's REQUIREMENTS change -->

## Impact

- **New file**: `utils/ccxt_compat.py` (keysort shim) + one import line in `utils/exchange_factory.py`.
- **Modified**: `agents/base.py` (`run()` setup guard); `agents/orchestrator.py` (`_health_loop` failed-task alert + helper, mirroring `_maybe_alert_dlq_growth`).
- **Behavioral**: restores the data→decision→execution pipeline (currently dead); adds a new `telegram_alert` type `agent_task_failed`.
- **Non-goals**: do NOT upgrade ccxt; do NOT change trading strategy / risk logic; do NOT alter message contracts beyond the new alert type.
- **Risk**: low — shim only changes sort behavior for `None` keys (a lookup dict, order irrelevant); base/orchestrator changes are additive logging/alerting.
