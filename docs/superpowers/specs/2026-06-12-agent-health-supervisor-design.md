# Agent Health Supervisor — Design

- **日期**：2026-06-12
- **来源**：`docs/to-do-list.md` #95（OPEN）"Agent health supervisor — Orchestrator 增加 setup failure、loop alive、queue backlog、DLQ、LLM degraded、data degraded 状态；Telegram `/status` 或 health 输出能看见关键 agent 健康状态"
- **基线**：1102 passed / 4 deselected / 1 warning
- **性质**：**observability-only**。零决策路径改动、无 gate/veto/halt、不消费于 Judge/Reviewer。与 Data Source Provenance 同性质，**不需要 event_backtest 同构**。

## 1. 背景与现状

系统是两层 16-Agent 趋势交易系统（研判 6 + 交易 10），Orchestrator 已有 `_health_loop`（30s）写 `agent_health.json` 并被 Telegram `/status` 展示。2026-06-11 已落地两块健康可见性：

- **DLQ 增长告警**（P2-16）：`orchestrator._maybe_alert_dlq_growth`（`agents/orchestrator.py:349-374`），DLQ size 增长时发 `telegram_alert{type='bus_dlq_growth'}`，30s cadence 限流。
- **Agent task 故障告警**（P2-16 / agent-fault-visibility）：`_maybe_alert_task_failure`（`agents/orchestrator.py:376-400`），首次出现的失败任务发 `telegram_alert{type='agent_task_failed'}`，`_alerted_failed_tasks` set 去重。`base.run()` setup 失败打 traceback 再 raise（`agents/base.py:53-60`）。

`agent_health.json` 现有 schema（`agents/orchestrator.py:334-343`）：`ts / agents_registered / tasks_alive / tasks_failed / halted_symbols / bus_dlq_size`。

### #95 六维度现状对标

| 维度 | 现状 | 缺口 |
|---|---|---|
| **setup failure** | ✅ 已有 `agent_task_failed` 告警 + task done/exception 计数 | 无（保留现状） |
| **DLQ** | ✅ 已有 `bus_dlq_growth` 告警 + snapshot 展示 | 无（保留现状） |
| **loop alive** | ❌ 仅 `task.done()` 判活，无心跳，无法检测"未崩溃但卡死" | 需心跳埋点 + stall 检测 |
| **queue backlog** | ⚠️ `bus.get_metrics()` 有 per-agent `pending`，但无聚合/告警 | 需聚合 + 阈值告警 |
| **LLM degraded** | ⚠️ `LLMClient.degraded` 实例属性存在（`agents/llm_client.py:371-372`），Judge 单点 risk_alert，未全局聚合 | 需跨 agent 聚合 + 展示/告警 |
| **data degraded** | ⚠️ `market_data.payload.data_quality.degraded`（`multi_data_collector.py:343,399`）随消息丢失，无可读聚合态 | 需 collector 实例字段 + 聚合 |

**结论**：本 change 新增 **loop / queue / llm / data** 四维度的聚合 + 展示 + 状态跳变告警；**DLQ 与 setup failure 保持现状不动**（语义不同，已闭环），snapshot 仍展示 `bus_dlq_size`。

## 2. 目标与非目标

**目标**

1. Orchestrator 定期（复用 30s `_health_loop`）聚合 loop/queue/llm/data 四维度健康，扩展写入 `agent_health.json`。
2. 任一维度发生 健康→不健康 跳变时发一次 `telegram_alert`（warning）；不健康→健康时发一次（info，recovered）；持续不健康期间静默。
3. `/status` 末尾加一行健康总括；新增 `/health` 命令展示 per-dimension 明细。

**非目标（YAGNI）**

- 不做任何自动修复 / 自动 halt / 自动重启（observability-only）。
- 不据健康状态影响交易决策、ranking、gate（红线：与 provenance 一致，write-only）。
- 不重做 DLQ / task_failed 告警（已闭环）。
- 不做 tick-loop 专项 stall 告警（message-loop 心跳已覆盖 agent 级 liveness，tick 挂死属次要场景，留后续）。
- 不做 per-agent 节奏配置（告警维度锚定固定 0.5s 轮询，全局阈值即可）。

## 3. 架构

```
BaseAgent (心跳埋点)             MultiDataCollector (聚合字段)
  _last_alive_ts                   _latest_data_health
  _last_work_ts                          │
        │                                │
        └─────────────┬──────────────────┘
                      ▼
   utils/health_snapshot.py
     build_health_snapshot(agents, bus_metrics, now, cfg) -> snapshot dict
     纯函数：无 IO、无告警、无副作用
                      ▼
   Orchestrator._health_loop (30s)
     ├─ snapshot = build_health_snapshot(...)
     ├─ atomic_write_json(agent_health_path, snapshot)   # 扩展 schema
     └─ _maybe_alert_health_transitions(snapshot)        # 边沿 + 恢复
                      ▼
   TelegramNotifier
     /status  → 总括行（只列异常维度）
     /health  → per-dimension 明细 + 快照年龄
```

**单点收敛**：四维度聚合逻辑全部在 `health_snapshot.py` 一个纯函数内，Orchestrator 只负责"调用 + 写文件 + 跑告警状态机"。聚合与 IO/告警分离，便于单测。

## 4. 信号埋点（最小改动）

### 4.1 BaseAgent 心跳（`agents/base.py`，改 2 处）

新增两个实例字段（`__init__`）：

```python
self._last_alive_ts = 0.0   # loop 在转的证明（告警信号）
self._last_work_ts = 0.0    # 真正处理到消息（仅展示，永不告警）
```

`_message_loop`（现 `agents/base.py:73-85`）改动：

```python
async def _message_loop(self):
    while self._running and not self._should_stop:
        self._last_alive_ts = time.time()          # 新增：每迭代盖戳
        try:
            msg = await self.bus.receive(self.name, timeout=0.5)
            if msg:
                self._last_work_ts = time.time()    # 新增：处理到消息才盖
                await self.on_message(msg)
        except asyncio.CancelledError:
            break
        except Exception as e:
            ...
```

**关键事实**：`_message_loop` 用 `bus.receive(timeout=0.5)`（`agents/base.py:77`），0.5s 有界轮询。健康 agent 无论有没有消息，message loop 每 ≤0.5s 转一圈并刷新 `_last_alive_ts`。这使 `_last_alive_ts` 成为**与业务节奏（研判 4h / 采集 30s）完全无关**的心跳。`_last_alive_ts` 陈旧 = 真卡死（`on_message` 内挂死的网络调用 / 死锁 / 事件循环饿死）。

`_last_work_ts` 反映"最后一次处理到消息"，对 4h 研判 agent 长期不刷新是正常的——**仅用于 `/health` 展示"空闲 Xs"，绝不据此告警**。

### 4.2 MultiDataCollector 聚合字段（`agents/trading/multi_data_collector.py`，+1 字段）

`__init__` 新增：

```python
self._latest_data_health = {
    "ts": None,                 # 最近一次聚合更新时刻
    "any_degraded": False,      # 是否有 symbol degraded
    "degraded_symbols": [],     # 当前 degraded 的 symbol 列表
    "last_collect_ts": None,    # 最近一次成功 _full_collect 时刻
}
```

`_full_collect()` 末尾（现 degraded 判定在 `multi_data_collector.py:343`）更新该字段。聚合所有 symbol 的 degraded（`dimensions_ok < 6`）状态与最近成功采集时间。collector 是单实例 agent（`name="multi_data_collector"`，`orchestrator.py:81` 只实例化一次）。

### 4.3 零埋点的维度

- **LLM degraded**：直接读 `agent.llm.degraded`（`agents/llm_client.py:371-372`，每 agent per-instance）。
- **queue backlog**：直接读 `bus.get_metrics()` 的 per-agent `pending`（`agents/message_bus.py:229-239`）。
- **loop stall**：读 `agent._last_alive_ts`。
- **task failed**：现有 `_collect_task_stats`（保留）。

## 5. health_snapshot.py（纯函数）

```python
def build_health_snapshot(agents, bus_metrics, now, cfg, *, base_stats):
    """
    agents: 可迭代 BaseAgent（research + trading）
    bus_metrics: bus.get_metrics() 返回的 dict
    now: float 时间戳（调用方传入，便于测试）
    cfg: {stall_timeout_sec, backlog_warn_pending, data_stale_timeout_sec}
    base_stats: {agents_registered, tasks_alive, tasks_failed, halted_symbols, bus_dlq_size}
                （Orchestrator 已有的现成统计，原样透传保持向后兼容）
    返回 snapshot dict（见 §6）
    """
```

逻辑（纯计算，无 IO）：

- **loop_health**：遍历 agents，`idle = now - agent._last_alive_ts`；`_last_alive_ts==0`（尚未起跑）跳过不算 stall；`idle > stall_timeout_sec` 入 `stalled`。
- **queue_health**：遍历 `bus_metrics` per-agent，`pending > backlog_warn_pending` 入 `backlogged`；记 `max_pending`。
- **llm_health**：遍历 agents，`agent.llm is not None and agent.llm.degraded` 入 `degraded_agents`；`degraded = len>0`。
- **data_health**：从 collector（按 `name=='multi_data_collector'` 找，或调用方传入其 `_latest_data_health`）读 `any_degraded` 与 `last_collect_ts`；`stale = last_collect_ts is None or now - last_collect_ts > data_stale_timeout_sec`。

> 取数细节：`build_health_snapshot` 接收 agents 列表、`bus_metrics`、`now`、`base_stats`（均由 Orchestrator 调用前取好）。函数内**只读** agents 的实例字段（`_last_alive_ts` / `agent.llm.degraded` / collector 按 `name` 命中读 `_latest_data_health`），不触发任何 IO、不调用 bus、不改 agent 状态——这些字段由各自 loop 在调用前自然更新。这是"纯函数"在此处的含义：无副作用、可用假 stub 单测。

## 6. 扩展后的 agent_health.json（向后兼容）

保留现有 6 个顶层键，新增 4 个子对象：

```json
{
  "ts": 1781232142.57,
  "agents_registered": 16,
  "tasks_alive": 19,
  "tasks_failed": 0,
  "halted_symbols": {},
  "bus_dlq_size": 0,
  "loop_health":  {"stalled_count": 0, "stalled": [{"name": "judge", "idle_sec": 73}]},
  "queue_health": {"backlogged_count": 0, "max_pending": 12, "backlogged": [{"name": "reviewer", "pending": 210}]},
  "llm_health":   {"degraded": false, "degraded_agents": [{"name": "tech_analyst", "consecutive_failures": 4}]},
  "data_health":  {"degraded": false, "stale": false, "last_collect_ago_sec": 23, "degraded_symbols": []}
}
```

每维度的"是否不健康"布尔（喂给告警状态机）：

| 维度 | unhealthy 判定 |
|---|---|
| loop | `loop_health.stalled_count > 0` |
| queue | `queue_health.backlogged_count > 0` |
| llm | `llm_health.degraded` |
| data | `data_health.degraded or data_health.stale` |

读取方 `TelegramNotifier._read_agent_health`（`agents/trading/telegram_notifier.py:509-517`）对新键缺失 fail-safe（旧 snapshot 无新键时按"未知/省略"展示，不抛异常）。

## 7. 告警状态机（边沿 + 恢复）

Orchestrator 新增 `self._health_alert_state = {"loop": False, "queue": False, "llm": False, "data": False}`（False=健康），新增 `_maybe_alert_health_transitions(snapshot)`，在 `_health_loop` 内 `_write_agent_health` 之后调用：

```python
def _maybe_alert_health_transitions(self, snapshot):
    for dim, unhealthy, detail in self._iter_health_dims(snapshot):
        prev = self._health_alert_state[dim]
        if unhealthy and not prev:
            publish telegram_alert{level=warning, type=f'health_{dim}', detail}
        elif not unhealthy and prev:
            publish telegram_alert{level=info, type=f'health_{dim}_recovered'}
        self._health_alert_state[dim] = unhealthy
```

- 健康→不健康：发一次 warning，detail 带异常实体（如 `stalled=[judge]` / `degraded_agents=[tech_analyst]`）。
- 不健康→健康：发一次 info recovered。
- 持续不健康期间静默（不重发）。
- Topic `telegram_alert`，scope broadcast，TelegramNotifier 订阅处理（与现有 DLQ/task_failed 告警同路径）。

**与既有告警不冲突**：

- DLQ（`bus_dlq_growth`）、task_failed（`agent_task_failed`）**保持原样**，不并入状态机（增长触发 / per-failure 去重，语义不同）。
- Judge 的 `risk_alert{type='llm_degraded'}`（`agents/trading/judge.py:3939-3946`）是**决策路径**（强制 hold，红线）保留不动；supervisor 的 `health_llm` 是"任意 agent LLM 降级"的**观测告警**，文案明确区分（如"🩺 健康监控：LLM 降级（tech_analyst 连续失败 4）"），二者作用域不同、互不替代。

## 8. 配置（config_loader DEFAULTS/HARD_LIMITS + env 覆盖）

沿用既有范式（如 `RESEARCH_MIN_VOLUME_24H_USDT`）：

| 参数 | 默认 | HARD 范围（建议） | 含义 |
|---|---|---|---|
| `AGENT_STALL_TIMEOUT_SEC` | 60 | [10, 3600] | `_last_alive_ts` 超时算 stall（vs 0.5s 轮询，120× 余量，零误报） |
| `QUEUE_BACKLOG_WARN_PENDING` | 200 | [50, 1000] | per-agent pending 超过算 backlog（bus soft drop 在 500） |
| `DATA_STALE_TIMEOUT_SEC` | 180 | [30, 3600] | collector 最近成功采集超时算 data stale（采集 30s，6×） |

阈值仅影响**观测/告警敏感度**，不放宽任何交易风控。

## 9. Telegram 展示

### 9.1 `/status` 总括行（`_cmd_status`，`telegram_notifier.py:741-804` 末尾追加）

- 全绿：`─ 健康: ✓`
- 有异常（只列异常维度）：`─ 健康: ⚠ 1 stall / 2 backlog / LLM降级`
- 快照缺失：`─ 健康: ?（快照缺失）`

### 9.2 `/health` 新命令（注册进 `telegram_notifier.py:469-484` handlers 表，`async def _cmd_health(self)`）

```
🩺 Agent 健康明细
Loop:  ⚠ 1 stalled
  • judge 空闲 73s
Queue: ✓ (max pending 12)
LLM:   ⚠ 降级
  • tech_analyst 连续失败 4
Data:  ✓ 上次采集 23s 前
（快照 ts: 12s 前）
```

快照缺失时降级为"健康快照缺失（orchestrator 未写入或文件不可读）"。复用 `_read_agent_health()` 的 None 处理。

## 10. 测试矩阵

| 测试 | 覆盖 |
|---|---|
| `test_health_snapshot.py` | 纯函数每维度阈值 yes/no：stall（idle>阈值 / ==0 跳过 / 边界）、backlog（pending>阈值）、llm（degraded agent 聚合 / llm=None 跳过）、data（degraded / stale / last_collect=None） |
| `test_health_alert_transitions.py` | 边沿触发一次 / 恢复一次 / 持续不健康不重发 / 多维度独立（test_dlq_growth_alert.py 风格） |
| `test_base_agent_heartbeat.py` | `_last_alive_ts` 每迭代更新；`_last_work_ts` 仅 `msg is not None` 时更新 |
| collector data_health（并入现有 collector 测试或新增） | `_full_collect()` 后 `_latest_data_health` 正确聚合 any_degraded / degraded_symbols / last_collect_ts |
| `/status` + `/health` 展示（并入 `test_tg_status_enhancement.py` 或新增） | 总括行全绿/异常/缺失三态；`/health` 明细渲染 + 缺失降级 |

**不需要 event_backtest 同构**：observability-only，零决策路径、无 gate/veto/halt。

## 11. 红线与约束遵循

- `agent_health.json` 路径仍由 `utils/state_paths.py:get_state_paths()` 派生（live/testnet/paper 命名空间），不硬编码。
- 健康信号 **write-only / display-only**，严禁任何 gate/rank/veto/halt/daily-stop 读取（与 provenance 红线一致）。
- 新增配置经 config_loader DEFAULTS/HARD_LIMITS clamp，env 覆盖经 clamp。
- 不改 `data/`、`logs/`、`.env` 用户数据。

## 12. 落地文件清单

| 文件 | 改动 |
|---|---|
| `utils/health_snapshot.py` | **新增** 纯函数 `build_health_snapshot` |
| `agents/base.py` | +2 实例字段 + message loop 2 处盖戳 |
| `agents/trading/multi_data_collector.py` | +1 实例字段 `_latest_data_health` + `_full_collect` 末尾更新 |
| `agents/orchestrator.py` | `_health_loop` 调用 builder + 扩展写入 + `_maybe_alert_health_transitions` + `_health_alert_state` |
| `agents/trading/telegram_notifier.py` | `/status` 总括行 + `/health` 命令注册与 handler |
| `utils/config_loader.py` | 3 个 DEFAULTS/HARD_LIMITS + env 覆盖 |
| `tests/test_health_snapshot.py` 等 | 见 §10 测试矩阵 |
