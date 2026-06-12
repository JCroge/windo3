# Tasks

> 经 superpowers subagent-driven-development 执行，每任务两阶段 review（spec 合规 + 代码质量）。13 commit 在分支 `agent-health-supervisor`。

## 1. 信号埋点 (agent-health-supervisor)
- [x] 1.1 BaseAgent `__init__` 加 `_last_alive_ts`/`_last_work_ts`；`_message_loop` 每迭代盖 alive、处理到消息盖 work（`test_base_agent_heartbeat.py` 3 case）
- [x] 1.2 MultiDataCollector 加 `_latest_data_health` + `_update_data_health` + `_full_collect` 成功后调用（`test_collector_data_health.py` 3 case）

## 2. 聚合 builder (agent-health-supervisor)
- [x] 2.1 `utils/health_snapshot.py::build_health_snapshot` 纯函数聚合四维度（loop/queue/llm/data），含边界（stall 严格 >、未起跑跳过、`_dlq_size` 跳过、never-collected 不 stale、no collector neutral）（`test_health_snapshot.py` 11 case）
- [x] 2.2 config_loader 加 3 阈值 DEFAULTS/HARD_LIMITS/env_map（`test_health_snapshot.py` +1 case）

## 3. Orchestrator 接入与告警 (agent-health-supervisor)
- [x] 3.1 `_write_agent_health` 接 builder 组装扩展 snapshot + 缓存 `_latest_health_snapshot`，保持返回 dlq_size（向后兼容 6 键）
- [x] 3.2 `_health_dim_status` + `_maybe_alert_health_transitions` 边沿告警 + 恢复，4 维独立、持续静默；`_health_loop` 接线（`test_health_alert_transitions.py` 6 case）

## 4. Telegram 展示 (agent-health-supervisor)
- [x] 4.1 `_format_health_summary` + `/status` 总括行（只列异常维度）
- [x] 4.2 `_format_health_detail` + `_cmd_health` 明细命令 + 注册（offender 子行 `.get()` 防御）（`test_health_telegram_display.py` 9 case）

## 5. 验证与收尾
- [x] 5.1 全量 `python3 -m pytest -q` —— 实测 `1135 passed / 4 deselected / 1 warning`（基线 1102 + 本次 33）
- [x] 5.2 编译检查 `python3 -m compileall -q agents/ utils/` 通过
- [x] 5.3 最终整体 review（opus）READY TO MERGE；红线合规独立确认（grep 全库，健康状态仅被展示/告警消费，无任何 gate/veto/halt/rank）
