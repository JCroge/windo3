## ADDED Requirements

### Requirement: 时间序磁带驱动
系统 SHALL 按时间顺序读决策磁带，对每条 record 用扰动 config + 当前 CF 状态注入真实 `_make_decision` 重决策，再按决策结果推进 CF 组合状态。

#### Scenario: 时间序重放
- **WHEN** driver 跑一条扰动序列
- **THEN** 系统 SHALL 按 record timestamp 升序处理，每步用 CF 状态机当前状态（非录下快照）注入 Judge

#### Scenario: 复用真实决策
- **WHEN** 每步重决策
- **THEN** 系统 SHALL 经 L2 `replay_decision`（注入 CF 状态）→ 真实 `_make_decision`，SHALL NOT 另写决策逻辑

#### Scenario: 决策推进 CF 状态
- **WHEN** 扰动决策为开仓且 CF slot/daily-stop 允许
- **THEN** 系统 SHALL 在 CF 状态机开一个 CF 仓并占 slot；为 hold/reject 则不开

### Requirement: 退出推进与到期解析
系统 SHALL 在序列推进中按时间解析到期/触发的 CF 持仓退出，更新 CF 状态。

#### Scenario: 到期 CF 仓解析
- **WHEN** 序列时间推进越过某 CF 仓的 SL/TP/24h 退出点
- **THEN** 系统 SHALL 解析其退出、计净 PnL、释放 slot、喂回反馈

### Requirement: driver observability-only write-only
系统 SHALL 保证序列 driver 为离线工具，反事实消息绝不进真实总线，严禁被任何 gate/veto/halt/rank/daily-stop 读取。

#### Scenario: 不进真实总线
- **WHEN** driver 重决策产生决策 payload
- **THEN** 系统 SHALL 只在 CF 内部消费，SHALL NOT publish 到真实 bus / Reviewer / RiskGuard
