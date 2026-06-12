## Why

多 Agent 系统的健康可观测性此前只有零散信号：DLQ 增长告警（P2-16）、agent 任务崩溃告警（agent-fault-visibility），以及 `/status` 里基础的 agents/DLQ/halt 行（tg-status-enhancement）。但 to-do #95 列出的六维度里，**loop-alive（未崩溃但卡死）、queue backlog、LLM degraded、data degraded 四个维度无聚合、无展示、无告警**：

- `LLMClient.degraded` 是 per-agent 实例状态，无全局聚合；
- `bus.get_metrics()` 的 pending 有数据但无 backlog 判定；
- collector 的 `market_data.degraded` 随消息丢失，Orchestrator 读不到；
- **没有任何 loop 心跳**——一个 agent 的 `on_message` 挂死（阻塞网络调用 / 死锁）时 `task.done()` 仍为 False，运维完全无感。

运维无法在 Telegram 看到"哪个 agent 卡死了 / LLM 在降级 / 数据陈旧"，只能等到交易异常才后知后觉。

## What Changes

- 新增纯函数 `utils/health_snapshot.py::build_health_snapshot` 聚合四维度健康，扩展 `agent_health.json`（保留 6 个 legacy 键 + loop_health/queue_health/llm_health/data_health）。
- BaseAgent 心跳埋点：`_last_alive_ts`（message loop 0.5s 轮询每迭代盖戳，与业务节奏解耦的告警信号）/ `_last_work_ts`（处理到消息才盖，仅展示）。
- MultiDataCollector 新增 `_latest_data_health` 聚合字段。
- Orchestrator `_maybe_alert_health_transitions`：四维度健康↔不健康跳变各发一次 telegram_alert（边沿 + 恢复通知，4 维独立，持续不健康静默）。
- Telegram `/status` 末尾健康总括行 + 新增 `/health` per-dimension 明细命令。
- 三阈值（`AGENT_STALL_TIMEOUT_SEC`=60 / `QUEUE_BACKLOG_WARN_PENDING`=200 / `DATA_STALE_TIMEOUT_SEC`=180）走 config_loader DEFAULTS/HARD_LIMITS + env 覆盖。

## Capabilities

### New Capabilities
- `agent-health-supervisor`：Orchestrator 聚合 loop-alive / queue backlog / LLM degraded / data degraded 四维度健康，扩展 agent_health.json、Telegram 展示（/status 总括 + /health 明细）、边沿告警 + 恢复通知。**observability-only write-only**：严禁任何 gate/veto/halt/rank/daily-stop 读取健康状态做交易决策。

### Modified Capabilities
<!-- none — 不改 tg-status-enhancement 既有 /status 基础行语义，只在其后追加健康总括行 -->

## Impact

- **New**: `utils/health_snapshot.py`（纯函数 builder）。
- **Modified**: `agents/base.py`（2 心跳字段 + message loop 盖戳）、`agents/trading/multi_data_collector.py`（`_latest_data_health` + `_full_collect` 更新）、`agents/orchestrator.py`（`_write_agent_health` 接 builder + `_maybe_alert_health_transitions` + `_health_dim_status` + `_health_loop` 接线）、`agents/trading/telegram_notifier.py`（`_format_health_summary` + `_format_health_detail` + `_cmd_health` + 注册）、`utils/config_loader.py`（3 阈值）。
- **Test**: `test_health_snapshot.py` / `test_base_agent_heartbeat.py` / `test_collector_data_health.py` / `test_health_alert_transitions.py` / `test_health_telegram_display.py`（共 +33 test）。
- **Behavioral**: 运维可在 Telegram 看见四维度健康；agent 卡死/LLM 降级/数据陈旧主动告警。**零决策路径改动，不需 event_backtest 同构**。
- **Non-goals**: 不做自动修复/自动 halt；不改 DLQ/task_failed 既有告警；不做 tick-loop 挂死专项告警（message-loop 心跳已覆盖 agent 级 liveness，留 backlog）；不做 per-agent 节奏配置。
