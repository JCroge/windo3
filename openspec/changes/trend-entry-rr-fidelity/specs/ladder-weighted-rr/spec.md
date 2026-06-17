## ADDED Requirements

### Requirement: effective_rr 按真实阶梯离场加权

`effective_rr` 的计算 SHALL 反映 executor 真实的阶梯离场比例(TP1/TP2/trailing 各档平仓占比),而非仅用第一档 take_profit。各 TP 档的盈利贡献 MUST 按对应平仓比例加权计入分子。剩余 trailing 仓位的盈利贡献 SHALL 使用保守口径(如 +1R 锁利或 trailing 期望下界),MUST NOT 记为最远档满额。净成本(手续费+资金费)扣法 SHALL 保持现状(分子减、分母加)。

#### Scenario: 阶梯加权抬升趋势仓 R:R

- **WHEN** 一个计划的 TP 阶梯各档距离对应 executor 的 50/25/25 离场比例
- **THEN** `effective_rr` 按各档比例加权计算,结果不低于仅用 TP1 的口径(在阶梯各档为正贡献时)

#### Scenario: 剩余仓位保守口径

- **WHEN** 计算 trailing 剩余仓位(约 25%)的盈利贡献
- **THEN** 使用保守口径(+1R 锁利或 trailing 期望下界),不记为最远档满额

### Requirement: 与旧口径同假设、不引入额外概率折扣(v1)

v1 的阶梯加权 MUST 与旧 TP1-only 口径保持**相同的"目标达成"假设**——旧公式即假设满仓在 TP1 离场(隐含 P=1),故 v1 MUST NOT 仅对新口径分子单独施加 P(reach tierᵢ)<1 的概率折扣(实测表明:只缩分子而不同步缩减阶梯化后降低的风险分母,会把 effective_rr 不合理地压到低于旧口径,反而抹掉杠杆②的本意)。v1 的唯一保守折扣 SHALL 是剩余 trailing 档封顶 +1R(见上一 Requirement)。基于历史频率的到达概率 + 风险分母同步降低的相干口径(v2)拆出本 change,不在本 change 范围内。

#### Scenario: 不出现反向压低

- **WHEN** 阶梯各档均为正贡献
- **THEN** 阶梯加权 effective_rr 不低于旧 TP1-only effective_rr(对同一笔计划),即不得因口径改动反而压低评分

#### Scenario: 离场比例可观测

- **WHEN** effective_rr 使用阶梯加权
- **THEN** 所用离场比例权重([0.5,0.25,0.25])随 effective_rr_ladder 一并记录,可在决策记录中回溯

### Requirement: 全样本 A/B 背书(CF 重放实验室)与灰度

阶梯加权 effective_rr SHALL 在 **CF 重放实验室**(`utils/knob_sweep` / `utils/sequential_perturbation` 跑真实 `MultiJudge` 决策代码)上,以 `ladder_rr_enabled` 作为旋钮对**全样本被拒磁带(含亏单)** A/B,产出净 PnL/胜率/MDD delta。新旋钮 MUST 经 `utils/decision_replay.py::_install_config_flags` 注入(否则 replay 用 `getattr` 兜底默认致旋钮无效、A/B 假阴性)。退出估算的粗粒度(SL/TP/24h)由两臂同估算在 delta 抵消(以 delta 为结论,非绝对值)。新口径 SHALL 通过 config 开关灰度,背书前不直接全量上线。

#### Scenario: 含亏单的全样本 A/B

- **WHEN** baseline 臂(ladder_rr_enabled=False)与 perturbed 臂(=True)在 CF 重放实验室对比
- **THEN** A/B 覆盖全样本被拒磁带(趋势赢家翻转 + 同期亏单翻转),净效果以含亏单的 delta 为准,而非仅趋势赢家

#### Scenario: 旋钮经 _install_config_flags 注入

- **WHEN** CF 重放以 `ladder_rr_enabled` 为旋钮
- **THEN** `utils/decision_replay.py::_install_config_flags` 必须设置该 flag(及①的 path_evidence flags),使 perturbed 臂真实生效;缺注入则视为实现缺陷

#### Scenario: config 灰度开关

- **WHEN** 阶梯加权口径的 config 开关关闭
- **THEN** effective_rr 计算回退到改动前的 TP1 口径
