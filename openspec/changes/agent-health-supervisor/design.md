# Design — Agent Health Supervisor

> 高层架构决策见此；完整技术 RFC（含取数点核查、schema、测试矩阵、红线）见 Superpowers Design Doc：`docs/superpowers/specs/2026-06-12-agent-health-supervisor-design.md`。实施计划见 `docs/superpowers/plans/2026-06-12-agent-health-supervisor.md`。

## 架构

```
BaseAgent (心跳埋点)              MultiDataCollector (聚合字段)
  _last_alive_ts                   _latest_data_health
  _last_work_ts                          │
        └─────────────┬──────────────────┘
                      ▼
   utils/health_snapshot.py
     build_health_snapshot(agents, bus_metrics, now, cfg, base_stats) -> snapshot
     纯函数：无 IO、无副作用、不改 agent 状态
                      ▼
   Orchestrator._health_loop (30s)
     ├─ build_health_snapshot(...) → 写 agent_health.json（扩展 schema）
     └─ _maybe_alert_health_transitions(snapshot) → 边沿告警 + 恢复
                      ▼
   TelegramNotifier  /status 总括行  +  /health 明细
```

## 关键决策

1. **单点收敛**：四维度聚合全在 `health_snapshot.py` 一个纯函数，Orchestrator 只负责"调用 + 写文件 + 跑告警状态机"，聚合与 IO/告警分离便于单测。

2. **loop-alive 心跳锚定 0.5s 轮询**：`_message_loop` 用 `bus.receive(timeout=0.5)`，健康 agent 无论有无消息每 ≤0.5s 转一圈刷新 `_last_alive_ts`。这使 stall 信号**与业务节奏（研判 4h / 采集 30s）完全解耦**，单一全局阈值（60s，120× 余量）即零误报，无需 per-agent 配置。`_last_work_ts`（处理到消息才盖）仅用于 `/health` 展示"空闲 Xs"，**绝不告警**（对慢节奏 agent 空闲正常）。

3. **边沿 + 恢复告警**：每维度健康→不健康发一次 warning，不健康→健康发一次 info，持续不健康静默。4 维独立。复用现有 `_maybe_alert_task_failure` 的 telegram_alert 广播模式。

4. **不并入既有告警**：DLQ（增长触发）、task_failed（per-failure 去重）语义不同，保持现状不动；Judge 的 `risk_alert{llm_degraded}` 是决策路径（强制 hold，红线）保留，supervisor 的 `health_llm` 仅观测，二者作用域不同互不替代。

5. **observability-only write-only**：健康快照只被写文件/告警/展示消费，**严禁** gate/veto/halt/rank/daily-stop 读取（与 data-source-provenance 同性质红线）。因此**不需 event_backtest 同构**。

6. **向后兼容**：`agent_health.json` 保留原 6 键（agents_registered/tasks_alive/tasks_failed/halted_symbols/bus_dlq_size + ts），`/halts`、`/status` 既有 reader 不受影响；`_write_agent_health` 仍返回 dlq_size（DLQ 告警链不破）。

## 配置

| 参数 | 默认 | HARD | 含义 |
|---|---|---|---|
| `AGENT_STALL_TIMEOUT_SEC` | 60 | [10,3600] | `_last_alive_ts` 超时算 stall |
| `QUEUE_BACKLOG_WARN_PENDING` | 200 | [50,1000] | per-agent pending 超过算 backlog（bus soft drop 在 500） |
| `DATA_STALE_TIMEOUT_SEC` | 180 | [30,3600] | collector 最近成功采集超时算 stale |
