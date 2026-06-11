## Context

The multi-agent system (`run_agents.py`) builds every ccxt client through one factory, `utils/exchange_factory.create_exchange`, used by 4 agents (data_collector, market_scanner, judge, telegram_notifier). Each calls `load_markets()` during `setup()`. On 2026-06-11 OKX began returning 2 malformed `future` markets with `id=None`/`symbol=None`; ccxt `keysort` (`ccxt/base/exchange.py:1064`) does `dict(sorted(markets_by_id.items()))` and raises `TypeError: '<' not supported between instances of 'NoneType' and 'str'`.

The agent runtime makes this fatal and invisible:
- `BaseAgent.run()` calls `await self.setup()` with no guard → the exception propagates and the agent's task ends during setup. Only "启动" is logged, never the agent's "就绪" line.
- `Orchestrator._health_loop` iterates `self._tasks`, calls `task.exception()` only to increment `tasks_failed` — which *retrieves* the exception so asyncio never prints "Task exception was never retrieved". The crash leaves **no traceback anywhere** (file logs or stdout).

Net effect: `data_collector` never publishes `market_data` → no `tech_analysis` → Judge makes 0 decisions → no opens (live or paper). Confirmed: across 6 restarts today, `data_collector` logged 启动×6 / 就绪×0, `tasks_failed=1` constant. Reproduced standalone — exact traceback in `keysort`.

## Goals / Non-Goals

**Goals:**
- `load_markets()` survives malformed (null-id) exchange markets without raising, for all factory-created clients.
- No agent `setup()` failure is ever silent again — full traceback logged.
- Any failed agent task raises an operator-visible Telegram alert naming the agent + exception.
- Restore the data→decision→execution pipeline; reproduces-on-restart bug eliminated.

**Non-Goals:**
- Upgrading ccxt (would trigger the "no ccxt upgrade without OKX testnet re-acceptance" red line).
- Changing trading strategy, risk, or Judge logic.
- Auto-restarting dead agent tasks (supervision = visibility here, not self-healing; restart semantics are a possible follow-up).

## Decisions

### D1 — Fix at ccxt `keysort` (shim), not at market filtering
**Choice**: Monkeypatch `ccxt.base.exchange.Exchange.keysort` to sort `None` keys deterministically (`key=lambda kv: (kv[0] is None, str(kv[0]))`), installed once at import of a new `utils/ccxt_compat.py`, imported by `utils/exchange_factory`.
**Why over alternatives**:
- *Filter null-id markets before `set_markets`* — semantically cleaner but requires overriding `fetch_markets`/`load_markets` per exchange; more invasive and exchange-specific. The garbage markets being absent is desirable, but the shim already makes them harmless.
- *Upgrade ccxt* — rejected (red line, needs testnet acceptance).
- The shim is generic: protects all 4 call sites + `OrderCapabilities` warmup + any future None-key sort, in one place. `markets_by_id` is a lookup dict — sort order is irrelevant to correctness, so reordering None-first is safe.

### D2 — Harden setup in `BaseAgent.run()`, not just `MultiDataCollector`
**Choice**: Wrap `await self.setup()` in `run()` with `try/except`, log `self.logger.critical(... + traceback.format_exc())`, then re-raise.
**Why**: The silent-death root is the generic `run()`→setup path + the health-loop's exception retrieval, not collector-specific. Fixing `base.run()` covers *every* agent's setup with one change. Re-raise (not swallow) so the task still reports failure to the health loop — but now with a logged traceback. (A swallowed setup leaves the collector with no `markets`, breaking symbol validation — worse than failing loud.)

### D3 — Failed-task alert mirrors `_maybe_alert_dlq_growth`
**Choice**: In `_health_loop`'s existing task scan, when a task is `done()` & not cancelled & `exception() is not None`, collect `(agent_name, repr(exc))`. Map task→agent by index (`self._tasks[:len(all_agents)]` aligns with `all_agents`; trailing tasks are research/cmd/health). Add `_maybe_alert_task_failure(failed)` that publishes `telegram_alert {type: agent_task_failed}` only for newly-seen failures (track an `_alerted_failed_tasks` set, like `_prev_dlq_size`), to avoid per-30s spam.
**Why**: Reuses an existing, proven alert path; keeps `agent_health.json` schema unchanged; turns a silent counter into an actionable signal. This is the "Agent health supervisor" to-do.

## Risks / Trade-offs

- **[Monkeypatching vendored ccxt]** → isolated to one tiny override in `utils/ccxt_compat.py`, documented, and behavior-preserving for non-None keys (only changes the previously-crashing case). Revert = delete one import.
- **[Task→agent index mapping drift]** if `_tasks` assembly order changes → mitigate by mapping defensively (guard index bounds; fall back to "unknown-agent" label) and keeping the mapping next to the `_tasks` construction.
- **[Alert spam]** if a task flaps → mitigate with the `_alerted_failed_tasks` dedup set (alert once per failed task identity).
- **[OKX stops returning bad markets]** (self-resolves) → shim is still correct and harmless; keep it as a permanent guard.

## Migration Plan

1. Land shim + base guard + alert behind no flags (pure resilience, safe-by-default).
2. Restart `run_agents.py`; verify `data_collector` logs "9维度数据采集就绪", `[采集]` lines flow, `tasks_failed=0`, Judge resumes decisions.
3. Rollback: revert the commit (no state/schema migration; the only new artifact is `agent_task_failed` alerts, additive).

## Open Questions

- Should failed-task alerting also attempt a bounded auto-restart of the dead agent task? (Deferred — out of scope; visibility first.)
- Should we *also* filter null-id markets (defense-in-depth on top of the keysort shim)? Decide in build; lean no unless cheap.
