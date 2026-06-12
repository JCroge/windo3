---
change: agent-tick-stall-detection
design-doc: docs/superpowers/specs/2026-06-12-agent-tick-stall-detection-design.md
archived-with: 2026-06-12-agent-tick-stall-detection
---

# Agent Tick-Loop Stall Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 #95 Agent Health Supervisor 的 loop-alive 维度增加 tick-loop 挂死检测——`tick()` 挂死而 message loop 仍健康时也能看见 + 告警。

**Architecture:** BaseAgent `_periodic_loop` 在 tick 前后盖 `_tick_enter_ts`/`_tick_exit_ts`；`health_snapshot._loop_health` 测量"当前 tick 已执行多久"（`enter>exit AND now-enter>120s`），并入 `loop_health`；orchestrator loop 维度 unhealthy 判定 + 告警 detail 区分 message vs tick；telegram `/health`+`/status` 展示。observability-only，沿用 #95 红线，不需 event_backtest。

**Tech Stack:** Python 3.9 / asyncio / pytest（默认 `-m "not network"`）。

**Baseline before start:** `1135 passed / 4 deselected / 1 warning`（branch `agent-tick-stall-detection`）

**关键设计事实**：最长健康单次 tick = ReviewerAgent 60s（研判层 on_message 驱动不阻塞 tick；3600s/1800s 都是 1s tick + 计数器）。扁平阈值 120s（2×）零误报。

archived-with: 2026-06-12-agent-tick-stall-detection
---

## Task 1: BaseAgent tick 埋点

**Files:**
- Modify: `agents/base.py` `__init__`（在 `_last_work_ts` 之后加 2 字段）、`_periodic_loop`（盖戳）
- Test: `tests/test_base_agent_heartbeat.py`（追加）

- [ ] **Step 1: 追加失败测试** 到 `tests/test_base_agent_heartbeat.py`（文件已存在，有 `_Probe`/`_reset_bus`/import asyncio,time,pytest）：

```python
class _TickProbe(BaseAgent):
    name = "tick_probe"
    subscriptions = []

    async def setup(self):
        pass

    async def on_message(self, msg):
        pass

    async def tick(self):
        await asyncio.sleep(0.05)


def test_init_tick_fields_default_zero():
    a = _TickProbe()
    assert a._tick_enter_ts == 0.0
    assert a._tick_exit_ts == 0.0


@pytest.mark.asyncio
async def test_periodic_loop_stamps_tick_enter_and_exit():
    a = _TickProbe()
    a._running = True
    task = asyncio.create_task(a._periodic_loop())
    await asyncio.sleep(0.25)   # 多轮 0.05s tick
    a._running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert a._tick_enter_ts > 0.0    # tick 前盖过
    assert a._tick_exit_ts > 0.0     # tick 后也盖过（至少完成一轮）
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_base_agent_heartbeat.py -k tick -v`
Expected: FAIL — `AttributeError: '_TickProbe' object has no attribute '_tick_enter_ts'`

- [ ] **Step 3: `__init__` 加字段**

在 `agents/base.py` `__init__`，`self._last_work_ts = 0.0` 之后加：

```python
        self._last_work_ts = 0.0    # 业务进度：处理到消息时刷新（仅 /health 展示，永不告警）
        self._tick_enter_ts = 0.0   # tick 前盖（tick-loop 挂死检测信号）
        self._tick_exit_ts = 0.0    # tick 后盖（正常返回才更新）
```

- [ ] **Step 4: `_periodic_loop` 盖戳**

把 `agents/base.py` 的 `_periodic_loop` 改为（仅加两行盖戳，except 分支不变）：

```python
    async def _periodic_loop(self):
        """独立周期任务，不阻塞消息消费"""
        while self._running and not self._should_stop:
            try:
                self._tick_enter_ts = time.time()
                await self.tick()
                self._tick_exit_ts = time.time()
            except asyncio.CancelledError:
                break
            except Exception as e:
                import traceback
                self.logger.error(f"tick错误: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(1)
```

> 说明：`_tick_exit_ts` 只在 tick 正常返回后盖。tick 抛异常时 enter 已盖、exit 未盖 → 短暂 mid-tick，但下一轮 loop（异常 sleep 1s 后）立即重盖 enter，`now-enter` 始终小，不误判为挂死（除非单次异常 tick 真卡 >120s）。`time` 已在文件顶部 import。

- [ ] **Step 5: 运行确认通过**

Run: `python3 -m pytest tests/test_base_agent_heartbeat.py -v`
Expected: PASS（原有 3 + 新增 2 = 5 passed）

- [ ] **Step 6: Commit**

```bash
git add agents/base.py tests/test_base_agent_heartbeat.py
git commit -m "feat(base): _periodic_loop tick 埋点 _tick_enter_ts/_tick_exit_ts (agent-tick-stall-detection)"
```
末尾加：`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

archived-with: 2026-06-12-agent-tick-stall-detection
---

## Task 2: health_snapshot tick-stall 检测

**Files:**
- Modify: `utils/health_snapshot.py`（`_loop_health` + `build_health_snapshot` 签名/调用）
- Test: `tests/test_health_snapshot.py`（更新 `_FakeAgent`/`CFG`/`_snap` + 追加用例）

- [ ] **Step 1: 更新测试 helper + 追加失败测试**

在 `tests/test_health_snapshot.py`：
(a) 给 `CFG` 加 `tick_stall_timeout_sec=120`：
```python
CFG = dict(stall_timeout_sec=60, backlog_warn_pending=200, data_stale_timeout_sec=180,
           tick_stall_timeout_sec=120)
```
(b) `_FakeAgent.__init__` 增加 tick 时间戳参数（默认 0.0，保持既有用例不变）：
```python
class _FakeAgent:
    def __init__(self, name, alive_ts=NOW, llm=None, data_health=None,
                 tick_enter_ts=0.0, tick_exit_ts=0.0):
        self.name = name
        self._last_alive_ts = alive_ts
        self._tick_enter_ts = tick_enter_ts
        self._tick_exit_ts = tick_exit_ts
        self.llm = llm
        if data_health is not None:
            self._latest_data_health = data_health
```
(c) `_snap` 透传新阈值：
```python
def _snap(agents, bus_metrics, base=None):
    return build_health_snapshot(
        agents, bus_metrics, NOW,
        stall_timeout_sec=CFG["stall_timeout_sec"],
        backlog_warn_pending=CFG["backlog_warn_pending"],
        data_stale_timeout_sec=CFG["data_stale_timeout_sec"],
        tick_stall_timeout_sec=CFG["tick_stall_timeout_sec"],
        base_stats=base or BASE,
    )
```
(d) 追加用例：
```python
def test_tick_stall_detected():
    # 正在 tick 中（enter>exit）且执行超 120s
    agents = [_FakeAgent("stuck", tick_enter_ts=NOW - 200, tick_exit_ts=NOW - 260)]
    s = _snap(agents, {"_dlq_size": 0})
    lh = s["loop_health"]
    assert lh["tick_stalled_count"] == 1
    assert lh["tick_stalled"][0]["name"] == "stuck"
    assert lh["tick_stalled"][0]["tick_sec"] == 200


def test_tick_stall_exact_boundary_not_stalled():
    # now-enter == 120 不算（严格 >）；121 才算
    a = [_FakeAgent("edge", tick_enter_ts=NOW - 120, tick_exit_ts=NOW - 130)]
    assert _snap(a, {"_dlq_size": 0})["loop_health"]["tick_stalled_count"] == 0
    a2 = [_FakeAgent("over", tick_enter_ts=NOW - 121, tick_exit_ts=NOW - 130)]
    assert _snap(a2, {"_dlq_size": 0})["loop_health"]["tick_stalled_count"] == 1


def test_tick_mid_within_budget_not_stalled():
    # 正在 tick 中但只跑了 30s（< 120）
    a = [_FakeAgent("busy", tick_enter_ts=NOW - 30, tick_exit_ts=NOW - 90)]
    assert _snap(a, {"_dlq_size": 0})["loop_health"]["tick_stalled_count"] == 0


def test_tick_between_ticks_not_stalled():
    # exit>=enter（tick 已完成，在 sleep 等下轮），无论多久都不算
    a = [_FakeAgent("idle", tick_enter_ts=NOW - 500, tick_exit_ts=NOW - 100)]
    assert _snap(a, {"_dlq_size": 0})["loop_health"]["tick_stalled_count"] == 0


def test_tick_unstarted_skipped():
    a = [_FakeAgent("fresh", tick_enter_ts=0.0, tick_exit_ts=0.0)]
    assert _snap(a, {"_dlq_size": 0})["loop_health"]["tick_stalled_count"] == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_health_snapshot.py -k tick -v`
Expected: FAIL — `TypeError: build_health_snapshot() got an unexpected keyword argument 'tick_stall_timeout_sec'`（或 KeyError tick_stalled_count）

- [ ] **Step 3: 改 `_loop_health`**

把 `utils/health_snapshot.py` 的 `_loop_health` 改为（同时算 message-stall 与 tick-stall）：

```python
def _loop_health(agents, now, stall_timeout_sec, tick_stall_timeout_sec):
    stalled = []
    tick_stalled = []
    for a in agents:
        ts = getattr(a, "_last_alive_ts", 0.0) or 0.0
        if ts > 0.0 and (now - ts) > stall_timeout_sec:
            stalled.append({"name": getattr(a, "name", None), "idle_sec": int(now - ts)})
        enter = getattr(a, "_tick_enter_ts", 0.0) or 0.0
        exit_ts = getattr(a, "_tick_exit_ts", 0.0) or 0.0
        if enter > 0.0 and enter > exit_ts and (now - enter) > tick_stall_timeout_sec:
            tick_stalled.append({"name": getattr(a, "name", None), "tick_sec": int(now - enter)})
    return {"stalled_count": len(stalled), "stalled": stalled,
            "tick_stalled_count": len(tick_stalled), "tick_stalled": tick_stalled}
```

- [ ] **Step 4: 改 `build_health_snapshot` 签名与调用**

签名加 `tick_stall_timeout_sec`（keyword-only），调用 `_loop_health` 传入：

```python
def build_health_snapshot(agents, bus_metrics, now, *,
                          stall_timeout_sec, backlog_warn_pending,
                          data_stale_timeout_sec, tick_stall_timeout_sec, base_stats):
    ...
    snapshot["loop_health"] = _loop_health(agents, now, stall_timeout_sec, tick_stall_timeout_sec)
    ...
```

- [ ] **Step 5: 运行确认通过**

Run: `python3 -m pytest tests/test_health_snapshot.py -v`
Expected: PASS（原有 + 5 新增）。原有 loop 用例不受影响（_FakeAgent tick 默认 0.0 → tick_stalled_count=0）。

- [ ] **Step 6: Commit**

```bash
git add utils/health_snapshot.py tests/test_health_snapshot.py
git commit -m "feat(health): _loop_health 增加 tick-loop 挂死检测 (agent-tick-stall-detection)"
```
末尾加 Co-Authored-By 行。

archived-with: 2026-06-12-agent-tick-stall-detection
---

## Task 3: config 阈值 + Orchestrator 接线与告警

**Files:**
- Modify: `utils/config_loader.py`（1 阈值）、`agents/orchestrator.py`（`__init__` 读 config + build 调用传参 + `_health_dim_status` loop 判定/detail）
- Test: `tests/test_health_snapshot.py`（config 断言）、`tests/test_health_alert_transitions.py`（tick 告警）

- [ ] **Step 1: 追加失败测试**

(a) `tests/test_health_snapshot.py` 的 `test_health_thresholds_in_defaults_and_hard_limits` 追加断言：
```python
    assert DEFAULTS["agent_tick_stall_timeout_sec"] == 120
    assert HARD_LIMITS["agent_tick_stall_timeout_sec"] == (30, 3600)
```
(b) `tests/test_health_alert_transitions.py`：`_snap_with` 增加 `tick` 参数并在返回的 `loop_health` 带 tick 字段；新增告警测试：
```python
# _snap_with 改为带 tick 参数（loop_health 加 tick_stalled_count/tick_stalled）
def _snap_with(loop=False, queue=False, llm=False, data=False, tick=False):
    return {
        "loop_health": {"stalled_count": 1 if loop else 0,
                        "stalled": [{"name": "judge", "idle_sec": 99}] if loop else [],
                        "tick_stalled_count": 1 if tick else 0,
                        "tick_stalled": [{"name": "reviewer", "tick_sec": 200}] if tick else []},
        "queue_health": {"backlogged_count": 1 if queue else 0,
                         "max_pending": 300 if queue else 0,
                         "backlogged": [{"name": "reviewer", "pending": 300}] if queue else []},
        "llm_health": {"degraded": llm,
                       "degraded_agents": [{"name": "tech", "consecutive_failures": 4}] if llm else []},
        "data_health": {"degraded": data, "stale": False,
                        "degraded_symbols": ["ETH-USDT"] if data else [], "present": True},
    }


@pytest.mark.asyncio
async def test_tick_stall_fires_loop_alert(tmp_path, monkeypatch):
    orch = _make_orch(tmp_path, monkeypatch)
    bus = _CapturingBus()
    orch.bus = bus
    await orch._maybe_alert_health_transitions(_snap_with(tick=True))
    types = [p["type"] for _, p in bus.published]
    assert types == ["health_loop"]            # tick 卡死归 loop 维度
    msg = [p["message"] for _, p in bus.published][0]
    assert "tick" in msg                        # detail 区分 tick
```
> 注意：原有 `_snap_with` 调用点（不传 tick）默认 tick=False，行为不变。确认原有 loop 测试仍过。

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_health_snapshot.py::test_health_thresholds_in_defaults_and_hard_limits tests/test_health_alert_transitions.py::test_tick_stall_fires_loop_alert -v`
Expected: FAIL（KeyError 配置 / health_loop 未触发或 msg 无 tick）

- [ ] **Step 3: config_loader 加阈值**

`utils/config_loader.py`：HARD_LIMITS 在 `data_stale_timeout_sec` 后加 `"agent_tick_stall_timeout_sec": (30, 3600),`；DEFAULTS 在 `data_stale_timeout_sec` 后加 `"agent_tick_stall_timeout_sec": 120,`；env_map 加 `"AGENT_TICK_STALL_TIMEOUT_SEC": ("agent_tick_stall_timeout_sec", float),`。（先 Read 确认三处 #95 已加的 data_stale 位置，紧随其后加。）

- [ ] **Step 4: Orchestrator 读 config + 传参**

`agents/orchestrator.py` `__init__`，在 `self._data_stale_timeout_sec = ...` 之后加：
```python
        self._tick_stall_timeout_sec = cfg.get("agent_tick_stall_timeout_sec", 120)
```
`build_health_snapshot(...)` 调用加一行参数：
```python
                data_stale_timeout_sec=getattr(self, '_data_stale_timeout_sec', 180),
                tick_stall_timeout_sec=getattr(self, '_tick_stall_timeout_sec', 120),
                base_stats=base_stats,
```

- [ ] **Step 5: `_health_dim_status` loop 判定 + detail 区分**

把 `agents/orchestrator.py` `_health_dim_status` 的 loop 相关行改为（loop_bad 含 tick，message 区分）：
```python
        loop = snapshot.get("loop_health", {})
        n_msg_stall = loop.get("stalled_count", 0)
        n_tick_stall = loop.get("tick_stalled_count", 0)
        loop_bad = n_msg_stall > 0 or n_tick_stall > 0
        ...
        loop_parts = []
        if n_msg_stall:
            _mn = ", ".join(s.get("name") for s in loop.get("stalled", []))
            loop_parts.append(f"message-loop 卡死 {n_msg_stall}（{_mn}）")
        if n_tick_stall:
            _tn = ", ".join(s.get("name") for s in loop.get("tick_stalled", []))
            loop_parts.append(f"tick 卡死 {n_tick_stall}（{_tn}）")
        loop_msg = "🩺 健康监控：agent loop 异常 — " + "；".join(loop_parts)
```
并把 return 列表里 loop 那一项改为 `("loop", loop_bad, loop_msg),`。其余 queue/llm/data 三项不动。

- [ ] **Step 6: 运行确认通过**

Run: `python3 -m pytest tests/test_health_snapshot.py tests/test_health_alert_transitions.py -v`
Expected: PASS（含新增；原有 loop 告警用例仍过——`_snap_with(loop=True)` 现 tick_stalled_count=0，loop_bad 仍 True，message 含 "message-loop 卡死"）。

- [ ] **Step 7: Commit**

```bash
git add utils/config_loader.py agents/orchestrator.py tests/test_health_snapshot.py tests/test_health_alert_transitions.py
git commit -m "feat(orchestrator): tick-stall 阈值 + loop 维度并入 tick 卡死告警 detail (agent-tick-stall-detection)"
```
末尾加 Co-Authored-By 行。

archived-with: 2026-06-12-agent-tick-stall-detection
---

## Task 4: Telegram /health + /status 展示

**Files:**
- Modify: `agents/trading/telegram_notifier.py`（`_format_health_detail` Loop 段 + `_format_health_summary` 计数）
- Test: `tests/test_health_telegram_display.py`（追加）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_health_telegram_display.py` 追加：
```python
def test_detail_shows_tick_stalled():
    health = {
        "ts": 1990.0,
        "loop_health": {"stalled_count": 0, "stalled": [],
                        "tick_stalled_count": 1,
                        "tick_stalled": [{"name": "reviewer", "tick_sec": 200}]},
        "queue_health": {"backlogged_count": 0, "max_pending": 5, "backlogged": []},
        "llm_health": {"degraded": False, "degraded_agents": []},
        "data_health": {"degraded": False, "stale": False, "last_collect_ago_sec": 9,
                        "degraded_symbols": [], "present": True},
    }
    s = TelegramNotifier._format_health_detail(health, now=2000.0)
    assert "reviewer tick 卡死 200s" in s
    assert "Loop:  ⚠" in s


def test_summary_counts_tick_into_loop():
    health = {
        "loop_health": {"stalled_count": 1, "tick_stalled_count": 2},
        "queue_health": {"backlogged_count": 0},
        "llm_health": {"degraded": False},
        "data_health": {"degraded": False, "stale": False},
    }
    s = TelegramNotifier._format_health_summary(health)
    assert "3 stall" in s     # 1 message + 2 tick
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_health_telegram_display.py -k "tick or counts_tick" -v`
Expected: FAIL（无 tick 展示 / summary 只数 message stall）

- [ ] **Step 3: 改 `_format_health_detail` Loop 段**

把 `agents/trading/telegram_notifier.py` `_format_health_detail` 的 Loop 段（现 `loop = health.get("loop_health", {})` 到 `lines.append("Loop:  ✓")`）改为：
```python
        loop = health.get("loop_health", {})
        n_stall = loop.get("stalled_count", 0)
        n_tick = loop.get("tick_stalled_count", 0)
        if n_stall or n_tick:
            parts = []
            if n_stall:
                parts.append(f"{n_stall} message-loop")
            if n_tick:
                parts.append(f"{n_tick} tick")
            lines.append(f"Loop:  ⚠ {' + '.join(parts)} stalled")
            for s in loop.get("stalled", []):
                lines.append(f"  • {s.get('name', '?')} message-loop 空闲 {s.get('idle_sec', '?')}s")
            for s in loop.get("tick_stalled", []):
                lines.append(f"  • {s.get('name', '?')} tick 卡死 {s.get('tick_sec', '?')}s")
        else:
            lines.append("Loop:  ✓")
```

- [ ] **Step 4: 改 `_format_health_summary` loop 计数**

把 `_format_health_summary` 的 loop 计数段（现 `n_stall = ...stalled_count...; if n_stall: bad.append(...)`）改为：
```python
        loop = health.get("loop_health", {})
        n_stall = loop.get("stalled_count", 0) + loop.get("tick_stalled_count", 0)
        if n_stall:
            bad.append(f"{n_stall} stall")
```

- [ ] **Step 5: 运行确认通过**

Run: `python3 -m pytest tests/test_health_telegram_display.py -v`
Expected: PASS（原有 + 2 新增）。原有 detail/summary 用例：旧 health dict 无 tick_stalled_count → `.get(...,0)` = 0，行为不变。

- [ ] **Step 6: Commit**

```bash
git add agents/trading/telegram_notifier.py tests/test_health_telegram_display.py
git commit -m "feat(tg): /health+/status 展示 tick 卡死（并入 loop） (agent-tick-stall-detection)"
```
末尾加 Co-Authored-By 行。

archived-with: 2026-06-12-agent-tick-stall-detection
---

## Task 5: 全量回归 + 收尾

**Files:** Modify `openspec/changes/agent-tick-stall-detection/tasks.md`（勾选）

- [ ] **Step 1: 全量**

Run: `python3 -m pytest -q`
Expected: PASS，基线 1135 + 本 change 新增（约 +11：2 base + 5 snapshot + 1 config 断言 + 1 alert + 2 telegram，最终以实跑为准）。

- [ ] **Step 2: compileall**

Run: `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q agents/ utils/`
Expected: 无输出。

- [ ] **Step 3: 勾选 tasks.md**

把 `openspec/changes/agent-tick-stall-detection/tasks.md` 所有 `- [ ]` 改 `- [x]`，补实测基线数字。

- [ ] **Step 4: Commit**

```bash
git add openspec/changes/agent-tick-stall-detection/tasks.md
git commit -m "docs(comet): mark tasks complete (agent-tick-stall-detection, <实测基线>)"
```
末尾加 Co-Authored-By 行。

archived-with: 2026-06-12-agent-tick-stall-detection
---

## Self-Review

**1. Spec coverage**

| Delta spec Requirement / Scenario | 实现任务 |
|---|---|
| Tick-loop hang detection（埋点） | Task 1 |
| Hung tick flagged / 边界不算 / between-ticks 不算 / unstarted 跳过 | Task 2 |
| message vs tick 区分（告警 detail） | Task 3 Step 5 |
| 并入 loop_health（loop unhealthy = stalled OR tick_stalled） | Task 3 Step 5 |
| /health 列 tick 卡死 / /status 计入 | Task 4 |
| 阈值 default 在 hard limits 内 | Task 3 Step 3 |

**2. Placeholder scan**：每 code step 含完整代码；Task 5 的"实测基线"是收尾文档可变项。

**3. Type consistency**：`build_health_snapshot` 新增 keyword `tick_stall_timeout_sec`（Task 2 定义，Task 3 Step 4 调用一致）；`loop_health` 子键 `tick_stalled_count`/`tick_stalled`/`tick_sec` 在 Task 2/3/4 一致；config key `agent_tick_stall_timeout_sec` 三处一致；orchestrator `_tick_stall_timeout_sec` 字段。
