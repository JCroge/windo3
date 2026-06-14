## ADDED Requirements

### Requirement: 单旋钮 grid 扫描
系统 SHALL 对一个旋钮的显式值列表逐值跑 L3b `build_delta_report`，聚合每值的 delta 与信任/样本元数据。

#### Scenario: 逐值跑 L3b
- **WHEN** 对 knob 的 values=[v1,v2,...] 扫描
- **THEN** 系统 SHALL 对每个 v 跑 `build_delta_report(records, baseline_config={}, perturbed_config={knob: v}, ...)`，收集 `{value, delta, baseline_fidelity, untrustworthy, divergence_ratio, sequence_len}`

#### Scenario: 复用 L3b 不重写
- **WHEN** 扫描执行
- **THEN** 其 SHALL 经 L3b `build_delta_report`（真实 Judge 序列重演），SHALL NOT 另写决策/PnL 逻辑

#### Scenario: 显式值列表
- **WHEN** 指定扫描值域
- **THEN** 系统 SHALL 接受显式值列表（非 range+step），允许非均匀值

### Requirement: 扫描 observability-only write-only
系统 SHALL 保证扫描引擎为离线工具，严禁被任何 gate/veto/halt/rank/daily-stop 读取，绝不自动改线上 config。

#### Scenario: 不进生产链路
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其 SHALL NOT import 或调用扫描引擎；扫描产物 SHALL NOT 自动应用到线上 config
