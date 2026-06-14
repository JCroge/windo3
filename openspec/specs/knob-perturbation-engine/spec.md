## ADDED Requirements

### Requirement: 逐决策扰动跑两次真实决策
系统 SHALL 对同一 decision_replay_record 用 baseline config 与 perturbed config 各跑一次真实 `_make_decision`（经 L2 `replay_decision`），并分层 diff 两次决策，复用真实 Judge 逻辑不重写。

#### Scenario: baseline 与 perturbed 各一次
- **WHEN** 对一条 replayable record 跑扰动引擎
- **THEN** 系统 SHALL 调用 `replay_decision(record, baseline_config)` 与 `replay_decision(record, perturbed_config)`，得到两个 captured 决策

#### Scenario: 复用真实决策逻辑
- **WHEN** 扰动引擎执行
- **THEN** 其 SHALL 经 `replay_decision`→真实 `_make_decision` 产生决策，SHALL NOT 另写第二份评分/gate

### Requirement: baseline 复现自检闸
系统 SHALL 对每条 record 先验证 baseline replay 复现录下的决策；不复现的 record 标 `baseline_mismatch` 并排除出翻转统计。

#### Scenario: baseline 复现失败排除
- **WHEN** baseline replay 的决策与 record 录下的 `trade_decision_output` 比对不 match
- **THEN** 系统 SHALL 标 `status=baseline_mismatch` 并 SHALL NOT 把该 record 计入翻转率（连原决策都没复现，perturbed diff 不可信）

#### Scenario: baseline 复现成功才比翻转
- **WHEN** baseline replay 复现了录下的决策
- **THEN** 系统 SHALL 才比对 baseline vs perturbed 求翻转

### Requirement: 翻转分类
系统 SHALL 用 `compare_decision` 分层比对两次决策，并派生 `flip_kind ∈ {accept_to_reject, reject_to_accept, gate_label_change, none, baseline_mismatch}`。

#### Scenario: accept↔reject 翻转
- **WHEN** baseline action 为开仓而 perturbed 为 hold（或反之）
- **THEN** `flipped=True` 且 `flip_kind` 为 `accept_to_reject` / `reject_to_accept`

#### Scenario: gate 标签变化但 action 不变
- **WHEN** 两次 action 相同但某 gate 标签（如 rr_policy / rr_floor_used）不同
- **THEN** `flip_kind=gate_label_change`，`diffs` 含该字段

#### Scenario: 无变化
- **WHEN** 两次决策一致
- **THEN** `flipped=False`，`flip_kind=none`

### Requirement: 引擎 observability-only write-only
系统 SHALL 保证扰动引擎为离线工具，严禁被任何 gate/veto/halt/rank/daily-stop 读取或进入生产决策链路。

#### Scenario: 引擎不进生产链路
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其 SHALL NOT import 或调用扰动引擎
