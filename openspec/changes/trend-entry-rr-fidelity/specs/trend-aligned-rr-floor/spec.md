## ADDED Requirements

### Requirement: 干净趋势授予趋势对齐 R:R 地板

入场 R:R 地板选择 SHALL 在标的呈现客观干净趋势证据时,授予趋势对齐地板(`rr_floor_long_aligned_choppy` / 对称的趋势地板),而非默认地板(`rr_floor_default`)。"干净趋势"的判定 MUST 不仅依赖可能漏报的 HTF/日线 bias 信号,还 SHALL 纳入客观路径证据(如入场前/近窗的方向一致性与低逆行特征),使价格上明显、逆行幅度小的趋势不被误落 default 地板。

判定 MUST 仍排除真 choppy(方向反复、深逆行)行情,避免把低质量震荡误授趋势地板。

#### Scenario: 干净 long 趋势授予对齐地板

- **WHEN** 一个 long 计划所在标的 effective_regime 为 choppy/mixed,但客观证据显示方向一致、近窗逆行幅度小(趋势干净)
- **THEN** `_select_rr_floor` 返回趋势对齐地板(1.30 级)而非 default(1.50),且 rr_policy 标记为对齐策略

#### Scenario: 真 choppy 不被误授对齐地板

- **WHEN** 标的方向反复、逆行幅度大(真震荡),即便单根信号方向为 bullish
- **THEN** `_select_rr_floor` 不授予趋势对齐地板,落到 default 地板

#### Scenario: 客观证据禁前视

- **WHEN** 计算干净趋势的客观路径证据(方向一致性、近窗回撤、延展度)
- **THEN** 仅使用入场决策时点及之前的 bar 数据,MUST NOT 引用入场后的 bar(无前视偏差)

#### Scenario: 全样本回测背书(CF 重放实验室)

- **WHEN** 趋势对齐判定的放宽以 `path_evidence_aligned_enabled` 为旋钮,在 CF 重放实验室(跑真实 judge 决策代码)对全样本被拒磁带(含亏单)A/B
- **THEN** 产出净 PnL/胜率/MDD delta,且胜率不被低质量入场显著稀释(背书阈值在回测报告中明示);旋钮 MUST 经 `_install_config_flags` 注入方能生效

### Requirement: 趋势对齐判定可观测且可配置

趋势对齐地板的判定 SHALL 记录其触发依据(命中的证据项与最终 rr_policy/rr_floor_reason),并 SHALL 通过 config 开关控制启用与证据门槛,默认走灰度,不直接全量改变线上行为。

#### Scenario: 判定依据可追溯

- **WHEN** 一个计划被授予或拒绝趋势对齐地板
- **THEN** 决策记录中包含 rr_policy、rr_floor_reason 及命中的客观证据项,可在被拒/被放行事件中回溯

#### Scenario: config 灰度开关

- **WHEN** 趋势对齐放宽的 config 开关关闭
- **THEN** 地板选择行为与改动前一致(default 路径不变)
