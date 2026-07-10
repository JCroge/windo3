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

#### Scenario: rejected 流忠实 A/B(真实目标人群)

- **WHEN** CF 重放磁带的 lever2 目标人群不足(被拒趋势单不在 decision_replay_tape)
- **THEN** lever2 SHALL 另在 `rejected_signal_events.jsonl`(被拒趋势单实际所在,含 tp/sl/entry/leverage)上做忠实 A/B:重算 ladder effective_rr → 判定是否过 reject_reason 隐含地板 → 按趋势簇去重 → `resolve_counterfactual` + klines 出**含亏单**的 CF 净 PnL,以净期望(非单看赢家)为背书依据

### Requirement: ladder_rr_enabled 默认启用（lever2 背书已满足）

阶梯加权 effective_rr（`ladder_rr_enabled`）的默认值 SHALL 为启用（True）。其全样本 A/B 背书已满足：rejected 流忠实 A/B 在保守 TP1 结算（零 TP2 信用）下含亏单净 **+0.21R/簇**；tier 到达频率定价表明被 `rr_below_floor` 拒的干净趋势 long **P(达TP2)=68% / P(TP2|达TP1)=90%**，且把 TP2/TP3 按到达频率打折后 effective_rr 仍 **1.76~1.80**（对"TP2 必达"假设不敏感，因 TP1 50% 权重 + 第3档封顶 +1R 已扛主导）。默认值 SHALL 经 `config_loader.DEFAULTS` 提供，并保留 env 覆盖（`LADDER_RR_ENABLED`）作为**即时关闭逃生阀**，无需改代码即可回滚。

#### Scenario: 默认启用

- **WHEN** 未显式配置 `ladder_rr_enabled`（既不在 env 也不在 config 文件）
- **THEN** `effective_rr` 使用阶梯加权口径（lever2 生效），被 TP1-only 口径误拒的趋势单按真实 50/25/25 离场评分

#### Scenario: env 逃生阀即时关闭

- **WHEN** 环境变量 `LADDER_RR_ENABLED=false`
- **THEN** `effective_rr` 回退到改动前 TP1-only 口径，无需改代码（满足既有「config 灰度开关」回退场景）

#### Scenario: lever1 不随本 change 默认开

- **WHEN** 本 change 默认开 lever2
- **THEN** lever1（`path_evidence_aligned_enabled`）SHALL 保持默认关——其目标人群（中性 bias + 干净趋势）验证待 `tech_context` 埋点数据累积，另起独立 change；本 change 不动 lever1 默认值

#### Scenario: lever2 抬高 R:R 过正常地板而非走低 R:R 策略

- **WHEN** lever2 把某趋势单 effective_rr 从 <地板 抬到 ≥1.50 default 地板
- **THEN** 该单作为正常 R:R 单开仓（全尺寸、不触发 `low_rr_policies` 缩仓/降杠杆/独立 slot——那是 lever1 授 <1.5 地板时的路径）

### Requirement: 低 R:R 保护性缩仓判定用 TP1 口径（与阶梯解耦）

阶梯加权 effective_rr（lever2）SHALL 只用于 **R:R 地板 gate**（判定是否开仓）。低 R:R 保护性缩仓/降杠杆判定（`low_rr_policies` 命中时的 `size_usdt` 缩放、`leverage` 上限、`rr_scale` 计算）MUST 用 **TP1 口径 effective_rr**（`effective_rr_tp1`），不得用阶梯值——否则阶梯抬高的 R:R 会把本应保护性缩仓的低-R:R 趋势单松绑成全仓满杠杆，意外放大敞口。地板 gate 与缩仓判定 SHALL 解耦：lever2 多开仓不变，保护性 sizing 不被阶梯松绑。

#### Scenario: 阶梯抬高仍保护性缩仓

- **WHEN** lever2 开、某 `long_aligned_low_rr` / `long_bullish_low_rr` 单的阶梯 effective_rr ≥ 1.5 但 TP1 口径 effective_rr < 1.5
- **THEN** 该单仍走低 R:R 保护性缩仓（`size_usdt` 缩放 + `leverage` 上限），不因阶梯口径松绑为全仓满杠杆

#### Scenario: lever2 关时零回归

- **WHEN** lever2 关（`ladder_rr_enabled=False`）
- **THEN** `effective_rr_tp1 == effective_risk_reward_ratio`，缩仓行为与改动前完全一致

### Requirement: Tactical R:R isolation from ladder-weighted Main R:R
The system SHALL keep Main Trend ladder-weighted `effective_risk_reward_ratio` separate from Tactical R:R. Tactical plans MUST expose their own Tactical R:R and EV fields and MUST NOT use Main Trend TP2/TP3 ladder assumptions for Tactical acceptance, sizing, ranking, or EV gates.

#### Scenario: Main ladder remains Main-only
- **WHEN** a candidate is classified as `track=main`
- **THEN** existing ladder-weighted R:R behavior MAY be used according to the Main Trend configuration
- **AND** the plan SHALL remain compatible with the existing ladder-weighted R:R requirements

#### Scenario: Tactical uses Tactical R:R
- **WHEN** a candidate is classified as `track=tactical`
- **THEN** acceptance and ranking SHALL use Tactical R:R and Tactical EV fields
- **AND** `effective_risk_reward_ratio` from Main ladder math SHALL NOT be the deciding Tactical acceptance value

#### Scenario: Reclassification recalculates payoff fields
- **WHEN** a Main candidate is downgraded into Tactical
- **THEN** the system SHALL recalculate stop distance, TP profile, net profit, net loss, R:R, and EV using the Tactical profile
- **AND** the plan SHALL retain both original Main diagnostic R:R and final Tactical R:R for audit
