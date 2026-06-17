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
