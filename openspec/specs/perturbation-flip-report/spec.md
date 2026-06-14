## ADDED Requirements

### Requirement: 翻转分桶报表
系统 SHALL 对一批 record 跑扰动引擎，按 reject_reason×regime×side 分桶统计翻转计数/率与 flip_kind 分布。

#### Scenario: 分桶翻转统计
- **WHEN** 对一批 replayable record 跑扰动报表
- **THEN** 输出 SHALL 按 `reject_reason|regime|side` 分桶，每桶含翻转总数、翻转率、各 flip_kind 计数

#### Scenario: 缺快照跳过计数
- **WHEN** record 缺状态快照（不可回放）
- **THEN** 系统 SHALL 跳过该条并计入 skipped，不中断报表

### Requirement: 诚实 gate 守门
系统 SHALL 对每桶翻转结论经 L1 诚实 gate（Wilson 区间 + 三档样本），薄样本桶拒答。

#### Scenario: 薄样本拒答
- **WHEN** 某桶样本量低于阈值
- **THEN** 该桶 SHALL 标 `INSUFFICIENT_SAMPLE`，不给翻转率结论

### Requirement: 范围与保真标注
系统 SHALL 在报表 metadata 标注 L3a 的范围与保真限制。

#### Scenario: metadata 带标注
- **WHEN** 生成扰动报表
- **THEN** metadata SHALL 含 `perturbed_knobs`（perturbed_config diff）、`fidelity_note`（逐决策独立、不含级联、只对非 LLM 旋钮确定）

### Requirement: 报表 observability-only
系统 SHALL 保证报表为离线分析产物，输出严禁被任何 gate/veto/halt/rank/daily-stop 读取做交易决策。

#### Scenario: 报表不进决策
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其 SHALL NOT 读取扰动报表产物
