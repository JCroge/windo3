## ADDED Requirements

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
