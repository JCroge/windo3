## ADDED Requirements

### Requirement: 诚实门控 + 排名 + actionable 推荐
系统 SHALL 对扫描结果先门控（剔除 untrustworthy 与薄样本），再按 delta 净 PnL 排名，最优值 actionable 时输出方向推荐，否则拒答，绝不杜撰方向。

#### Scenario: 剔除不可信值
- **WHEN** 某扫描值 `untrustworthy=True`（L3b baseline_fidelity 不足）或 `sequence_len < min_sample`
- **THEN** 系统 SHALL 把它排除出排名

#### Scenario: actionable 给推荐
- **WHEN** 排名最优的 trustworthy 值 `delta.net_pnl > actionable_min_pnl`（显著正改善）
- **THEN** 系统 SHALL 输出 `{verdict="recommend", recommended_value, delta_net_pnl, confidence, sample, baseline_fidelity}`

#### Scenario: 证据不足拒答
- **WHEN** 无 trustworthy 值，或最优值改善不显著
- **THEN** 系统 SHALL 输出 `verdict="no_actionable_direction"`，SHALL NOT 编造方向

### Requirement: 多重比较守卫 — 连贯趋势才推荐
系统 SHALL 防止"扫一排挑最高"的选择性偏差：报出全部值的 delta 全貌；最优值必须是连贯趋势（相邻值同向）而非孤立尖刺才推荐；actionable 门槛随扫描值数收紧。

#### Scenario: 报出全貌
- **WHEN** 生成推荐
- **THEN** 输出 SHALL 含 `all_values`（每个扫描值的 delta + 信任元数据），供人看趋势非只看赢家

#### Scenario: 孤立尖刺拒答
- **WHEN** 排名最优值的 delta 远高于其相邻值（相邻不同向，疑似噪声尖刺）
- **THEN** 系统 SHALL 标 `isolated_spike` 并 SHALL NOT 推荐该值

#### Scenario: 门槛随值数收紧
- **WHEN** 扫描的值越多
- **THEN** actionable 的有效净 PnL 门槛 SHALL 相应提高（抵消多重比较）

### Requirement: 置信度三因子透明
系统 SHALL 从 baseline_fidelity、divergence_ratio、样本量三因子派生 confidence，并同时报出三原始因子，不藏进单一数字。

#### Scenario: 三因子随推荐报出
- **WHEN** 生成推荐
- **THEN** 输出 SHALL 含 confidence 与 `baseline_fidelity`/`divergence_ratio`/`sample` 三原始因子 + `fidelity_note`（继承 L3b 保真天花板）

### Requirement: 推荐 observability-only，绝不自动应用
系统 SHALL 保证推荐为离线建议，严禁被任何 gate/veto/halt/rank/daily-stop 读取，绝不自动改线上 config（人审）。

#### Scenario: 推荐不进决策、不自动应用
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其 SHALL NOT 读取推荐产物；推荐 SHALL NOT 自动应用到线上 config
