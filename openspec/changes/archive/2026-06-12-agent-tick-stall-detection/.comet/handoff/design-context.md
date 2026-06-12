# Comet Design Handoff

- Change: agent-tick-stall-detection
- Phase: design
- Mode: compact
- Context hash: 57dc0def887919cdeca45268e766e764aa76f467c2afd3ac68c3cea132fdf1e6

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/agent-tick-stall-detection/proposal.md

- Source: openspec/changes/agent-tick-stall-detection/proposal.md
- Lines: 1-28
- SHA256: 2324b74542af5d502b5c6a9bade3e52186817a2a504d2764a65e61e64565f6e8

```md
## Why

Agent Health Supervisor (#95) 的 loop-alive 维度用 `BaseAgent._last_alive_ts`（`_message_loop` 0.5s 有界轮询心跳）检测 **message 循环**卡死。但每个 agent 还有第二条独立循环 `_periodic_loop`（调 `tick()`）。`tick()` 若挂死（如网络调用无超时阻塞在 `await` 里）而 message loop 仍健康时，**当前无任何检测**——agent 的周期性工作（采集/复盘/风控扫描）已停摆，但 `/status`/`/health` 仍显示该 agent 健康。

探索结论（关键事实）：各 agent 的 tick 节奏虽差异大，但**单次 tick 的健康执行时长是有界的**——最长的是 ReviewerAgent 的 60s 纯 sleep（研判层 4h 节奏由 orchestrator `_research_loop` 发 trigger 驱动，不阻塞 tick；3600s 复评用 1s tick + 计数器）。没有任何 agent 在单次 tick 内 sleep 超过 60s。因此一个**扁平阈值即可零误报**，无需 per-agent 配置——与 message-loop 心跳锚定 0.5s 轮询同样的优雅性。

## What Changes

- `BaseAgent._periodic_loop` 在 `tick()` 前后各盖一个时间戳 `_tick_enter_ts` / `_tick_exit_ts`（+2 实例字段）。
- `utils/health_snapshot.py` 的 `loop_health` 维度扩展：测量"**当前这次 tick 已执行多久**"——`_tick_enter_ts > _tick_exit_ts` 表示正在 tick 中，若 `now - _tick_enter_ts > AGENT_TICK_STALL_TIMEOUT_SEC` 则该 agent tick 挂死。新增 `tick_stalled_count` + `tick_stalled: [{name, tick_sec}]`。
- `config_loader` 新增 `AGENT_TICK_STALL_TIMEOUT_SEC`（默认 120 = 2× 最长健康单次 tick 60s）。
- Orchestrator 边沿告警状态机：loop 维度的 unhealthy 判定扩展为 `stalled_count > 0 OR tick_stalled_count > 0`，告警 detail 区分"message loop 卡死"与"tick 卡死"。
- Telegram `/health` 明细在 Loop 段下增列 tick 卡死的 agent。

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `agent-health-supervisor`：loop-alive 维度从"仅 message-loop 心跳"扩展为"message-loop 心跳 + tick-loop 挂死检测"，两者互补（前者抓事件循环级死，后者抓单 agent tick 级卡）。新增 tick-stall 子检测、阈值配置与告警 detail 区分。

## Impact

- **Modified**: `agents/base.py`（`_periodic_loop` 盖戳 + 2 字段）、`utils/health_snapshot.py`（`loop_health` 加 tick-stall）、`utils/config_loader.py`（1 阈值）、`agents/orchestrator.py`（loop unhealthy 判定 + 告警 detail）、`agents/trading/telegram_notifier.py`（`/health` 明细 + 可能 `/status` 总括）。
- **Test**: 扩展 `test_health_snapshot.py`（tick-stall 边界）、`test_base_agent_heartbeat.py`（tick 盖戳）、`test_health_alert_transitions.py`、`test_health_telegram_display.py`。
- **Behavioral**: tick 挂死的 agent 现在可见 + 告警。**observability-only write-only，零决策路径，不需 event_backtest**（与 #95 同性质红线）。
- **Non-goals**: 不改各 agent 的 tick() 实现（不重构 sleep 位置）；不做 per-agent tick 阈值（扁平 120s 已零误报）；不改 message-loop 心跳逻辑。
```

## openspec/changes/agent-tick-stall-detection/design.md

- Source: openspec/changes/agent-tick-stall-detection/design.md
- Lines: 1-57
- SHA256: d1b133a9033062383bdcbbcb699104fefb4c8ade28e354a167b914b98deb3819

```md
# Design — Agent Tick-Loop Stall Detection

> 高层决策见此；完整技术 RFC 见 Superpowers Design Doc（comet-design 阶段产出）。本 change 修改既有 capability `agent-health-supervisor`（#95）。

## 问题与现状

每个 BaseAgent 有两条独立 asyncio 循环：
- `_message_loop`：`bus.receive(timeout=0.5)` 0.5s 有界轮询 → `_last_alive_ts` 心跳（#95 已覆盖）。
- `_periodic_loop`：`while: await self.tick()` → **当前无心跳**。

tick() 挂死（`await` 在无超时的网络/锁上）不会 starve 事件循环（其它 agent 的 message loop 仍转 → `_last_alive_ts` 仍刷新），所以 #95 的 loop-alive 检测**看不见 tick 卡死**。

## 关键探索结论：单次 tick 健康时长有界

| agent | 单次 tick 健康时长 | 节奏来源 |
|---|---|---|
| 研判层 6 | 不 override tick（默认 1s） | orchestrator `_research_loop` 发 trigger，on_message 驱动 |
| MultiDataCollector / PositionAnalyst | ~1s | sleep 1s + 计数器分频（30s/300s/1800s/3600s） |
| MultiExecutor | ~5s | sleep 5s |
| TelegramNotifier | ~5-10s | poll + 5s timeout |
| PortfolioRiskGuard | ~10s | sleep 10s |
| PaperExecutor | ~30s | sleep 30s |
| **ReviewerAgent** | **~60s** | 60s 纯 sleep（最长） |

**没有任何 agent 单次 tick 健康执行超过 60s。** 因此扁平阈值 120s（2× 最长）零误报。

## 方案：测量"当前 tick 已执行多久"

```
BaseAgent._periodic_loop:
  while running:
    self._tick_enter_ts = time.time()   # tick 前
    await self.tick()
    self._tick_exit_ts = time.time()    # tick 后

supervisor (health_snapshot._loop_health 扩展):
  正在 tick 中 = _tick_enter_ts > _tick_exit_ts
  tick 挂死    = 正在 tick 中 AND (now - _tick_enter_ts) > AGENT_TICK_STALL_TIMEOUT_SEC(120)
```

为何不误报：健康 agent 的 `_tick_enter_ts` 在每次 tick 开始刷新；单次 tick ≤60s 后 `_tick_exit_ts` 追上，回到"不在 tick 中"。只有真卡死（tick 执行 >120s）才命中。`_tick_enter_ts <= 0`（未起跑）跳过。

## 关键决策

1. **扁平阈值，无 per-agent 配置**（锚定"最长健康单次 tick 60s"，与 #95 message-loop 锚定 0.5s 轮询同理）。

2. **并入 loop_health 维度（不单列第 5 维度）**：`loop_health` 加 `tick_stalled_count` / `tick_stalled`；loop 维度 unhealthy 判定扩展为 `stalled_count > 0 OR tick_stalled_count > 0`。复用 #95 已建的边沿告警状态机与 `/status`/`/health`，告警 detail 与 `/health` 明细**区分** message-loop 卡死 vs tick 卡死（ops 动作不同）。理由：tick 卡死本质是 agent loop 健康问题，归 loop 维度自然；避免新增维度的状态机/展示分叉。

3. **observability-only write-only**：沿用 #95 红线，严禁 gate/veto/halt；不需 event_backtest。

4. **不重构 agent tick()**：不动各 agent 的 sleep 位置/实现，纯外层埋点。

## 配置

| 参数 | 默认 | HARD | 含义 |
|---|---|---|---|
| `AGENT_TICK_STALL_TIMEOUT_SEC` | 120 | [30, 3600] | 当前 tick 执行超过此值算挂死（2× 最长健康单次 tick 60s） |
```

## openspec/changes/agent-tick-stall-detection/tasks.md

- Source: openspec/changes/agent-tick-stall-detection/tasks.md
- Lines: 1-16
- SHA256: d080e364b8f109ba1599d9435189170076e8ea7b2f9918c81071f18ec96f7f0b

```md
# Tasks

## 1. tick 埋点 (agent-health-supervisor)
- [ ] 1.1 `BaseAgent.__init__` 加 `_tick_enter_ts=0.0` / `_tick_exit_ts=0.0`；`_periodic_loop` 在 `await self.tick()` 前盖 enter、后盖 exit（CancelledError/Exception 路径不破坏）。测试 `test_base_agent_heartbeat.py`：tick 前后盖戳、enter>exit 表示 mid-tick。

## 2. builder tick-stall (agent-health-supervisor)
- [ ] 2.1 `utils/health_snapshot.py::_loop_health` 扩展：读 agents 的 `_tick_enter_ts`/`_tick_exit_ts`，`enter>exit AND now-enter>tick_stall_timeout_sec AND enter>0` → tick 挂死；`loop_health` 加 `tick_stalled_count` + `tick_stalled:[{name, tick_sec}]`。`build_health_snapshot` 加 `tick_stall_timeout_sec` 参数。测试 `test_health_snapshot.py`：tick-stall 检出 / 边界相等不算 / 未起跑跳过 / mid-tick 但未超时不算。
- [ ] 2.2 `config_loader` 加 `AGENT_TICK_STALL_TIMEOUT_SEC`=120（DEFAULTS/HARD_LIMITS [30,3600]/env_map）；Orchestrator 读取并传入 builder。

## 3. 告警与展示 (agent-health-supervisor)
- [ ] 3.1 Orchestrator `_health_dim_status`：loop 维度 unhealthy = `stalled_count>0 OR tick_stalled_count>0`，告警 message 区分 message-loop 卡死 vs tick 卡死。测试 `test_health_alert_transitions.py`：tick-stall 触发 loop 边沿告警。
- [ ] 3.2 Telegram `/health` 明细 Loop 段增列 tick 卡死 agent（`{name} tick {tick_sec}s`）；`/status` 总括 tick 卡死计入 loop。测试 `test_health_telegram_display.py`。

## 4. 验证与收尾
- [ ] 4.1 全量 `python3 -m pytest -q` 通过（基线 1135 + 新增）。
- [ ] 4.2 编译检查 `python3 -m compileall -q agents/ utils/` 通过。
```

## openspec/changes/agent-tick-stall-detection/specs/agent-health-supervisor/spec.md

- Source: openspec/changes/agent-tick-stall-detection/specs/agent-health-supervisor/spec.md
- Lines: 1-34
- SHA256: ae5f44dac5d5e86b881fd54b10b5ef32ae46458ec44f0629945ad9ebead5a4d4

```md
## ADDED Requirements

### Requirement: Tick-loop hang detection

Each agent SHALL stamp `_tick_enter_ts` immediately before each `tick()` invocation and `_tick_exit_ts` immediately after it returns, in `_periodic_loop`. The supervisor SHALL flag an agent's tick loop as hung when it is currently inside a tick (`_tick_enter_ts > _tick_exit_ts`) AND the current tick has been executing longer than `AGENT_TICK_STALL_TIMEOUT_SEC` (default 120, anchored at 2× the longest healthy single-tick duration of ~60s). Agents that have not yet entered a tick (`_tick_enter_ts <= 0`) MUST be skipped. This detection is complementary to the message-loop `_last_alive_ts` heartbeat: the former catches a hung periodic loop while the message loop remains healthy. The tick-hung signal MUST be surfaced within the existing `loop_health` dimension (`tick_stalled_count`, `tick_stalled` list of `{name, tick_sec}`), and the loop dimension's unhealthy predicate becomes `stalled_count > 0 OR tick_stalled_count > 0`. Alert detail and the `/health` view MUST distinguish a message-loop stall from a tick-loop hang, since they imply different operator actions. This detection is observability-only and MUST NOT gate, veto, halt, or otherwise affect any trading decision.

#### Scenario: Hung tick is flagged
- **WHEN** an agent is inside a `tick()` call (`_tick_enter_ts > _tick_exit_ts`) and the current tick has run longer than `AGENT_TICK_STALL_TIMEOUT_SEC`
- **THEN** the agent appears in `loop_health.tick_stalled` with its current tick seconds
- **AND** the loop dimension is reported unhealthy

#### Scenario: Healthy long tick within budget is not flagged
- **WHEN** an agent is inside a `tick()` call but the current tick has run for less than `AGENT_TICK_STALL_TIMEOUT_SEC` (e.g. a 60s reviewer sleep)
- **THEN** the agent is NOT flagged as tick-stalled

#### Scenario: Between ticks is not flagged
- **WHEN** an agent has completed its last tick (`_tick_exit_ts >= _tick_enter_ts`)
- **THEN** the agent is NOT flagged as tick-stalled regardless of elapsed time since the last tick

#### Scenario: Unstarted agent is skipped
- **WHEN** an agent has `_tick_enter_ts <= 0` (no tick has begun)
- **THEN** it is not flagged as tick-stalled

#### Scenario: Message-loop stall and tick hang are distinguished
- **WHEN** an alert fires for the loop dimension
- **THEN** the alert detail indicates whether the cause is a message-loop stall (`_last_alive_ts`) or a tick-loop hang (`_tick_enter_ts`)

### Requirement: Tick stall threshold is configurable and bounded

The tick-stall threshold `AGENT_TICK_STALL_TIMEOUT_SEC` (default 120) SHALL be defined in `config_loader` DEFAULTS with a HARD_LIMITS bound of `[30, 3600]` and an env-var override, following the existing config pattern. It affects observability/alert sensitivity only and MUST NOT relax any trading risk limit.

#### Scenario: Default within hard limits
- **WHEN** config is loaded with no override
- **THEN** `AGENT_TICK_STALL_TIMEOUT_SEC` resolves to 120, within its HARD_LIMITS range `[30, 3600]`
```

