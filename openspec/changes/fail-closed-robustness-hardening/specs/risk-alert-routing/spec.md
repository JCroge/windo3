## ADDED Requirements

### Requirement: live executor 的 risk_alert handler 必须以 source 守卫拒绝 paper 来源
live `MultiExecutor._handle_risk_alert` 处理 `risk_alert` 事件时，MUST 在分发任何动作前以
`source` 字段做结构性守卫——`source == 'paper_executor'` 的事件 MUST 被直接忽略（return），
不得进入任何 live 平仓 / 缩仓 / halt 分支。paper 与 live 共用 `risk_alert` topic 时，隔离
MUST 由该 source 守卫保证，MUST NOT 依赖"paper 的 alert type 恰好不在 live 白名单内"这一
脆性巧合。

#### Scenario: paper 来源 risk_alert 不驱动 live 动作
- **WHEN** live executor 收到 `risk_alert{source='paper_executor', type='paper_unfilled'}`
- **THEN** handler MUST 直接 return，不调用任何 close / reduce / halt
- **AND** 即便未来 paper 复用与 live 白名单同名的 type，也 MUST NOT 触发 live 平仓

#### Scenario: live 来源 risk_alert 不受守卫影响
- **WHEN** live executor 收到 `risk_alert{source!='paper_executor'}`（如 emergency_close / max_drawdown）
- **THEN** handler MUST 正常按 type 分发处理
