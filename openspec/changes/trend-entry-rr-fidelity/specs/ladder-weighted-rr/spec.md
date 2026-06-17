## ADDED Requirements

### Requirement: effective_rr 按真实阶梯离场加权

`effective_rr` 的计算 SHALL 反映 executor 真实的阶梯离场比例(TP1/TP2/trailing 各档平仓占比),而非仅用第一档 take_profit。各 TP 档的盈利贡献 MUST 按对应平仓比例加权计入分子。剩余 trailing 仓位的盈利贡献 SHALL 使用保守口径(如 +1R 锁利或 trailing 期望下界),MUST NOT 记为最远档满额。净成本(手续费+资金费)扣法 SHALL 保持现状(分子减、分母加)。

#### Scenario: 阶梯加权抬升趋势仓 R:R

- **WHEN** 一个计划的 TP 阶梯各档距离对应 executor 的 50/25/25 离场比例
- **THEN** `effective_rr` 按各档比例加权计算,结果不低于仅用 TP1 的口径(在阶梯各档为正贡献时)

#### Scenario: 剩余仓位保守口径

- **WHEN** 计算 trailing 剩余仓位(约 25%)的盈利贡献
- **THEN** 使用保守口径(+1R 锁利或 trailing 期望下界),不记为最远档满额

### Requirement: 各档到达概率折扣

加权 `effective_rr` 的各档盈利贡献 SHALL 乘以该档的到达概率 P(reach tierᵢ)。该概率 MUST NOT 默认全为 1(即不得假设各档必达),远档概率 MUST 不高于近档。v1 实现 SHALL 使用文档化、可辩护的**保守固定先验**(如 TP1=1.0 / TP2=0.5 / trailing=0.25),无需历史标定即可投入全样本回测;基于历史磁带/klines 频率的概率校准(v2)拆出本 change,不在本 change 范围内。所采用的概率取值 SHALL 可观测(记录在决策记录中)。

#### Scenario: 保守先验防注水

- **WHEN** 远档(如 TP2/trailing)使用低于 TP1 的固定先验概率
- **THEN** 该档盈利贡献按先验概率折扣后计入,远档不显著抬高 effective_rr

#### Scenario: 概率取值可观测

- **WHEN** effective_rr 使用各档到达概率
- **THEN** 所用概率取值随 effective_rr_ladder 一并记录,可在决策记录中回溯

### Requirement: 全样本回测背书与灰度

阶梯加权 effective_rr SHALL 在 event_backtest 上对**全样本(含亏单)** A/B,产出净 PnL/胜率/MDD delta。若 event_backtest 未建模阶梯离场,MUST 先补阶梯离场建模再做 A/B。新口径 SHALL 通过 config 开关灰度,回测背书前不直接全量上线。

#### Scenario: 含亏单的全样本 A/B

- **WHEN** 新旧 effective_rr 口径在 event_backtest 上对比
- **THEN** 回测覆盖全样本(趋势赢家 + 同期亏单),净效果以含亏单的 delta 为准,而非仅趋势赢家

#### Scenario: event_backtest 阶梯离场前置

- **WHEN** 现有 event_backtest 仅按单档 SL/TP 结算
- **THEN** 在做②的 A/B 前先补 50/25/25 阶梯离场 + trailing 建模,否则 A/B 结果不被采信

#### Scenario: config 灰度开关

- **WHEN** 阶梯加权口径的 config 开关关闭
- **THEN** effective_rr 计算回退到改动前的 TP1 口径
