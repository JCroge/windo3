# Tasks

## 1. tick 埋点 (agent-health-supervisor)
- [x] 1.1 `BaseAgent.__init__` 加 `_tick_enter_ts=0.0` / `_tick_exit_ts=0.0`；`_periodic_loop` 在 `await self.tick()` 前盖 enter、后盖 exit（CancelledError/Exception 路径不破坏）。测试 `test_base_agent_heartbeat.py`：tick 前后盖戳、enter>exit 表示 mid-tick。

## 2. builder tick-stall (agent-health-supervisor)
- [x] 2.1 `utils/health_snapshot.py::_loop_health` 扩展：读 agents 的 `_tick_enter_ts`/`_tick_exit_ts`，`enter>exit AND now-enter>tick_stall_timeout_sec AND enter>0` → tick 挂死；`loop_health` 加 `tick_stalled_count` + `tick_stalled:[{name, tick_sec}]`。`build_health_snapshot` 加 `tick_stall_timeout_sec` 参数。测试 `test_health_snapshot.py`：tick-stall 检出 / 边界相等不算 / 未起跑跳过 / mid-tick 但未超时不算。
- [x] 2.2 `config_loader` 加 `AGENT_TICK_STALL_TIMEOUT_SEC`=120（DEFAULTS/HARD_LIMITS [30,3600]/env_map）；Orchestrator 读取并传入 builder。

## 3. 告警与展示 (agent-health-supervisor)
- [x] 3.1 Orchestrator `_health_dim_status`：loop 维度 unhealthy = `stalled_count>0 OR tick_stalled_count>0`，告警 message 区分 message-loop 卡死 vs tick 卡死。测试 `test_health_alert_transitions.py`：tick-stall 触发 loop 边沿告警。
- [x] 3.2 Telegram `/health` 明细 Loop 段增列 tick 卡死 agent（`{name} tick {tick_sec}s`）；`/status` 总括 tick 卡死计入 loop。测试 `test_health_telegram_display.py`。

## 4. 验证与收尾
- [x] 4.1 全量 `python3 -m pytest -q` 通过（基线 1135 + 新增）。
- [x] 4.2 编译检查 `python3 -m compileall -q agents/ utils/` 通过。
