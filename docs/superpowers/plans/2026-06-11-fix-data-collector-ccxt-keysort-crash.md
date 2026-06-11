---
change: fix-data-collector-ccxt-keysort-crash
design-doc: docs/superpowers/specs/2026-06-11-fix-data-collector-ccxt-keysort-crash-design.md
base-ref: da3d3170c874f9d3572c12d6b2e499268ab777fb
archived-with: 2026-06-11-fix-data-collector-ccxt-keysort-crash
---

# data_collector ccxt keysort 崩溃修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 ccxt `load_markets()` 容忍 OKX 的 null-id 畸形市场，并让任何 agent 的 setup 崩溃响亮可见（日志 + Telegram 告警），恢复 data→decision→execution 链路。

**Architecture:** 三处独立修复：① import-once 的 ccxt keysort shim（`utils/ccxt_compat.py`，由 `exchange_factory` 导入）；② `BaseAgent.run()` 包 setup try/except 打 traceback 再抛；③ `Orchestrator` 把失败 task 映射到 agent 名并发去重 `telegram_alert`。需求以 OpenSpec delta spec 为准（`exchange-client-resilience`、`agent-fault-visibility`）。

**Tech Stack:** Python 3.9 / asyncio / ccxt / pytest（`asyncio_mode = auto`）。测试放仓库根 `test_*.py`（与多数现有测试一致）。

---

## File Structure

- `utils/ccxt_compat.py` — **新增**。覆写 `ccxt.base.exchange.Exchange.keysort`，None 键安全。
- `utils/exchange_factory.py` — **改**。顶部 `import utils.ccxt_compat` 安装 shim。
- `agents/base.py` — **改**。`run()` 包 `await self.setup()`。
- `agents/orchestrator.py` — **改**。新增 `_collect_task_stats()`（纯函数 seam）、`_maybe_alert_task_failure()`；`__init__` 加去重 set；`_health_loop` 调新告警。
- `test_ccxt_compat.py` / `test_base_setup_guard.py` / `test_agent_task_failure_alert.py` — **新增**测试。

---

## Task 1: ccxt keysort 容 None shim (`exchange-client-resilience`)

**Files:**
- Create: `utils/ccxt_compat.py`
- Modify: `utils/exchange_factory.py:6-7`（在 `import ccxt` 后加一行）
- Test: `test_ccxt_compat.py`

- [ ] **Step 1: 写失败测试** `test_ccxt_compat.py`

```python
import ccxt
import utils.ccxt_compat  # 安装 shim（import 即生效）


def test_keysort_tolerates_none_key():
    ex = ccxt.okx()
    out = ex.keysort({None: 1, "b": 2, "a": 3})  # 不应抛 TypeError
    assert list(out.keys()) == [None, "a", "b"]   # None 排首，其余按 str 升序


def test_keysort_all_str_unchanged():
    ex = ccxt.okx()
    assert list(ex.keysort({"b": 1, "a": 2, "c": 3}).keys()) == ["a", "b", "c"]


def test_install_is_idempotent():
    import importlib
    importlib.reload(utils.ccxt_compat)  # 二次安装不报错、不叠加
    ex = ccxt.okx()
    assert ex.keysort({None: 1}) == {None: 1}


def test_markets_by_id_with_none_id_does_not_crash():
    # 复现根因：markets_by_id 含 None 键时排序不崩
    ex = ccxt.okx()
    d = {None: {"id": None}, "BTC-USDT-SWAP": {"id": "BTC-USDT-SWAP"}}
    assert ex.keysort(d) is not None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest test_ccxt_compat.py -v`
Expected: FAIL — 未打 shim 时 `test_keysort_tolerates_none_key` 抛 `TypeError: '<' not supported...`（且 `import utils.ccxt_compat` ModuleNotFoundError）。

- [ ] **Step 3: 写 `utils/ccxt_compat.py`**

```python
"""ccxt 兼容性 shim：使 keysort 容忍 None 键。

OKX 偶尔返回 id=None 的畸形市场，ccxt keysort 用 dict(sorted(items())) 排序
markets_by_id 时 None<str 抛 TypeError，导致 load_markets 崩溃。
本 shim 让 None 键确定性排在最前，不再抛错。import 本模块即安装（幂等）。
不升级 ccxt，规避「ccxt 升级须 testnet 重验收」红线。
"""
import ccxt.base.exchange as _ccxt_base

_PATCH_FLAG = "_keysort_none_safe"


def _safe_keysort(self, dictionary):
    return dict(sorted(dictionary.items(), key=lambda kv: (kv[0] is None, str(kv[0]))))


def install():
    if getattr(_ccxt_base.Exchange, _PATCH_FLAG, False):
        return
    _ccxt_base.Exchange.keysort = _safe_keysort
    setattr(_ccxt_base.Exchange, _PATCH_FLAG, True)


install()
```

- [ ] **Step 4: 在 exchange_factory 安装 shim**

Modify `utils/exchange_factory.py`，在 `import ccxt`（第 7 行）下方加：

```python
import ccxt
import utils.ccxt_compat  # noqa: F401  安装 keysort None-safe shim（import 即生效）
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python3 -m pytest test_ccxt_compat.py -v`
Expected: PASS（4 个）。

- [ ] **Step 6: 提交**

```bash
git add utils/ccxt_compat.py utils/exchange_factory.py test_ccxt_compat.py
git commit -m "fix(exchange): ccxt keysort 容 None 键 shim，修 OKX null-id 市场致 load_markets 崩溃 (exchange-client-resilience)"
```

- [ ] **Step 7: 勾选 tasks.md 1.1/1.2/1.3**

---

## Task 2: base.run() setup 失败不再静默 (`agent-fault-visibility`)

**Files:**
- Modify: `agents/base.py:48-54`（`run()` 的 `await self.setup()`）
- Test: `test_base_setup_guard.py`

- [ ] **Step 1: 写失败测试** `test_base_setup_guard.py`

```python
import asyncio
from unittest.mock import MagicMock
import pytest
from agents.base import BaseAgent


class _DummyAgent(BaseAgent):
    name = "dummy_setup_test"

    def __init__(self, fail: bool):
        super().__init__({})
        self._fail = fail

    async def on_message(self, msg):  # 满足 ABC
        pass

    async def setup(self):
        if self._fail:
            raise RuntimeError("boom")


async def test_setup_failure_logged_with_traceback_and_reraised():
    a = _DummyAgent(fail=True)
    a.logger = MagicMock()
    with pytest.raises(RuntimeError):
        await a.run()
    a.logger.critical.assert_called_once()
    msg = a.logger.critical.call_args[0][0]
    assert "setup 失败" in msg
    assert "Traceback" in msg  # traceback.format_exc() 输出含 "Traceback"


async def test_setup_success_does_not_log_critical():
    a = _DummyAgent(fail=False)
    a.logger = MagicMock()
    # loops 替换为立即返回，避免 run() 永久阻塞
    async def _noop():
        return
    a._message_loop = _noop
    a._periodic_loop = _noop
    await a.run()
    a.logger.critical.assert_not_called()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest test_base_setup_guard.py -v`
Expected: FAIL — 当前 `run()` 无 try/except，setup 异常直接抛但不调用 `logger.critical`，`assert_called_once` 失败。

- [ ] **Step 3: 改 `agents/base.py` run()**

把第 54 行 `await self.setup()` 替换为：

```python
        try:
            await self.setup()
        except Exception:
            import traceback
            self.logger.critical(
                f"Agent [{self.name}] setup 失败\n{traceback.format_exc()}"
            )
            raise
```

（保持其余 run() 不变；`import traceback` 局部导入，与 `_message_loop`/`_periodic_loop` 既有写法一致。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest test_base_setup_guard.py -v`
Expected: PASS（2 个）。

- [ ] **Step 5: 提交**

```bash
git add agents/base.py test_base_setup_guard.py
git commit -m "fix(base): run() 包 setup try/except 打 traceback 再抛，根除 agent setup 静默死亡 (agent-fault-visibility)"
```

- [ ] **Step 6: 勾选 tasks.md 2.1/2.2**

---

## Task 3: orchestrator 失败任务去重告警 (`agent-fault-visibility`)

**Files:**
- Modify: `agents/orchestrator.py:40`（`__init__` 加去重 set + 最近失败缓存）
- Modify: `agents/orchestrator.py:285-308`（抽出 `_collect_task_stats`，`_write_agent_health` 复用）
- Modify: `agents/orchestrator.py:334`（在 `_maybe_alert_dlq_growth` 旁加 `_maybe_alert_task_failure`）
- Modify: `agents/orchestrator.py:361-365`（`_health_loop` 调新告警）
- Test: `test_agent_task_failure_alert.py`

- [ ] **Step 1: 写失败测试** `test_agent_task_failure_alert.py`

```python
import asyncio
import pytest
from agents.orchestrator import Orchestrator


class _FakeTask:
    def __init__(self, done, exc=None, cancelled=False):
        self._done, self._exc, self._cancelled = done, exc, cancelled
    def done(self): return self._done
    def cancelled(self): return self._cancelled
    def exception(self): return self._exc


class _StubAgent:
    def __init__(self, name): self.name = name


class _FakeBus:
    def __init__(self): self.published = []
    async def publish(self, src, mtype, payload, to):
        self.published.append((mtype, payload))


def _orch():
    o = Orchestrator()
    o._research_agents = [_StubAgent("market_scanner")]
    o._trading_agents = [_StubAgent("multi_data_collector")]
    o.bus = _FakeBus()
    return o


def test_collect_task_stats_maps_index_to_agent_name():
    o = _orch()
    exc = RuntimeError("boom")
    # index 0 -> market_scanner(alive), index 1 -> multi_data_collector(failed)
    o._tasks = [_FakeTask(done=False), _FakeTask(done=True, exc=exc)]
    alive, failed = o._collect_task_stats()
    assert alive == 1
    assert failed == [("multi_data_collector", repr(exc))]


def test_unknown_index_uses_unknown_agent_label():
    o = _orch()
    exc = RuntimeError("x")
    # 3 个 task 但只有 2 个 agent -> index 2 越界
    o._tasks = [_FakeTask(False), _FakeTask(False), _FakeTask(True, exc)]
    _, failed = o._collect_task_stats()
    assert failed == [("unknown-agent", repr(exc))]


async def test_alert_published_once_then_deduped():
    o = _orch()
    failed = [("multi_data_collector", "RuntimeError('boom')")]
    await o._maybe_alert_task_failure(failed)
    await o._maybe_alert_task_failure(failed)  # 同一失败再来一次
    alerts = [p for (t, p) in o.bus.published if t == "telegram_alert"]
    assert len(alerts) == 1
    assert alerts[0]["type"] == "agent_task_failed"
    assert alerts[0]["agent"] == "multi_data_collector"


async def test_cancelled_task_not_counted_as_failed():
    o = _orch()
    o._tasks = [_FakeTask(done=True, cancelled=True)]
    _, failed = o._collect_task_stats()
    assert failed == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest test_agent_task_failure_alert.py -v`
Expected: FAIL — `_collect_task_stats` / `_maybe_alert_task_failure` 尚不存在（AttributeError）。

- [ ] **Step 3: `__init__` 加状态**（`agents/orchestrator.py:40` `self._prev_dlq_size` 行后）

```python
        self._prev_dlq_size: int = 0  # P2-16: DLQ 增长告警基准
        self._alerted_failed_tasks: set = set()  # 已告警失败任务标识，防重复
        self._latest_failed_tasks: list = []     # 最近一次扫描的失败任务 [(agent, repr)]
```

- [ ] **Step 4: 抽出 `_collect_task_stats`，改造 `_write_agent_health`**

在 `_write_agent_health` 前新增纯函数 seam：

```python
    def _collect_task_stats(self):
        """返回 (tasks_alive, failed)；failed=[(agent_name, repr(exc))]。

        index 映射 all_agents = _research_agents + _trading_agents（与 _tasks 前缀对齐）；
        越界（research/cmd/health 等附加 task）用 'unknown-agent'。纯函数，不写文件，便于单测。
        """
        all_agents = self._research_agents + self._trading_agents
        tasks_alive = 0
        failed = []
        for i, t in enumerate(self._tasks):
            try:
                if not t.done():
                    tasks_alive += 1
                    continue
                cancelled = getattr(t, "cancelled", None)
                if callable(cancelled) and cancelled() is True:
                    continue
                try:
                    exc = t.exception()
                except asyncio.CancelledError:
                    continue
                if exc is not None:
                    name = all_agents[i].name if i < len(all_agents) else "unknown-agent"
                    failed.append((name, repr(exc)))
            except Exception:
                pass
        return tasks_alive, failed
```

在 `_write_agent_health` 里，把原先内联的 `for t in self._tasks:` 计数块替换为：

```python
            tasks_alive, failed = self._collect_task_stats()
            tasks_failed = len(failed)
            self._latest_failed_tasks = failed
```

（其余 health dict 组装、`atomic_write_json`、`return dlq_size` 不变。）

- [ ] **Step 5: 新增 `_maybe_alert_task_failure`**（紧跟 `_maybe_alert_dlq_growth` 之后，约 `:360`）

```python
    async def _maybe_alert_task_failure(self, failed):
        """失败 agent 任务首次出现时发 telegram_alert（去重；仅可见性，不自动重启）。"""
        if not failed:
            return
        for agent_name, err in failed:
            key = f"{agent_name}:{err}"
            if key in self._alerted_failed_tasks:
                continue
            self._alerted_failed_tasks.add(key)
            try:
                from agents.message_bus import MessageBus
                bus = self.bus or MessageBus.get_instance()
                await bus.publish("orchestrator", "telegram_alert", {
                    "level": "critical",
                    "type": "agent_task_failed",
                    "agent": agent_name,
                    "error": err,
                    "message": f"Agent [{agent_name}] 任务已崩溃退出：{err}",
                }, "broadcast")
            except Exception as e:
                self.logger.warning(f"[TaskFail Alert] 发布失败: {e}")
```

- [ ] **Step 6: `_health_loop` 调新告警**（`agents/orchestrator.py:364` `_maybe_alert_dlq_growth` 行后）

```python
            dlq_size = self._write_agent_health()
            await self._maybe_alert_dlq_growth(dlq_size)
            await self._maybe_alert_task_failure(self._latest_failed_tasks)
```

- [ ] **Step 7: 跑测试确认通过**

Run: `python3 -m pytest test_agent_task_failure_alert.py -v`
Expected: PASS（4 个）。

- [ ] **Step 8: 提交**

```bash
git add agents/orchestrator.py test_agent_task_failure_alert.py
git commit -m "feat(orchestrator): 失败 agent 任务发去重 telegram_alert{agent_task_failed}（Agent health supervisor, agent-fault-visibility）"
```

- [ ] **Step 9: 勾选 tasks.md 3.1/3.2/3.3**

---

## Task 4: 端到端验证与收尾

**Files:** 无代码改动（验证）

- [ ] **Step 1: 复现脚本确认 load_markets 现在通过**

Run:
```bash
python3 - <<'PY'
from utils.exchange_factory import create_exchange
ex = create_exchange({"exchange": "okx", "use_testnet": False}, purpose="data_collector")
ex.load_markets()
print("load_markets OK:", len(ex.markets), "markets")
PY
```
Expected: `load_markets OK: <N> markets`（不再 TypeError）。勾选 tasks.md 4.1。

- [ ] **Step 2: 全量测试**

Run: `python3 -m pytest -q`
Expected: 基线 1088 + 本次新增（约 10）全部 PASS / 4 deselected / 1 warning。勾选 tasks.md 4.2。

- [ ] **Step 3: 运行期验证（交回用户重启）**

`run_agents.py` 重启后确认：`agent_multi_data_collector` 日志出现 "9维度数据采集就绪" 与 `[采集]`；`data/agent_health.json` 的 `tasks_failed=0`；Judge 恢复产出决策。勾选 tasks.md 4.3。

> 注：Step 3 由用户重启实例后，用既有系统监控（重新 arm `/tmp/watch30.py`）确认 `[OK] 行情采集已恢复`。

- [ ] **Step 4: 终次提交（勾选 tasks.md 4.x）**

```bash
git add openspec/changes/fix-data-collector-ccxt-keysort-crash/tasks.md
git commit -m "docs(comet): mark build tasks complete (1088+ passed, load_markets 恢复)"
```

---

## Self-Review

- **Spec coverage**：`exchange-client-resilience`（null-id 容忍 / 4 调用点保护 / 正常不变）→ Task 1；`agent-fault-visibility`（setup 失败记录+重抛 / 失败任务告警 / 去重 / unknown-agent）→ Task 2+3。全覆盖。
- **Placeholder scan**：无 TBD/TODO；每个 code step 给出完整代码。
- **Type consistency**：`_collect_task_stats` 返回 `(int, list[tuple])`，`_maybe_alert_task_failure` 消费同结构；`_latest_failed_tasks` 类型一致；告警 payload 字段（type/agent/error）与测试断言一致。
