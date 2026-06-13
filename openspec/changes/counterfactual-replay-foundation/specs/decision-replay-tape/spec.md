## ADDED Requirements

### Requirement: 决策点磁带落盘
系统 SHALL 在 Judge 每次开仓决策点（包括 accept 与 reject）原子追加一条 `decision_replay_record` 到独立磁带文件，捕获足以未来忠实回放的完整输入与输出 bundle。

#### Scenario: 开仓 accept 落磁带
- **WHEN** Judge 发布 `trade_decision.v2` 且 action 为 open_long/open_short
- **THEN** 磁带追加一条记录，含 `request_id`、`timestamp`、`symbol`、`decision="accept"`、`tech_analysis` 9 维全量快照、`price_at_decision`、`regime_state`、`llm_audit_ref`、`trade_decision_output`（plan + attribution）

#### Scenario: 拒单也落磁带
- **WHEN** Judge 拒绝一个开仓计划（任一 gate 拦截）
- **THEN** 磁带追加一条记录，`decision="reject"`，含同样的输入 bundle 加 `reject_reason` 与拒单 attribution

#### Scenario: 原子写不污染主链路
- **WHEN** 磁带 writer 写入失败或抛异常
- **THEN** 异常 SHALL NOT 传播进 Judge 决策路径，记录 fail-safe 丢弃并计数告警，决策正常继续

### Requirement: 磁带 LLM 输出自包含
系统 SHALL 在磁带中内联存储 parsed LLM 输出（action/confidence/reasoning/key_factors/risk_warnings），使磁带自包含、不依赖 `logs/llm_audit_*.jsonl` 存活；`llm_audit_ref` 作为 7 天内可取原始 prompt 的 best-effort 指针。

#### Scenario: 内联输出抗 llm_audit 过期
- **WHEN** 一条 accept/reject 记录由 LLM 参与决策，且其后 llm_audit 文件已过 7 天保留期被清理
- **THEN** 磁带内 `llm_output_inline` SHALL 仍可被回放读取到当时 LLM 输出，无需 llm_audit

#### Scenario: 规则降级无 LLM
- **WHEN** 决策由规则引擎降级产生（LLM 不可用）
- **THEN** `llm_output_inline` SHALL 为 null，记录照常落带

### Requirement: 磁带 observability-only write-only
系统 SHALL 保证决策磁带为纯观测写入，任何 gate/veto/halt/rank/daily-stop SHALL NOT 读取磁带做交易决策。

#### Scenario: 磁带不进决策路径
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其代码路径 SHALL NOT 读取 `decision_replay_tape` 文件或 writer 状态

### Requirement: 磁带路径与 retention 受控
系统 SHALL 经 `utils/state_paths.py` 派生磁带文件路径（禁止硬编码），并支持 retention 配置与 feature flag 关停。

#### Scenario: 命名空间隔离
- **WHEN** `STATE_NAMESPACE` 为 testnet/paper
- **THEN** 磁带文件 SHALL 带对应前缀，与 live 隔离

#### Scenario: flag 关停回到现状
- **WHEN** 决策磁带 feature flag 关闭
- **THEN** 系统 SHALL NOT 写磁带、SHALL NOT 产生任何文件，且决策行为零变化

#### Scenario: retention 滚动封顶
- **WHEN** 磁带超过配置的保留窗口（默认 90 天）或总大小上限
- **THEN** 系统 SHALL 按先到条件滚动清理最旧数据，不无界增长
