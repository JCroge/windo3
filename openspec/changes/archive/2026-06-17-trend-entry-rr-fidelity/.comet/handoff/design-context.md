# Comet Design Handoff

- Change: trend-entry-rr-fidelity
- Phase: design
- Mode: compact
- Context hash: ab8eac41b8884bbdb791fcba45c0b89c3863517e4f278b3b7584fefccc99b362

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/trend-entry-rr-fidelity/proposal.md

- Source: openspec/changes/trend-entry-rr-fidelity/proposal.md
- Lines: 1-37
- SHA256: cf7216ddf7e34dbb4459e6ad058c188682572241f38425bf9d3736cd333f8aff

```md
## Why

实战诊断(2026-06-17)发现:近三天系统对 4 个**干净趋势**(HYPE/WLD/UNI long、NEAR short)**全程零开仓**。这些趋势入场后沿途最深逆行仅 **0.1–0.3R**、峰值有利 **1.9–9.5R**(先到 +1R 再触 -1R),却被入场 gate 连续两三天逐根拒绝。根因不是市场无机会,也不是 R:R 地板数值(反事实实验室已证伪降地板无效),而是 gate 的两处**口径与现实不一致**:

1. **趋势对齐失败 → 拿错地板**:这些干净趋势被 regime 分类器判为 `choppy`,且 `htf_bias/daily_bias` 未识别出 bullish,导致 `_select_rr_floor` 的 `long_aligned` 条件不满足,落到 **default 1.50** 而非趋势对齐的 **1.30** 地板。
2. **effective_rr 只数 TP1 首档**:`judge.py:_build_plan` 用 `take_profit[0]` 单档算 R:R,而 executor 实际是 **50% @TP1 / 25% @TP2 / 25% trailing** 的阶梯离场。gate 把会吃到 TP2/TP3/trailing 的趋势仓,按"100% 在最近小档离场"评分,系统性压低 R:R(HYPE 真实几何 1.58 被记为 effective 1.19)。

两个杠杆互补:修①解锁 WLD(1.41)/UNI(1.38),修②额外解锁 HYPE(1.19→保守阶梯口径 1.82)。

## What Changes

> 范围已收敛到最小闭环(design 阶段锁定)。① P2(bias 上游根治)与 ② v2(到达概率频率校准)各起新 change,不在本 change 内。

- **新增 `trend-aligned-rr-floor`(本期 P1)**:`_select_rr_floor` 的 `long_aligned` 判定增加「客观路径证据」OR 分支——用**入场前**数据(近窗方向一致性、近窗浅回撤、延展未过热)识别干净趋势,授予趋势对齐地板(1.30 级)而非 default,真 choppy 仍落 default。**禁前视**。bias 信号上游根治(P2)拆出本 change。
- **新增 `ladder-weighted-rr`(本期 v1)**:`effective_rr` 改为按 executor 真实平仓阶梯(50/25/25)对各 TP 档加权,乘**保守固定先验概率**(TP1=1.0/TP2=0.5/trailing=0.25,MUST NOT 全=1),剩余 trailing 仓位用**保守口径**(+1R 锁利,不记最远档满额),净成本扣法保持。基于历史频率的概率校准(v2)拆出本 change。
- **回测护栏**:两个杠杆均必须在 `event_backtest`(已建模 50%@TP1+trailing)上对**全样本(含亏单)** A/B,产出净 PnL/胜率/MDD delta;**禁止注水**——保守先验与保守剩余口径是硬要求,任何提高 R:R 评分的改动都要被全样本回测净效果背书。
- **observability/灰度**:改动经 config 开关(默认关),先回测后灰度,不直接全量上线。

## Capabilities

### New Capabilities
- `trend-aligned-rr-floor`: 入场 R:R 地板选择如何识别"干净趋势"并授予趋势对齐地板(含 regime/HTF/日线对齐判据与证据门槛),取代干净趋势被误落 default 的现状。
- `ladder-weighted-rr`: `effective_rr` 如何按真实阶梯离场比例 + 各档到达概率折扣 + 保守剩余口径计算,使 gate 评分对齐 executor 实际离场策略且不注水。

### Modified Capabilities
<!-- 无:现有 specs 未覆盖入场 R:R 地板选择或 effective_rr 计算,均为未 spec 化实现,故立为新 capability。 -->

## Impact

- **代码**:
  - `agents/trading/judge.py` — `_select_rr_floor`(杠杆①)、`_build_plan` 的 `effective_rr` 计算(杠杆②)。
  - `utils/market_regime.py` — 趋势对齐判据来源(杠杆①,若需补强 htf/daily bias 信号)。
  - 新增 per-tier 到达概率估计模块(杠杆②)。
  - `event_backtest.py` — 支持以新旧 R:R 口径 A/B 回放并模拟 50/25/25 阶梯离场(若现有 backtest 仅 SL/TP 单档,需补阶梯离场建模,否则无法测出真实净效果)。
- **配置**:新增 R:R 口径/趋势对齐开关与概率折扣参数(config 灰度)。
- **红线**:observability/回测优先,不改线上 config 直至全样本回测背书;与反事实实验室口径保持一致。
- **依赖风险**:per-tier 到达概率无现成数据,需先标定;event_backtest 可能未建模阶梯离场,是关键前置。
```

## openspec/changes/trend-entry-rr-fidelity/design.md

- Source: openspec/changes/trend-entry-rr-fidelity/design.md
- Lines: 1-38
- SHA256: d3de35d2f46239f9937fc0f7a3df96f007fb82b4fe3334f26d8c8510f7c8cba3

```md
## Context

入场 gate(`agents/trading/judge.py`)对干净趋势零开仓的根因已在 explore 阶段定位到两处口径失配:

- **杠杆①（地板选择）**:`_select_rr_floor` 已有 `long_aligned`(choppy/mixed 下 long 趋势对齐 → 1.30 地板)路径,但其判定要求 `sym_dir==bullish AND (htf_bias OR daily_bias == bullish) AND not block_long AND |score|>=45`。实战中 HYPE/UNI 全程走 `default`(1.50)、WLD 仅 6/145 命中 aligned,说明 HTF/日线 bias 未识别出价格上明显的趋势。
- **杠杆②（R:R 口径）**:`_build_plan` 用 `take_profit[0]` 单档算 `effective_rr`,而 executor `_update_trailing` 实际执行 50% @TP1 / 25% @TP2 / 剩余 trailing 的阶梯离场(`executor.py:1354` `pct = 0.5 if partial_tp_1 else 0.25`)。gate 口径系统性低于真实离场策略。

现状数据基线:近三天被拒 1813 个计划,64% rr_below_floor / 30% quality_gate;4 个干净趋势(逆行 0.1–0.3R,峰值 1.9–9.5R)零开仓。

## Goals / Non-Goals

**Goals:**
- 让明确干净的趋势能拿到趋势对齐地板,不被误落 default。
- 让 `effective_rr` 口径对齐 executor 真实阶梯离场,且**乘各档到达概率折扣 + 剩余仓位保守口径**,不注水。
- 两杠杆均可在 `event_backtest` 上对**全样本(含亏单)** A/B,以净 PnL/胜率/MDD delta 背书。

**Non-Goals:**
- 不降低 R:R 地板数值(反事实实验室已证伪;地板值不在本 change 内调整)。
- 不改 executor 的离场比例(50/25/25 视为既定事实,本 change 让 gate 口径去对齐它,而非反过来)。
- 不直接全量上线;config 灰度 + 回测背书优先。

## Decisions

> 以下为高层架构决策与待 brainstorming 锁定的开放项。具体 HOW(概率标定方法、对齐判据补强方式)在 comet-design 的 brainstorming 阶段锁定。

1. **两个 capability 分离**:`trend-aligned-rr-floor`(①)与 `ladder-weighted-rr`(②)各自独立、可单独 A/B,便于隔离归因与按需拆 change。
2. **杠杆①方向**:优先修正/补强趋势对齐判据来源(HTF/日线 bias 为何对干净趋势返回 neutral),而非简单放宽阈值——避免把真 choppy 也放进趋势地板。判据可叠加"低逆行/路径干净度"等客观证据。【开放:补强 bias 信号 vs 增加客观路径证据,brainstorming 定】
3. **杠杆②口径**:`effective_rr = (Σ wᵢ·P(reach tierᵢ)·profitᵢ·notional − cost) / (max_loss + cost)`,wᵢ=[0.5,0.25,0.25] 对齐 executor;剩余 25% trailing 仓位 profit 用保守口径(+1R 锁利或 trailing 期望下界)。【开放:P(reach tierᵢ) 标定方法——历史磁带频率 vs 模型,brainstorming 定】
4. **回测前置**:确认 `event_backtest` 是否已建模阶梯离场;若仅 SL/TP 单档,先补阶梯离场建模,否则无法测出②的真实净效果。这是②的硬前置。
5. **灰度护栏**:新口径经 config 开关,默认走回测/灰度;与反事实实验室口径一致,observability 优先。

## Risks / Trade-offs

- **注水风险(②)**:若 P(reach tierᵢ) 估计偏乐观或剩余仓位记满档,会把虚高 R:R 喂给 gate,自欺。缓解:概率折扣 + 保守剩余口径为硬要求,全样本(含亏单)回测净效果背书。
- **幸存者偏差**:4 个趋势是赢家样本;放宽入场会同时放进同期 461 个 naive SL。缓解:回测必须全分布,不能只看趋势赢家。
- **杠杆①过放宽**:把真 choppy 误授趋势地板 → 引入低质量入场。缓解:对齐判据叠加客观证据,回测验证胜率不被稀释。
- **前置依赖(④)**:event_backtest 若未建模阶梯离场,工作量上浮且是②的阻塞前置;可能触发 change 拆分。
- **概率数据缺失**:per-tier 到达概率无现成数据,需先标定,标定样本量/时效性影响可信度。
```

## openspec/changes/trend-entry-rr-fidelity/tasks.md

- Source: openspec/changes/trend-entry-rr-fidelity/tasks.md
- Lines: 1-32
- SHA256: 99f66742a323bcb61b73f3dbcb1fb8dd91b8f5c030a3ab0c279dbe0fc8979a8d

```md
<!-- 范围:① P1 客观路径证据 + ② v1 保守先验阶梯加权。P2(bias 根治)/v2(频率校准)拆出本 change。 -->

## 0. 前置(已基本厘清,留作核对)

- [ ] 0.1 核对 `event_backtest` 阶梯建模与 executor 差异:回测为 50%@TP1+trailing,executor 为 50/25/25;登记 TP2 折进 trailing 的小保真差,本期接受
- [ ] 0.2 确认入场前可得特征清单(pre_12h_return / 近窗回撤 / position_in_24h_range / tech.trend),供①客观证据使用,确保无前视

## 1. 杠杆① trend-aligned-rr-floor(P1 客观路径证据)

- [ ] 1.1 定义客观路径证据判据(近窗方向一致性 + 近窗浅回撤≤k·ATR + 延展未过热),全部用入场前数据
- [ ] 1.2 在 `judge.py:_select_rr_floor` 给 long_aligned 加 `OR 客观证据` 分支,授 1.30 级地板,rr_policy='long_aligned_path_evidence',记命中证据项
- [ ] 1.3 加 config 开关 `path_evidence_aligned_enabled`(默认关)+ 阈值参数
- [ ] 1.4 单元测试:干净趋势授对齐地板 / 真 choppy 不误授 / 开关关闭行为不变 / **反前视断言**

## 2. 杠杆② ladder-weighted-rr(v1 保守先验)

- [ ] 2.1 实现阶梯加权 effective_rr:w=[.5,.25,.25]、P=[1.0,.5,.25] 保守先验、剩余档 +1R 锁利保守口径、成本扣法不变、缺档归一化
- [ ] 2.2 决策记录并存 `effective_rr`(旧)与 `effective_rr_ladder`(新)+ 所用概率,可观测
- [ ] 2.3 加 config 开关 `ladder_rr_enabled`(默认关),关闭回退 TP1 口径
- [ ] 2.4 单元测试:阶梯加权≥TP1口径 / 远档低概率不注水 / 剩余保守 / 开关回退 / 缺档归一化

## 3. 全样本 A/B 与背书

- [ ] 3.1 杠杆① 在 event_backtest 全样本(含亏单)A/B,产出净 PnL/胜率/MDD delta
- [ ] 3.2 杠杆② 在 event_backtest 全样本(含亏单)A/B,产出 delta
- [ ] 3.3 ①+② 合并 A/B,确认净 PnL 改善且胜率不被低质量入场显著稀释,形成背书结论

## 4. 灰度与收尾

- [ ] 4.1 按背书结论配置 config 灰度(默认关或小灰度),不直接全量
- [ ] 4.2 全量回归测试零回退;更新相关文档/记忆
- [ ] 4.3 登记后续拆出 change:① P2 bias 上游根治、② v2 到达概率频率校准
```

## openspec/changes/trend-entry-rr-fidelity/specs/ladder-weighted-rr/spec.md

- Source: openspec/changes/trend-entry-rr-fidelity/specs/ladder-weighted-rr/spec.md
- Lines: 1-48
- SHA256: 2c008a70bb53fd185c266ec8456baf72c4b2b4d7478451799ce9bf3729338542

```md
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
```

## openspec/changes/trend-entry-rr-fidelity/specs/trend-aligned-rr-floor/spec.md

- Source: openspec/changes/trend-entry-rr-fidelity/specs/trend-aligned-rr-floor/spec.md
- Lines: 1-41
- SHA256: 0caf3f2b7c7cf2ac280a34eee7c5794dc9bb59b51f3fc83948d8a49e656c5b00

```md
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

#### Scenario: 全样本回测背书

- **WHEN** 趋势对齐判定的放宽/补强在 event_backtest 上对全样本(含亏单)A/B
- **THEN** 产出净 PnL/胜率/MDD delta,且胜率不被低质量入场显著稀释(背书阈值在回测报告中明示)

### Requirement: 趋势对齐判定可观测且可配置

趋势对齐地板的判定 SHALL 记录其触发依据(命中的证据项与最终 rr_policy/rr_floor_reason),并 SHALL 通过 config 开关控制启用与证据门槛,默认走灰度,不直接全量改变线上行为。

#### Scenario: 判定依据可追溯

- **WHEN** 一个计划被授予或拒绝趋势对齐地板
- **THEN** 决策记录中包含 rr_policy、rr_floor_reason 及命中的客观证据项,可在被拒/被放行事件中回溯

#### Scenario: config 灰度开关

- **WHEN** 趋势对齐放宽的 config 开关关闭
- **THEN** 地板选择行为与改动前一致(default 路径不变)
```

