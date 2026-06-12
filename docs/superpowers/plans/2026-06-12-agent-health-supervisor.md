---
archived-with: 2026-06-12-agent-health-supervisor
status: final
---
# Agent Health Supervisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Orchestrator 聚合 loop-alive / queue backlog / LLM degraded / data degraded 四维度健康，扩展 `agent_health.json` + `/status` 总括行 + `/health` 明细，并在维度健康↔不健康跳变时发一次 Telegram 告警。

**Architecture:** 心跳/聚合状态埋在 BaseAgent 与 MultiDataCollector 实例字段；纯函数 `utils/health_snapshot.py::build_health_snapshot` 读这些字段 + bus 指标产出 snapshot dict；Orchestrator `_health_loop` 调用 builder→写文件→跑边沿告警状态机；TelegramNotifier 读 snapshot 展示。observability-only，零决策路径，不需 event_backtest 同构。

**Tech Stack:** Python 3.9 / asyncio / pytest（默认 `-m "not network"`）。

**Spec:** `docs/superpowers/specs/2026-06-12-agent-health-supervisor-design.md`

**Baseline before start:** `1102 passed / 4 deselected / 1 warning`（branch `agent-health-supervisor`）

---

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `utils/health_snapshot.py` | 纯函数：把 agents+bus 指标算成 snapshot dict | **新建** |
| `agents/base.py` | `_last_alive_ts`（告警心跳）/ `_last_work_ts`（仅展示） | 改 |
| `agents/trading/multi_data_collector.py` | `_latest_data_health` 聚合字段 | 改 |
| `utils/config_loader.py` | 3 个阈值配置（DEFAULTS/HARD_LIMITS/env_map） | 改 |
| `agents/orchestrator.py` | `_write_agent_health` 接 builder + `_maybe_alert_health_transitions` | 改 |
| `agents/trading/telegram_notifier.py` | `/status` 总括行 + `/health` 命令 | 改 |
| `tests/test_health_snapshot.py` | builder 单测 | 新建 |
| `tests/test_base_agent_heartbeat.py` | 心跳埋点单测 | 新建 |
| `tests/test_health_alert_transitions.py` | 告警状态机单测 | 新建 |
| `tests/test_collector_data_health.py` | collector 聚合字段单测 | 新建 |
| `tests/test_health_telegram_display.py` | `/status` + `/health` 渲染单测 | 新建 |

---

## Task 1: BaseAgent 心跳埋点

**Files:**
- Modify: `agents/base.py:17-25`（`__init__` 加字段）、`agents/base.py:73-85`（`_message_loop` 盖戳）
- Test: `tests/test_base_agent_heartbeat.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_base_agent_heartbeat.py
import asyncio
import time
import pytest
from agents.message_bus import MessageBus
from agents.base import BaseAgent


class _Probe(BaseAgent):
    name = "probe_hb"
    subscriptions = []

    def __init__(self, config=None):
        super().__init__(config)
        self.seen = []

    async def setup(self):
        pass

    async def on_message(self, msg):
        self.seen.append(msg)


@pytest.fixture(autouse=True)
def _reset_bus():
    MessageBus._instance = None
    yield
    MessageBus._instance = None


def test_init_heartbeat_fields_default_zero():
    a = _Probe()
    assert a._last_alive_ts == 0.0
    assert a._last_work_ts == 0.0


@pytest.mark.asyncio
async def test_message_loop_stamps_alive_even_without_messages():
    a = _Probe()
    a._running = True
    t0 = time.time()
    task = asyncio.create_task(a._message_loop())
    await asyncio.sleep(0.6)  # 至少一轮 0.5s receive 超时
    a._running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert a._last_alive_ts >= t0          # loop 在转，alive 被刷新
    assert a._last_work_ts == 0.0          # 没消息，work 不刷新


@pytest.mark.asyncio
async def test_message_loop_stamps_work_on_message():
    bus = MessageBus.get_instance()
    a = _Probe()
    a._running = True
    task = asyncio.create_task(a._message_loop())
    await bus.publish("tester", "probe_topic", {"x": 1}, to=a.name)
    await asyncio.sleep(0.6)
    a._running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert a._last_work_ts > 0.0           # 处理到消息，work 被刷新
    assert a.seen, "agent 应收到消息"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_base_agent_heartbeat.py -v`
Expected: FAIL — `AttributeError: '_Probe' object has no attribute '_last_alive_ts'`

- [ ] **Step 3: Add fields in `__init__`**

In `agents/base.py`, `__init__`（现 L17-25），在 `self._start_time = 0` 之后加：

```python
        self._start_time = 0
        self._last_alive_ts = 0.0   # 心跳：message loop 每迭代刷新（告警信号，与业务节奏无关）
        self._last_work_ts = 0.0    # 业务进度：处理到消息时刷新（仅 /health 展示，永不告警）
```

- [ ] **Step 4: Stamp in `_message_loop`**

把 `agents/base.py:73-85` 的 `_message_loop` 改为（仅加两行盖戳）：

```python
    async def _message_loop(self):
        """快速消费消息，不被 tick sleep 阻塞"""
        while self._running and not self._should_stop:
            self._last_alive_ts = time.time()
            try:
                msg = await self.bus.receive(self.name, timeout=0.5)
                if msg:
                    self._last_work_ts = time.time()
                    await self.on_message(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                import traceback
                self.logger.error(f"消息处理错误: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(1)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_base_agent_heartbeat.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add agents/base.py tests/test_base_agent_heartbeat.py
git commit -m "feat(base): agent 心跳埋点 _last_alive_ts/_last_work_ts (#95)"
```

---

## Task 2: MultiDataCollector 数据健康聚合字段

**Files:**
- Modify: `agents/trading/multi_data_collector.py:34-45`（`__init__` 加字段 + helper）、`agents/trading/multi_data_collector.py:411-412`（`_full_collect` 成功后更新）
- Test: `tests/test_collector_data_health.py`

聚合字段在 collector 实例上累积"每个 symbol 最近一次是否 degraded + 最近成功采集时间"，供 builder 读取。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_collector_data_health.py
import time
import pytest
from agents.message_bus import MessageBus
from agents.trading.multi_data_collector import MultiDataCollector


@pytest.fixture(autouse=True)
def _reset_bus():
    MessageBus._instance = None
    yield
    MessageBus._instance = None


def test_init_data_health_default():
    c = MultiDataCollector()
    h = c._latest_data_health
    assert h["any_degraded"] is False
    assert h["degraded_symbols"] == []
    assert h["last_collect_ts"] is None


def test_update_data_health_marks_degraded_symbol():
    c = MultiDataCollector()
    c._update_data_health("BTC-USDT", degraded=False)
    c._update_data_health("ETH-USDT", degraded=True)
    h = c._latest_data_health
    assert h["any_degraded"] is True
    assert "ETH-USDT" in h["degraded_symbols"]
    assert "BTC-USDT" not in h["degraded_symbols"]
    assert h["last_collect_ts"] is not None


def test_update_data_health_clears_recovered_symbol():
    c = MultiDataCollector()
    c._update_data_health("ETH-USDT", degraded=True)
    assert c._latest_data_health["any_degraded"] is True
    c._update_data_health("ETH-USDT", degraded=False)   # 恢复
    h = c._latest_data_health
    assert h["any_degraded"] is False
    assert h["degraded_symbols"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_collector_data_health.py -v`
Expected: FAIL — `AttributeError: ... has no attribute '_latest_data_health'`

- [ ] **Step 3: Add field in `__init__`**

在 `agents/trading/multi_data_collector.py` `__init__`（现 L34-39，`self._symbol_health = {}` 之后）加：

```python
        self._symbol_health = {}
        self._latest_data_health = {
            "ts": None,
            "any_degraded": False,
            "degraded_symbols": [],
            "last_collect_ts": None,
        }
```

- [ ] **Step 4: Add `_update_data_health` helper**

在 `MultiDataCollector` 类内（紧邻 `_init_health`，约 L733 之前）新增方法：

```python
    def _update_data_health(self, symbol: str, degraded: bool):
        """更新聚合数据健康态（供 health_snapshot builder 读取）。

        维护"当前 degraded 的 symbol 集合"与"最近一次成功采集时间"。
        observability-only，不影响采集/决策。
        """
        h = self._latest_data_health
        now = time.time()
        h["ts"] = now
        h["last_collect_ts"] = now
        degraded_set = set(h["degraded_symbols"])
        if degraded:
            degraded_set.add(symbol)
        else:
            degraded_set.discard(symbol)
        h["degraded_symbols"] = sorted(degraded_set)
        h["any_degraded"] = len(degraded_set) > 0
```

- [ ] **Step 5: Call it after successful collect**

在 `_full_collect` 成功发布之后（现 `agents/trading/multi_data_collector.py:412` `self._record_success(symbol)` 之后）加一行：

```python
        await self.publish("market_data", payload, symbol=symbol)
        self._record_success(symbol)
        self._update_data_health(symbol, degraded)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python3 -m pytest tests/test_collector_data_health.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add agents/trading/multi_data_collector.py tests/test_collector_data_health.py
git commit -m "feat(collector): _latest_data_health 聚合数据降级态 (#95)"
```

---

## Task 3: health_snapshot.py 纯函数 builder

**Files:**
- Create: `utils/health_snapshot.py`
- Test: `tests/test_health_snapshot.py`

这是核心：输入 agents 列表 + bus 指标 + now + 阈值 + base_stats，输出 snapshot dict。纯计算、无 IO。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_health_snapshot.py
import pytest
from utils.health_snapshot import build_health_snapshot

NOW = 1_000_000.0
CFG = dict(stall_timeout_sec=60, backlog_warn_pending=200, data_stale_timeout_sec=180)
BASE = dict(agents_registered=2, tasks_alive=4, tasks_failed=0,
            halted_symbols={}, bus_dlq_size=0)


class _FakeLLM:
    def __init__(self, degraded, fails):
        self._degraded = degraded
        self.consecutive_failures = fails

    @property
    def degraded(self):
        return self._degraded


class _FakeAgent:
    def __init__(self, name, alive_ts=NOW, llm=None, data_health=None):
        self.name = name
        self._last_alive_ts = alive_ts
        self.llm = llm
        if data_health is not None:
            self._latest_data_health = data_health


def _snap(agents, bus_metrics, base=None):
    return build_health_snapshot(
        agents, bus_metrics, NOW,
        stall_timeout_sec=CFG["stall_timeout_sec"],
        backlog_warn_pending=CFG["backlog_warn_pending"],
        data_stale_timeout_sec=CFG["data_stale_timeout_sec"],
        base_stats=base or BASE,
    )


def test_base_stats_passthrough_and_ts():
    s = _snap([_FakeAgent("judge")], {"_dlq_size": 0})
    assert s["agents_registered"] == 2
    assert s["tasks_failed"] == 0
    assert s["ts"] == NOW


def test_loop_stall_detected_and_skips_unstarted():
    agents = [
        _FakeAgent("fresh", alive_ts=NOW - 5),       # 健康
        _FakeAgent("stuck", alive_ts=NOW - 120),     # stall (>60)
        _FakeAgent("unstarted", alive_ts=0.0),       # 未起跑，跳过
    ]
    s = _snap(agents, {"_dlq_size": 0})
    lh = s["loop_health"]
    assert lh["stalled_count"] == 1
    assert lh["stalled"][0]["name"] == "stuck"
    assert lh["stalled"][0]["idle_sec"] == 120


def test_queue_backlog_detected_and_ignores_dlq_key():
    bus_metrics = {
        "judge": {"pending": 10},
        "reviewer": {"pending": 250},     # > 200
        "_dlq_size": 7,                    # 非 agent，跳过
    }
    s = _snap([_FakeAgent("judge")], bus_metrics)
    qh = s["queue_health"]
    assert qh["backlogged_count"] == 1
    assert qh["backlogged"][0]["name"] == "reviewer"
    assert qh["max_pending"] == 250


def test_llm_degraded_aggregates_and_skips_none():
    agents = [
        _FakeAgent("judge", llm=_FakeLLM(degraded=True, fails=4)),
        _FakeAgent("scanner", llm=None),                       # 无 llm，跳过
        _FakeAgent("tech", llm=_FakeLLM(degraded=False, fails=0)),
    ]
    s = _snap(agents, {"_dlq_size": 0})
    lh = s["llm_health"]
    assert lh["degraded"] is True
    assert len(lh["degraded_agents"]) == 1
    assert lh["degraded_agents"][0]["name"] == "judge"
    assert lh["degraded_agents"][0]["consecutive_failures"] == 4


def test_data_degraded_from_collector():
    dh = {"ts": NOW - 10, "any_degraded": True,
          "degraded_symbols": ["ETH-USDT"], "last_collect_ts": NOW - 10}
    agents = [_FakeAgent("multi_data_collector", data_health=dh)]
    s = _snap(agents, {"_dlq_size": 0})
    d = s["data_health"]
    assert d["degraded"] is True
    assert d["stale"] is False
    assert d["last_collect_ago_sec"] == 10
    assert d["degraded_symbols"] == ["ETH-USDT"]


def test_data_stale_when_collect_old():
    dh = {"ts": NOW - 500, "any_degraded": False,
          "degraded_symbols": [], "last_collect_ts": NOW - 500}  # > 180
    agents = [_FakeAgent("multi_data_collector", data_health=dh)]
    s = _snap(agents, {"_dlq_size": 0})
    assert s["data_health"]["stale"] is True


def test_data_health_no_collector_is_neutral():
    s = _snap([_FakeAgent("judge")], {"_dlq_size": 0})
    d = s["data_health"]
    assert d["degraded"] is False
    assert d["stale"] is False
    assert d["present"] is False


def test_data_health_never_collected_not_stale():
    dh = {"ts": None, "any_degraded": False,
          "degraded_symbols": [], "last_collect_ts": None}
    agents = [_FakeAgent("multi_data_collector", data_health=dh)]
    s = _snap(agents, {"_dlq_size": 0})
    assert s["data_health"]["stale"] is False     # 启动初期不误报
    assert s["data_health"]["last_collect_ago_sec"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_health_snapshot.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.health_snapshot'`

- [ ] **Step 3: Write the builder**

```python
# utils/health_snapshot.py
"""Agent Health Supervisor — 健康快照纯函数 builder (#95)。

observability-only：把 agents 实例字段 + bus 指标聚合成 snapshot dict。
无 IO、无副作用、不改 agent 状态、不调用 bus；外部状态由调用方取好传入，
故可用假 stub 单测。严禁任何 gate/veto/halt 读取本快照（与 provenance 红线一致）。
"""

COLLECTOR_NAME = "multi_data_collector"


def _loop_health(agents, now, stall_timeout_sec):
    stalled = []
    for a in agents:
        ts = getattr(a, "_last_alive_ts", 0.0) or 0.0
        if ts <= 0.0:
            continue  # 尚未起跑，不算 stall
        idle = now - ts
        if idle > stall_timeout_sec:
            stalled.append({"name": a.name, "idle_sec": int(idle)})
    return {"stalled_count": len(stalled), "stalled": stalled}


def _queue_health(bus_metrics, backlog_warn_pending):
    backlogged = []
    max_pending = 0
    for name, m in bus_metrics.items():
        if name == "_dlq_size" or not isinstance(m, dict):
            continue
        pending = int(m.get("pending", 0) or 0)
        if pending > max_pending:
            max_pending = pending
        if pending > backlog_warn_pending:
            backlogged.append({"name": name, "pending": pending})
    return {"backlogged_count": len(backlogged),
            "max_pending": max_pending,
            "backlogged": backlogged}


def _llm_health(agents):
    degraded_agents = []
    for a in agents:
        llm = getattr(a, "llm", None)
        if llm is not None and getattr(llm, "degraded", False):
            degraded_agents.append({
                "name": a.name,
                "consecutive_failures": getattr(llm, "consecutive_failures", None),
            })
    return {"degraded": len(degraded_agents) > 0,
            "degraded_agents": degraded_agents}


def _data_health(agents, now, data_stale_timeout_sec):
    collector = next((a for a in agents if a.name == COLLECTOR_NAME), None)
    if collector is None or not hasattr(collector, "_latest_data_health"):
        return {"degraded": False, "stale": False,
                "last_collect_ago_sec": None, "degraded_symbols": [],
                "present": False}
    h = collector._latest_data_health or {}
    last_ts = h.get("last_collect_ts")
    if last_ts is None:
        ago = None
        stale = False  # 从未采集（启动初期），不误报
    else:
        ago = int(now - last_ts)
        stale = ago > data_stale_timeout_sec
    return {"degraded": bool(h.get("any_degraded", False)),
            "stale": stale,
            "last_collect_ago_sec": ago,
            "degraded_symbols": list(h.get("degraded_symbols", [])),
            "present": True}


def build_health_snapshot(agents, bus_metrics, now, *,
                          stall_timeout_sec, backlog_warn_pending,
                          data_stale_timeout_sec, base_stats):
    """聚合健康快照。

    agents: 可迭代 BaseAgent（research + trading），只读其实例字段。
    bus_metrics: MessageBus.get_metrics() 返回的 dict（含 '_dlq_size' 键）。
    now: float 时间戳（调用方传入，便于测试）。
    base_stats: Orchestrator 现成统计（agents_registered/tasks_alive/tasks_failed/
                halted_symbols/bus_dlq_size），原样透传保持向后兼容。
    """
    agents = list(agents)
    snapshot = dict(base_stats)
    snapshot["ts"] = now
    snapshot["loop_health"] = _loop_health(agents, now, stall_timeout_sec)
    snapshot["queue_health"] = _queue_health(bus_metrics, backlog_warn_pending)
    snapshot["llm_health"] = _llm_health(agents)
    snapshot["data_health"] = _data_health(agents, now, data_stale_timeout_sec)
    return snapshot
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_health_snapshot.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/health_snapshot.py tests/test_health_snapshot.py
git commit -m "feat(health): build_health_snapshot 纯函数聚合四维度健康 (#95)"
```

---

## Task 4: config_loader 三个阈值

**Files:**
- Modify: `utils/config_loader.py:23-72`（HARD_LIMITS）、`utils/config_loader.py:76+`（DEFAULTS）、`utils/config_loader.py:296-299`（env_map）
- Test: `tests/test_health_snapshot.py`（追加 1 个配置存在性测试，避免新建文件）

- [ ] **Step 1: Write the failing test**

在 `tests/test_health_snapshot.py` 末尾追加：

```python
def test_health_thresholds_in_defaults_and_hard_limits():
    from utils.config_loader import DEFAULTS, HARD_LIMITS
    assert DEFAULTS["agent_stall_timeout_sec"] == 60
    assert DEFAULTS["queue_backlog_warn_pending"] == 200
    assert DEFAULTS["data_stale_timeout_sec"] == 180
    assert HARD_LIMITS["agent_stall_timeout_sec"] == (10, 3600)
    assert HARD_LIMITS["queue_backlog_warn_pending"] == (50, 1000)
    assert HARD_LIMITS["data_stale_timeout_sec"] == (30, 3600)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_health_snapshot.py::test_health_thresholds_in_defaults_and_hard_limits -v`
Expected: FAIL — `KeyError: 'agent_stall_timeout_sec'`

- [ ] **Step 3: Add to HARD_LIMITS**

在 `utils/config_loader.py` HARD_LIMITS 末尾（现 L71 `research_min_open_interest_usd` 之后、L72 `}` 之前）加：

```python
    "research_min_open_interest_usd": (0.0, 10_000_000_000.0),
    # Agent health supervisor (#95) — observability-only 阈值
    "agent_stall_timeout_sec": (10, 3600),
    "queue_backlog_warn_pending": (50, 1000),
    "data_stale_timeout_sec": (30, 3600),
}
```

- [ ] **Step 4: Add to DEFAULTS**

在 `utils/config_loader.py` DEFAULTS 里 `research_min_*` 附近（或 DEFAULTS dict 末尾闭合 `}` 之前）加：

```python
    # Agent health supervisor (#95)
    "agent_stall_timeout_sec": 60,
    "queue_backlog_warn_pending": 200,
    "data_stale_timeout_sec": 180,
```

- [ ] **Step 5: Add to env_map**

在 `utils/config_loader.py:296-298` env_map 的 `RESEARCH_MIN_OPEN_INTEREST_USD` 之后加：

```python
        "RESEARCH_MIN_OPEN_INTEREST_USD": ("research_min_open_interest_usd", float),
        # Agent health supervisor (#95)
        "AGENT_STALL_TIMEOUT_SEC": ("agent_stall_timeout_sec", float),
        "QUEUE_BACKLOG_WARN_PENDING": ("queue_backlog_warn_pending", int),
        "DATA_STALE_TIMEOUT_SEC": ("data_stale_timeout_sec", float),
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python3 -m pytest tests/test_health_snapshot.py -v`
Expected: PASS (9 passed)

- [ ] **Step 7: Commit**

```bash
git add utils/config_loader.py tests/test_health_snapshot.py
git commit -m "feat(config): agent health supervisor 三阈值 + env 覆盖 (#95)"
```

---

## Task 5: Orchestrator 接入 builder（扩展 agent_health.json）

**Files:**
- Modify: `agents/orchestrator.py:315-347`（`_write_agent_health` 接 builder + 存 snapshot）、`agents/orchestrator.py:39-42`（`__init__` 加阈值与 snapshot 缓存）
- Test: `tests/test_health_alert_transitions.py`（先建文件，测 snapshot 扩展键）

- [ ] **Step 1: Write the failing test**

```python
# tests/test_health_alert_transitions.py
import json
import time
import pytest
from agents.message_bus import MessageBus


@pytest.fixture(autouse=True)
def _reset_bus():
    MessageBus._instance = None
    yield
    MessageBus._instance = None


def _make_orch(tmp_path, monkeypatch):
    """构造一个最小可测的 Orchestrator，state 路径指向 tmp。"""
    from utils import state_paths
    from agents.orchestrator import Orchestrator

    # 把 agent_health 路径重定向到 tmp
    real = state_paths.get_state_paths()
    class _P:
        agent_health = str(tmp_path / "agent_health.json")
    monkeypatch.setattr(state_paths, "get_state_paths", lambda *a, **k: _P())

    orch = Orchestrator(config={})
    return orch


def test_write_agent_health_includes_four_dimensions(tmp_path, monkeypatch):
    orch = _make_orch(tmp_path, monkeypatch)
    orch._write_agent_health()
    with open(tmp_path / "agent_health.json") as f:
        snap = json.load(f)
    # 向后兼容键保留
    for k in ("ts", "agents_registered", "tasks_alive", "tasks_failed",
              "halted_symbols", "bus_dlq_size"):
        assert k in snap
    # 新增四维度
    assert "loop_health" in snap
    assert "queue_health" in snap
    assert "llm_health" in snap
    assert "data_health" in snap
    assert snap["llm_health"]["degraded"] is False
```

> 注：若 `Orchestrator(config={})` 构造需要额外参数，按 `agents/orchestrator.py` 实际 `__init__` 签名调整；目标是拿到一个能调 `_write_agent_health()` 的实例（无 agent 注册时 agents 列表为空，四维度应全健康）。

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_health_alert_transitions.py::test_write_agent_health_includes_four_dimensions -v`
Expected: FAIL — `KeyError: 'loop_health'`（snapshot 还没扩展）

- [ ] **Step 3: Add thresholds + snapshot cache in `__init__`**

在 `agents/orchestrator.py` `__init__`（现 L39-42 健康相关字段附近）加：

```python
        self._latest_health_snapshot = None
        self._health_alert_state = {"loop": False, "queue": False,
                                    "llm": False, "data": False}
        cfg = self.config or {}
        self._stall_timeout_sec = cfg.get("agent_stall_timeout_sec", 60)
        self._backlog_warn_pending = cfg.get("queue_backlog_warn_pending", 200)
        self._data_stale_timeout_sec = cfg.get("data_stale_timeout_sec", 180)
```

> 若 `self.config` 在 `__init__` 中尚未赋值，把这几行移到 config 赋值之后。

- [ ] **Step 4: Rewire `_write_agent_health` through the builder**

把 `agents/orchestrator.py:315-347` 的 `_write_agent_health` 改为（base_stats 计算不变，最后用 builder 组装）：

```python
    def _write_agent_health(self):
        """F-TG-004 + #95: 写 data/<ns_>agent_health.json（含四维度健康）。失败 logger.warning 不抛。"""
        try:
            from utils.state_paths import get_state_paths
            from utils.atomic_io import atomic_write_json
            from agents.message_bus import MessageBus
            from utils.health_snapshot import build_health_snapshot

            tasks_alive, failed = self._collect_task_stats()
            tasks_failed = len(failed)
            self._latest_failed_tasks = failed

            agents_registered = len(self._research_agents) + len(self._trading_agents)

            try:
                bus = MessageBus.get_instance()
                dlq_size = len(getattr(bus, '_dead_letter', []))
                bus_metrics = bus.get_metrics()
            except Exception:
                dlq_size = 0
                bus_metrics = {}

            base_stats = {
                'agents_registered': agents_registered,
                'tasks_alive': tasks_alive,
                'tasks_failed': tasks_failed,
                'halted_symbols': dict(self._latest_halts_snapshot),
                'bus_dlq_size': dlq_size,
            }
            snapshot = build_health_snapshot(
                self._research_agents + self._trading_agents,
                bus_metrics,
                time.time(),
                stall_timeout_sec=self._stall_timeout_sec,
                backlog_warn_pending=self._backlog_warn_pending,
                data_stale_timeout_sec=self._data_stale_timeout_sec,
                base_stats=base_stats,
            )
            self._latest_health_snapshot = snapshot
            path = get_state_paths().agent_health
            atomic_write_json(path, snapshot)
            return dlq_size
        except Exception as e:
            self.logger.warning(f"[AgentHealth] 写入失败: {e}")
            return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_health_alert_transitions.py::test_write_agent_health_includes_four_dimensions -v`
Expected: PASS

- [ ] **Step 6: Run regression on existing agent_health tests**

Run: `python3 -m pytest tests/test_tg_status_enhancement.py tests/test_dlq_growth_alert.py -v`
Expected: PASS（向后兼容键未动）

- [ ] **Step 7: Commit**

```bash
git add agents/orchestrator.py tests/test_health_alert_transitions.py
git commit -m "feat(orchestrator): _write_agent_health 接 health_snapshot builder (#95)"
```

---

## Task 6: Orchestrator 边沿告警状态机

**Files:**
- Modify: `agents/orchestrator.py`（新增 `_maybe_alert_health_transitions`）、`agents/orchestrator.py:402-407`（`_health_loop` 调用）
- Test: `tests/test_health_alert_transitions.py`（追加）

- [ ] **Step 1: Write the failing test**

在 `tests/test_health_alert_transitions.py` 末尾追加：

```python
class _CapturingBus:
    """捕获 publish 的 telegram_alert，供断言。"""
    def __init__(self):
        self.published = []

    async def publish(self, sender, topic, payload, to, **kwargs):
        self.published.append((topic, payload))


def _snap_with(loop=False, queue=False, llm=False, data=False):
    return {
        "loop_health": {"stalled_count": 1 if loop else 0,
                        "stalled": [{"name": "judge", "idle_sec": 99}] if loop else []},
        "queue_health": {"backlogged_count": 1 if queue else 0,
                         "max_pending": 300 if queue else 0,
                         "backlogged": [{"name": "reviewer", "pending": 300}] if queue else []},
        "llm_health": {"degraded": llm,
                       "degraded_agents": [{"name": "tech", "consecutive_failures": 4}] if llm else []},
        "data_health": {"degraded": data, "stale": False,
                        "degraded_symbols": ["ETH-USDT"] if data else [], "present": True},
    }


@pytest.mark.asyncio
async def test_alert_fires_once_on_edge_then_silent(tmp_path, monkeypatch):
    orch = _make_orch(tmp_path, monkeypatch)
    bus = _CapturingBus()
    orch.bus = bus

    await orch._maybe_alert_health_transitions(_snap_with(llm=True))
    await orch._maybe_alert_health_transitions(_snap_with(llm=True))  # 持续不健康，静默
    types = [p["type"] for _, p in bus.published]
    assert types.count("health_llm") == 1            # 只发一次
    assert "health_llm_recovered" not in types


@pytest.mark.asyncio
async def test_alert_recovery_fires_once(tmp_path, monkeypatch):
    orch = _make_orch(tmp_path, monkeypatch)
    bus = _CapturingBus()
    orch.bus = bus

    await orch._maybe_alert_health_transitions(_snap_with(llm=True))   # 不健康
    await orch._maybe_alert_health_transitions(_snap_with(llm=False))  # 恢复
    types = [p["type"] for _, p in bus.published]
    assert types == ["health_llm", "health_llm_recovered"]


@pytest.mark.asyncio
async def test_dimensions_independent(tmp_path, monkeypatch):
    orch = _make_orch(tmp_path, monkeypatch)
    bus = _CapturingBus()
    orch.bus = bus

    await orch._maybe_alert_health_transitions(_snap_with(loop=True, data=True))
    types = sorted(p["type"] for _, p in bus.published)
    assert types == ["health_data", "health_loop"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_health_alert_transitions.py -k "edge or recovery or independent" -v`
Expected: FAIL — `AttributeError: ... no attribute '_maybe_alert_health_transitions'`

- [ ] **Step 3: Implement the state machine**

在 `agents/orchestrator.py` 紧接 `_maybe_alert_task_failure`（现 L400 之后）新增：

```python
    @staticmethod
    def _health_dim_status(snapshot):
        """从 snapshot 抽出四维度 (dim, unhealthy, warn_message)。"""
        loop = snapshot.get("loop_health", {})
        queue = snapshot.get("queue_health", {})
        llm = snapshot.get("llm_health", {})
        data = snapshot.get("data_health", {})

        loop_bad = loop.get("stalled_count", 0) > 0
        queue_bad = queue.get("backlogged_count", 0) > 0
        llm_bad = bool(llm.get("degraded", False))
        data_bad = bool(data.get("degraded", False) or data.get("stale", False))

        loop_names = ", ".join(s["name"] for s in loop.get("stalled", []))
        queue_names = ", ".join(f"{s['name']}({s['pending']})" for s in queue.get("backlogged", []))
        llm_names = ", ".join(s["name"] for s in llm.get("degraded_agents", []))
        data_syms = ", ".join(data.get("degraded_symbols", [])) or ("stale" if data.get("stale") else "")

        return [
            ("loop", loop_bad, f"🩺 健康监控：{loop.get('stalled_count', 0)} 个 agent loop 卡死（{loop_names}）"),
            ("queue", queue_bad, f"🩺 健康监控：{queue.get('backlogged_count', 0)} 个 agent 队列积压（{queue_names}）"),
            ("llm", llm_bad, f"🩺 健康监控：LLM 降级（{llm_names}）"),
            ("data", data_bad, f"🩺 健康监控：数据降级（{data_syms}）"),
        ]

    async def _maybe_alert_health_transitions(self, snapshot):
        """四维度健康↔不健康跳变各发一次 telegram_alert（边沿 + 恢复，持续期间静默）。

        observability-only：不自动 halt / 不影响决策。DLQ 与 task_failed 不并入此机，
        各自语义独立。Judge 的 risk_alert{llm_degraded} 是决策路径，与此告警互不替代。
        """
        if not snapshot:
            return
        from agents.message_bus import MessageBus
        try:
            bus = self.bus or MessageBus.get_instance()
        except Exception:
            return
        for dim, unhealthy, message in self._health_dim_status(snapshot):
            prev = self._health_alert_state.get(dim, False)
            if unhealthy and not prev:
                payload = {"level": "warning", "type": f"health_{dim}", "message": message}
            elif not unhealthy and prev:
                payload = {"level": "info", "type": f"health_{dim}_recovered",
                           "message": f"🩺 健康监控：{dim} 已恢复"}
            else:
                self._health_alert_state[dim] = unhealthy
                continue
            try:
                await bus.publish("orchestrator", "telegram_alert", payload, "broadcast")
            except Exception as e:
                self.logger.warning(f"[Health Alert] 发布失败 ({dim}): {e}")
            self._health_alert_state[dim] = unhealthy
```

- [ ] **Step 4: Wire into `_health_loop`**

把 `agents/orchestrator.py:402-407` 的 `_health_loop` 循环体改为（在 task failure 告警后加一行）：

```python
    async def _health_loop(self):
        """F-TG-004: 写 agent_health.json；P2-16: DLQ 增长 + 失败任务告警；#95: 四维度跳变告警。"""
        while not self._shutdown_event.is_set():
            dlq_size = self._write_agent_health()
            await self._maybe_alert_dlq_growth(dlq_size)
            await self._maybe_alert_task_failure(self._latest_failed_tasks)
            await self._maybe_alert_health_transitions(self._latest_health_snapshot)
            try:
                await asyncio.wait_for(
```

> 仅在 `_maybe_alert_task_failure(...)` 行后插入 `_maybe_alert_health_transitions` 一行，其余 `_health_loop` 不变。

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_health_alert_transitions.py -v`
Expected: PASS（4 passed）

- [ ] **Step 6: Commit**

```bash
git add agents/orchestrator.py tests/test_health_alert_transitions.py
git commit -m "feat(orchestrator): 四维度健康边沿告警+恢复通知 (#95)"
```

---

## Task 7: Telegram `/status` 总括行

**Files:**
- Modify: `agents/trading/telegram_notifier.py:783-802`（`_cmd_status` health 段尾部追加总括行）
- Test: `tests/test_health_telegram_display.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_health_telegram_display.py
import pytest
from agents.trading.telegram_notifier import TelegramNotifier


def _summary(health):
    # 纯渲染：直接测静态格式化函数，避免起 Telegram。
    return TelegramNotifier._format_health_summary(health)


def test_summary_all_green():
    health = {
        "loop_health": {"stalled_count": 0},
        "queue_health": {"backlogged_count": 0},
        "llm_health": {"degraded": False},
        "data_health": {"degraded": False, "stale": False},
    }
    assert _summary(health) == "─ 健康: ✓"


def test_summary_lists_only_bad_dims():
    health = {
        "loop_health": {"stalled_count": 1},
        "queue_health": {"backlogged_count": 2},
        "llm_health": {"degraded": True},
        "data_health": {"degraded": False, "stale": False},
    }
    s = _summary(health)
    assert s.startswith("─ 健康: ⚠")
    assert "1 stall" in s
    assert "2 backlog" in s
    assert "LLM降级" in s
    assert "data" not in s            # data 健康，不列


def test_summary_missing_snapshot():
    assert _summary(None) == "─ 健康: ?（快照缺失）"


def test_summary_data_stale_counts():
    health = {
        "loop_health": {"stalled_count": 0},
        "queue_health": {"backlogged_count": 0},
        "llm_health": {"degraded": False},
        "data_health": {"degraded": False, "stale": True},
    }
    assert "data降级" in _summary(health)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_health_telegram_display.py -v`
Expected: FAIL — `AttributeError: type object 'TelegramNotifier' has no attribute '_format_health_summary'`

- [ ] **Step 3: Add `_format_health_summary` static method**

在 `agents/trading/telegram_notifier.py` 紧邻 `_read_agent_health`（现 L509 之前或之后）加：

```python
    @staticmethod
    def _format_health_summary(health) -> str:
        """#95: /status 末尾健康总括行，只列异常维度。"""
        if not health:
            return "─ 健康: ?（快照缺失）"
        bad = []
        n_stall = health.get("loop_health", {}).get("stalled_count", 0)
        if n_stall:
            bad.append(f"{n_stall} stall")
        n_backlog = health.get("queue_health", {}).get("backlogged_count", 0)
        if n_backlog:
            bad.append(f"{n_backlog} backlog")
        if health.get("llm_health", {}).get("degraded", False):
            bad.append("LLM降级")
        dh = health.get("data_health", {})
        if dh.get("degraded", False) or dh.get("stale", False):
            bad.append("data降级")
        if not bad:
            return "─ 健康: ✓"
        return "─ 健康: ⚠ " + " / ".join(bad)
```

- [ ] **Step 4: Append summary line in `_cmd_status`**

在 `agents/trading/telegram_notifier.py:783-802` 的 health 段——把现有 `if health:` 分支末尾（L800 per-symbol halt 之后）追加，并在 `else` 分支也用总括兜底。具体：在 L800 之后、L801 `else:` 之前插入：

```python
                text += f"\n─ Per-symbol halt: {len(halts)} ({halt_str}{suffix})"
            text += f"\n{self._format_health_summary(health)}"
        else:
            text += "\n─ Health: ?（agent_health.json 缺失）"
```

> 即把总括行加在 `if health:` 块内最后一行（halt 行之后、`else` 之前），保证有 snapshot 时多出一行 `─ 健康: ✓/⚠ …`。

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_health_telegram_display.py -v`
Expected: PASS（4 passed）

- [ ] **Step 6: Commit**

```bash
git add agents/trading/telegram_notifier.py tests/test_health_telegram_display.py
git commit -m "feat(tg): /status 健康总括行 (#95)"
```

---

## Task 8: Telegram `/health` 明细命令

**Files:**
- Modify: `agents/trading/telegram_notifier.py:469-484`（注册 `/health`）、新增 `_cmd_health` 与 `_format_health_detail`
- Test: `tests/test_health_telegram_display.py`（追加）

- [ ] **Step 1: Write the failing test**

在 `tests/test_health_telegram_display.py` 末尾追加：

```python
def _detail(health):
    return TelegramNotifier._format_health_detail(health, now=2000.0)


def test_detail_all_green():
    health = {
        "ts": 1990.0,
        "loop_health": {"stalled_count": 0, "stalled": []},
        "queue_health": {"backlogged_count": 0, "max_pending": 12, "backlogged": []},
        "llm_health": {"degraded": False, "degraded_agents": []},
        "data_health": {"degraded": False, "stale": False, "last_collect_ago_sec": 23,
                        "degraded_symbols": [], "present": True},
    }
    s = _detail(health)
    assert "🩺 Agent 健康明细" in s
    assert "Loop:  ✓" in s
    assert "Queue: ✓" in s
    assert "LLM:   ✓" in s
    assert "Data:  ✓" in s


def test_detail_shows_offenders():
    health = {
        "ts": 1990.0,
        "loop_health": {"stalled_count": 1, "stalled": [{"name": "judge", "idle_sec": 73}]},
        "queue_health": {"backlogged_count": 0, "max_pending": 5, "backlogged": []},
        "llm_health": {"degraded": True, "degraded_agents": [{"name": "tech", "consecutive_failures": 4}]},
        "data_health": {"degraded": False, "stale": False, "last_collect_ago_sec": 9,
                        "degraded_symbols": [], "present": True},
    }
    s = _detail(health)
    assert "judge 空闲 73s" in s
    assert "tech 连续失败 4" in s


def test_detail_missing_snapshot():
    s = TelegramNotifier._format_health_detail(None, now=2000.0)
    assert "健康快照缺失" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_health_telegram_display.py -k detail -v`
Expected: FAIL — `AttributeError: ... no attribute '_format_health_detail'`

- [ ] **Step 3: Add `_format_health_detail` static method**

在 `agents/trading/telegram_notifier.py`（紧邻 `_format_health_summary`）加：

```python
    @staticmethod
    def _format_health_detail(health, now=None) -> str:
        """#95: /health per-dimension 明细。"""
        if not health:
            return "🩺 健康快照缺失（orchestrator 未写入或文件不可读）"
        import time as _t
        now = now if now is not None else _t.time()
        lines = ["🩺 Agent 健康明细"]

        loop = health.get("loop_health", {})
        if loop.get("stalled_count", 0):
            lines.append(f"Loop:  ⚠ {loop['stalled_count']} stalled")
            for s in loop.get("stalled", []):
                lines.append(f"  • {s['name']} 空闲 {s['idle_sec']}s")
        else:
            lines.append("Loop:  ✓")

        q = health.get("queue_health", {})
        if q.get("backlogged_count", 0):
            lines.append(f"Queue: ⚠ {q['backlogged_count']} backlog")
            for s in q.get("backlogged", []):
                lines.append(f"  • {s['name']} pending {s['pending']}")
        else:
            lines.append(f"Queue: ✓ (max pending {q.get('max_pending', 0)})")

        llm = health.get("llm_health", {})
        if llm.get("degraded", False):
            lines.append("LLM:   ⚠ 降级")
            for s in llm.get("degraded_agents", []):
                lines.append(f"  • {s['name']} 连续失败 {s.get('consecutive_failures', '?')}")
        else:
            lines.append("LLM:   ✓")

        d = health.get("data_health", {})
        if d.get("degraded", False) or d.get("stale", False):
            tag = "降级" if d.get("degraded") else "陈旧"
            syms = ", ".join(d.get("degraded_symbols", [])) or "—"
            lines.append(f"Data:  ⚠ {tag}（{syms}）")
        else:
            ago = d.get("last_collect_ago_sec")
            ago_str = f"上次采集 {int(ago)}s 前" if ago is not None else "尚未采集"
            lines.append(f"Data:  ✓ {ago_str}")

        ts = health.get("ts")
        if ts:
            lines.append(f"（快照 {int(now - ts)}s 前）")
        return "\n".join(lines)
```

- [ ] **Step 4: Add `_cmd_health` handler + register**

新增 handler（紧邻 `_cmd_halts`，现 L519 附近）：

```python
    async def _cmd_health(self):
        """#95: 展示 agent 健康明细。"""
        health = self._read_agent_health()
        await self._send_message(self._format_health_detail(health))
```

在 handlers dict（`agents/trading/telegram_notifier.py:483`，`/paper_gap` 行之后）注册：

```python
            '/paper_gap': self._cmd_paper_gap,                # paper dual-track gap
            '/health': self._cmd_health,                      # #95 agent health detail
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_health_telegram_display.py -v`
Expected: PASS（7 passed）

- [ ] **Step 6: Commit**

```bash
git add agents/trading/telegram_notifier.py tests/test_health_telegram_display.py
git commit -m "feat(tg): /health per-dimension 明细命令 (#95)"
```

---

## Task 9: 全量回归 + 基线对齐 + 收尾

**Files:**
- Modify: `docs/to-do-list.md`（#95 标 DONE）、`CLAUDE.md` 命令清单加 `/health`、`docs/handoff.md`（里程碑 + 基线）

- [ ] **Step 1: Run full suite**

Run: `python3 -m pytest -q`
Expected: PASS，新基线 = 1102 + 本 change 新增测试数（约 +25：3 心跳 + 3 collector + 9 snapshot/config + 4 alert + 7 telegram，最终以实跑为准）。

- [ ] **Step 2: compileall sanity**

Run: `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q agents/ utils/`
Expected: 无输出（全部编译通过）

- [ ] **Step 3: Update docs**

- `docs/to-do-list.md`：把 #95 那行从 `OPEN` 改 `DONE 2026-06-12`，落地列填实现摘要（health_snapshot builder + 四维度 + /status 总括 + /health + 边沿告警），验收列填测试文件与基线。
- `CLAUDE.md`：TG 命令清单加 `/health`；当前基线更新为实跑值。
- `docs/handoff.md`：里程碑表加一行 `Agent Health Supervisor | 2026-06-12 | ... | <新基线> | docs/superpowers/specs/2026-06-12-agent-health-supervisor-design.md`；当前阶段基线更新。

- [ ] **Step 4: Commit docs**

```bash
git add docs/to-do-list.md CLAUDE.md docs/handoff.md
git commit -m "docs: close #95 Agent Health Supervisor + 基线对齐"
```

- [ ] **Step 5: 汇报**

汇报实跑基线数字、新增测试数、改动文件清单；提示后续可选项（tick-loop stall 专项告警留作 backlog）。**不自动 push、不自动合并 main**——按项目惯例等用户决定走 comet 归档还是直接合并。

---

## Self-Review

**1. Spec coverage**

| Spec 章节 | 实现任务 |
|---|---|
| §4.1 BaseAgent 心跳 | Task 1 |
| §4.2 collector `_latest_data_health` | Task 2 |
| §5 build_health_snapshot 纯函数 | Task 3 |
| §6 扩展 agent_health.json schema | Task 3（schema）+ Task 5（写入） |
| §7 告警状态机（边沿+恢复+独立） | Task 6 |
| §8 三配置 + clamp/env | Task 4 |
| §9.1 /status 总括行 | Task 7 |
| §9.2 /health 明细 | Task 8 |
| §10 测试矩阵 | Task 1/2/3/6/7/8 各自单测 |
| §11 红线（路径派生、write-only、配置 clamp） | Task 4（clamp 复用既有）+ Task 5（state_paths 派生未改） |
| 不需 event_backtest | Task 9（仅文档/回归，无决策路径改动） |

无遗漏需求。

**2. Placeholder scan**：每个 code step 均含完整代码；Task 9 Step 1/3 的"以实跑为准/实现摘要"是文档收尾的合理可变项，非代码占位。

**3. Type consistency**：`build_health_snapshot` 签名（Task 3 定义）与 Task 5 调用一致；snapshot 键名（`loop_health/queue_health/llm_health/data_health` 及子字段 `stalled_count/stalled/backlogged_count/backlogged/max_pending/degraded/degraded_agents/stale/degraded_symbols/last_collect_ago_sec`）在 Task 3/6/7/8 一致；`_format_health_summary`/`_format_health_detail`/`_maybe_alert_health_transitions`/`_health_dim_status`/`_update_data_health` 命名各任务间一致；`_health_alert_state` 键 `loop/queue/llm/data` 与 `_health_dim_status` 返回 dim 一致。
