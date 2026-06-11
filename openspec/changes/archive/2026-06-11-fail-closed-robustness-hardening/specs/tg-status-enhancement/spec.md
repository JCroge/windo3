## ADDED Requirements

### Requirement: bus DLQ 增长必须主动告警
系统 SHALL 保证：Orchestrator 周期性健康循环（已有 `_health_loop` / `_write_agent_health`，约 30s）在算出
`dlq_size = len(bus._dead_letter)` 后，MUST 与上一次记录的 `_prev_dlq_size` 比较；当
`dlq_size > _prev_dlq_size`（出现新死信，说明有 enqueue 失败或重要 topic 无订阅者）时，MUST
经现有 `telegram_alert` 通道主动 publish 一条告警事件（含当前 dlq_size 与本次增量 delta），
不得仅把 DLQ 计数静默写入 `agent_health.json`。比较基准 `_prev_dlq_size` MUST 在每次健康
tick 后更新，使告警按 30s cadence 天然限流、不重复刷屏。

#### Scenario: DLQ 增长触发告警
- **WHEN** 某次健康 tick 算出 `dlq_size=3` 且 `_prev_dlq_size=0`
- **THEN** MUST publish `telegram_alert{type='bus_dlq_growth', dlq_size=3, delta=3}`
- **AND** 随后 `_prev_dlq_size` MUST 更新为 3

#### Scenario: DLQ 未增长不告警
- **WHEN** 某次健康 tick 的 `dlq_size <= _prev_dlq_size`
- **THEN** MUST NOT publish bus_dlq_growth 告警
