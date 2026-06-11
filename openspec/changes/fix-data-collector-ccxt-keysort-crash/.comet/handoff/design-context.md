# Comet Design Handoff

- Change: fix-data-collector-ccxt-keysort-crash
- Phase: design
- Mode: compact
- Context hash: d5bbaed794449d12c8d5f8d1b19f77201c8b0644c2f4167c97f0f15e26353ca8

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/fix-data-collector-ccxt-keysort-crash/proposal.md

- Source: openspec/changes/fix-data-collector-ccxt-keysort-crash/proposal.md
- Lines: 1-28
- SHA256: 3c72ca5edb8709b02dac2d9ff9256220ba12a852f99bcdd8475f69c6d5d5ac0f

```md
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
```

## openspec/changes/fix-data-collector-ccxt-keysort-crash/design.md

- Source: openspec/changes/fix-data-collector-ccxt-keysort-crash/design.md
- Lines: 1-57
- SHA256: 740014427886141eb496b2d39505b67b5558fb323f08355b486aea6e3c6000cc

```md
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
```

## openspec/changes/fix-data-collector-ccxt-keysort-crash/tasks.md

- Source: openspec/changes/fix-data-collector-ccxt-keysort-crash/tasks.md
- Lines: 1-20
- SHA256: 6b59ee15e5b61aad50f394c2e388e14e31b57791c92d40a60dabea189cbbb4ae

```md
# Tasks

## 1. ccxt keysort 容 None shim (exchange-client-resilience)
- [ ] 1.1 新增 `utils/ccxt_compat.py`：覆写 `ccxt.base.exchange.Exchange.keysort`，用 `key=lambda kv: (kv[0] is None, str(kv[0]))` 排序，安装一次（模块级幂等）
- [ ] 1.2 `utils/exchange_factory.py` 顶部 `import utils.ccxt_compat`（确保任何 `create_exchange` 前 shim 已装）
- [ ] 1.3 单测：`keysort({None: x, "a": y})` 不抛且 None 排首；全 str 键顺序与原 ccxt 一致；构造含 `id=None` 的 mock markets 走 `set_markets` 不抛

## 2. base.run() setup 失败不再静默 (agent-fault-visibility)
- [ ] 2.1 `agents/base.py:run()` 把 `await self.setup()` 包 `try/except`，`logger.critical(f"Agent [{name}] setup 失败" + traceback.format_exc())` 后 `raise`
- [ ] 2.2 单测：setup 抛异常 → 记录 CRITICAL 含 traceback 且异常重抛；正常 setup 不记录、继续进入 loops

## 3. orchestrator 失败任务主动告警 (agent-fault-visibility)
- [ ] 3.1 `_health_loop` 任务扫描里收集 `(agent_name, repr(exc))`（按 index 映射 `all_agents`，越界用 `unknown-agent`）
- [ ] 3.2 新增 `_maybe_alert_task_failure(failed)`：对未告警过的失败任务发 `telegram_alert {type:"agent_task_failed", agent, error}`，用 `_alerted_failed_tasks` set 去重；`agent_health.json` schema 不变
- [ ] 3.3 单测：失败任务发一次 alert、同一任务再 tick 不重发、未知 index 用 `unknown-agent` 仍发

## 4. 验证与收尾
- [ ] 4.1 复现脚本（create_exchange + load_markets，真实 OKX）现在返回成功、markets>0
- [ ] 4.2 全量 `python3 -m pytest -q` 通过（基线 1088 + 本次新增用例）
- [ ] 4.3 重启 `run_agents.py`：`data_collector` 打出"9维度数据采集就绪"+`[采集]`，`tasks_failed=0`，Judge 恢复产出决策（live/paper 可开仓）
```

## openspec/changes/fix-data-collector-ccxt-keysort-crash/specs/agent-fault-visibility/spec.md

- Source: openspec/changes/fix-data-collector-ccxt-keysort-crash/specs/agent-fault-visibility/spec.md
- Lines: 1-31
- SHA256: 4ffea3a0efd96e4a957b86e752dfa6fde9cdf7e27cfb44117f96483e77d0e915

```md
## ADDED Requirements

### Requirement: Agent setup failures are logged, never silent

When an agent's `setup()` raises during `BaseAgent.run()`, the system SHALL log the full exception traceback at CRITICAL level (identifying the agent) before the task terminates. A setup failure MUST NOT leave the agent dead with no diagnostic output.

#### Scenario: setup() raises an exception
- **WHEN** an agent's `setup()` raises (e.g. `load_markets()` throws)
- **THEN** `run()` logs `Agent [<name>] setup 失败` with the full `traceback.format_exc()` at CRITICAL level
- **AND** the exception is re-raised so the task is recorded as failed

#### Scenario: Healthy setup is unaffected
- **WHEN** an agent's `setup()` completes normally
- **THEN** no failure is logged and the agent proceeds to its message/periodic loops

### Requirement: Failed agent tasks raise an operator alert

The orchestrator health loop SHALL detect agent tasks that have terminated with an exception and publish a `telegram_alert` of type `agent_task_failed` that names the affected agent and the exception. The same failed task MUST NOT be alerted repeatedly on every health tick.

#### Scenario: An agent task dies with an exception
- **WHEN** the health loop observes a task that is done, not cancelled, and `exception()` is not `None`
- **THEN** it publishes `telegram_alert {type: "agent_task_failed", agent: <name>, error: <repr>}`
- **AND** `agent_health.json` continues to report the `tasks_failed` count

#### Scenario: The same failure is not re-alerted
- **WHEN** a previously-alerted failed task is still failed on a subsequent health tick
- **THEN** no duplicate alert is published for that task

#### Scenario: Failed task cannot be mapped to an agent
- **WHEN** a failed task's index does not correspond to a known agent
- **THEN** the alert is still published with an `unknown-agent` label rather than being suppressed
```

## openspec/changes/fix-data-collector-ccxt-keysort-crash/specs/exchange-client-resilience/spec.md

- Source: openspec/changes/fix-data-collector-ccxt-keysort-crash/specs/exchange-client-resilience/spec.md
- Lines: 1-19
- SHA256: 47c981f57b7b31440e2cef5b0a466d27f5ab3201de6480ff416aa84fc2666dd7

```md
## ADDED Requirements

### Requirement: Exchange clients tolerate malformed market data

ccxt exchange clients created through `utils/exchange_factory.create_exchange` SHALL complete `load_markets()` without raising when the exchange returns malformed markets (e.g. markets whose `id` is `None`). The system MUST NOT require a ccxt version upgrade to achieve this.

#### Scenario: OKX returns markets with null id
- **WHEN** `load_markets()` is called and the exchange's `markets_by_id` contains one or more `None` keys
- **THEN** `keysort` sorts the entries deterministically (treating `None` as ordered-first) instead of raising `TypeError`
- **AND** `load_markets()` returns successfully with the valid markets available

#### Scenario: All factory-created clients are protected
- **WHEN** any agent (`data_collector`, `market_scanner`, `judge`, `telegram_notifier`) constructs its client via `create_exchange`
- **THEN** the null-tolerance behavior is already installed (the shim is applied once on import of the factory)
- **AND** no per-call-site change is required for protection

#### Scenario: Normal market data is unaffected
- **WHEN** `keysort` receives a dictionary whose keys are all strings
- **THEN** the resulting order is identical to the pre-shim ccxt behavior
```

