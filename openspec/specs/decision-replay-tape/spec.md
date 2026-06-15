## ADDED Requirements

### Requirement: 决策点磁带落盘
系统 SHALL 在 Judge 每次开仓决策点（包括 accept 与 reject）原子追加一条 `decision_replay_record` 到独立磁带文件，捕获足以未来忠实回放的完整输入与输出 bundle。`tech_analysis` 与 `llm_output_inline` 字段 SHALL 反映该决策**实际使用的输入**——禁止以空字典 / null 占位写入；当决策实际有 tech 信号或 LLM 参与时，对应字段 MUST 非空。

#### Scenario: 开仓 accept 落磁带
- **WHEN** Judge 发布 `trade_decision.v2` 且 action 为 open_long/open_short
- **THEN** 磁带追加一条记录，含 `request_id`、`timestamp`、`symbol`、`decision="accept"`、`tech_analysis` 9 维全量快照（取自该 symbol 决策时的真实 tech，非空占位）、`price_at_decision`、`regime_state`、`llm_output_inline`（LLM 参与时为真实 parsed 输出）、`llm_audit_ref`、`trade_decision_output`（plan + attribution）

#### Scenario: 拒单也落磁带
- **WHEN** Judge 拒绝一个开仓计划（任一 gate 拦截）
- **THEN** 磁带追加一条记录，`decision="reject"`，含同样的真实输入 bundle（`tech_analysis` 取自该 symbol 决策时的真实 tech，`llm_output_inline` 取自该决策的真实 parsed LLM 输出）加 `reject_reason` 与拒单 attribution

#### Scenario: 捕获使回放复现拒因
- **WHEN** 一条因 `rr_below_floor` / `quality_gate` / `ev_gate` 等 gate 拒单的记录被 `replay_decision` 以原 baseline config 回放
- **THEN** 回放 SHALL 凭记录内真实 `tech_analysis` + `llm_output_inline` 走到对应 gate 并复现该拒因（reject 且拒因匹配），而非在"无信号→hold"处提前短路得到 `reject_reason=null`

#### Scenario: 捕获使旋钮扰动可翻转
- **WHEN** 一条因 `rr_below_floor` 拒单的记录被 `replay_decision` 以 perturbed config（`rr_floor_default` 降至低于该记录 R:R）回放
- **THEN** 回放 SHALL 翻转为开仓决策（action 为 open_long/open_short），证明捕获使非 LLM 旋钮在回放中确实生效

#### Scenario: 原子写不污染主链路
- **WHEN** 磁带 writer 写入失败或抛异常
- **THEN** 异常 SHALL NOT 传播进 Judge 决策路径，记录 fail-safe 丢弃并计数告警，决策正常继续

### Requirement: 磁带 LLM 输出自包含
系统 SHALL 在磁带中内联存储 parsed LLM 输出（action/confidence/reasoning/key_factors/risk_warnings），使磁带自包含、不依赖 `logs/llm_audit_*.jsonl` 存活；`llm_audit_ref` 作为 7 天内可取原始 prompt 的 best-effort 指针。当某决策由 LLM 参与产生时，`llm_output_inline` MUST 为该次调用的真实 parsed 输出，SHALL NOT 写 null 占位。

#### Scenario: 内联输出抗 llm_audit 过期
- **WHEN** 一条 accept/reject 记录由 LLM 参与决策，且其后 llm_audit 文件已过 7 天保留期被清理
- **THEN** 磁带内 `llm_output_inline` SHALL 仍可被回放读取到当时 LLM 输出，无需 llm_audit

#### Scenario: 规则降级无 LLM
- **WHEN** 决策由规则引擎降级产生（LLM 不可用），或开仓走 LLM 之前的 rule-only 路径
- **THEN** `llm_output_inline` SHALL 为 null（诚实反映该决策无 LLM 参与），记录照常落带

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

### Requirement: replayable 标志真实性
系统 SHALL 仅在记录捕获了足以回放的完整输入时才标 `replayable=true`；`replayable` MUST 同时要求存在决策前状态快照与非空 `tech_analysis`。回放与报表读取端 SHALL 跳过 `replayable=false` 记录，不对其做"无信号→hold"兜底而误判为忠实复现。

#### Scenario: 输入完整才可回放
- **WHEN** `build_bundle` 构建一条记录，且 `state_snapshot_before_decision` 非 null 且 `tech_analysis` 非空
- **THEN** `replayable` SHALL 为 true

#### Scenario: 缺输入标不可回放
- **WHEN** 一条记录缺状态快照，或 `tech_analysis` 为空（含历史 v1 空记录）
- **THEN** `replayable` SHALL 为 false，回放 / 扰动 / 扫描端 SHALL 将其排除出统计，不计入复现率或翻转率

#### Scenario: schema 版本标记自包含
- **WHEN** 落盘一条捕获了真实 tech + llm 的新记录
- **THEN** 其 `schema_version` SHALL 标记为新版本（v2），使读取端可区分自包含记录与历史空 v1 记录
