## ADDED Requirements

### Requirement: baseline-vs-perturbed delta
系统 SHALL 用同一序列、同一 CF 估算方法跑 baseline config 与 perturbed config 两臂，输出 PnL/胜率/回撤的 delta（perturbed − baseline）。

#### Scenario: 两臂同估算求 delta
- **WHEN** 跑一次扰动评估
- **THEN** 系统 SHALL 对 baseline 与 perturbed 各跑一遍序列模拟（同退出/估算方法），输出净 PnL/胜率/最大回撤的 baseline、perturbed 与 delta

#### Scenario: delta 优先于绝对值
- **WHEN** 报告结论
- **THEN** 系统 SHALL 以 delta 为主结论（系统性估算偏差两臂抵消），绝对值标为估算

### Requirement: baseline 序列保真自检（delta 信任锚）
系统 SHALL 统计 baseline 臂的每步决策与录下决策的一致率（`baseline_fidelity`）；一致率低于阈值时标 `untrustworthy` 并拒给 delta 结论。

#### Scenario: 高一致率 delta 可信
- **WHEN** baseline-sim 决策与录下决策一致率 ≥ 阈值（默认 0.8）
- **THEN** 系统 SHALL 给出 delta 结论，并随报告报出 `baseline_fidelity`

#### Scenario: 低一致率拒答
- **WHEN** baseline-sim 与录下决策一致率 < 阈值
- **THEN** 系统 SHALL 标 `untrustworthy` 并 SHALL NOT 给 delta 方向结论（baseline-sim 跟不住现实，delta 不可信）

### Requirement: 误差/置信度观测
系统 SHALL 量化结果对估算的依赖度并随结论报出。

#### Scenario: divergence 与置信度
- **WHEN** 生成 delta 报告
- **THEN** 报告 SHALL 含序列长度、CF 开仓数 vs 真实开仓数、divergence_ratio（与 baseline 决策不同的比例）、估算 PnL 占比，并经 L1 诚实 gate；高 divergence / 薄样本 SHALL 标 low_confidence 或拒答

#### Scenario: 保真标注
- **WHEN** 输出 delta 报告
- **THEN** metadata SHALL 含 `perturbed_knobs` + `fidelity_note`（退出仅 SL/TP/24h、误差沿序列累积、漏 trailing/partial/risk-close）

### Requirement: 报表 observability-only
系统 SHALL 保证 delta 报表为离线分析产物，严禁被任何 gate/veto/halt/rank/daily-stop 读取做交易决策。

#### Scenario: 报表不进决策
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其 SHALL NOT 读取 delta 报表产物
