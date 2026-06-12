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
