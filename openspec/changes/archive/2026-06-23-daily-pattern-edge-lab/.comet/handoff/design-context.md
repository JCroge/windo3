# Comet Design Handoff

- Change: daily-pattern-edge-lab
- Phase: design
- Mode: compact
- Context hash: 73f1eada9744b0a3f1d7bc236b05f2f716dd9c479fdfcb3a9182726a90d01c7e

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/daily-pattern-edge-lab/proposal.md

- Source: openspec/changes/daily-pattern-edge-lab/proposal.md
- Lines: 1-31
- SHA256: 2233f04e8ed8dea2e8fe3223ad9d31d6b00a90609ead740c53fbe0fb6333b9cd

```md
## Why

四轮严格诊断已坐实:现策略方向决策无 edge(信号分↔实盈 Spearman ρ≈0),根结点是「赌动量但市场无动量」——这些标的的收益自相关≈0(微负)、延伸末端继续率仅 41.7%。全部价格类 alpha 源(MA 趋势/均值回归/OI/funding/taker/盘口/爆仓)在严格 SL/TP+成本+样本外检验下证伪;跨所基差/做市/carry 经团队历史排除。

蜡烛形态是唯一尚未被严格证伪、且理论上顺应市场轻微均值回归倾向的方向假设。但本轮实证:**日内(~1h)形态无 cost-surviving edge**(裸形态是噪声、确认型滤镜方向反了=给追顶盖章)。形态唯一可能成立的战场是**日线/波段尺度**——一波移动 5-15% ≫ 往返成本 ~20bp(成本地板从打平变有余),且 Binance 日线可拉 ~2.75 年跨多体制。本 change 建一个 observability-only 研究骨架,给「形态在日线尺度有无可交易 edge」一个带样本外 + 多重比较校正的**确定答案**(正或负)。

## What Changes

- **改造 `fetch_historical_klines.py`**:从单次 100 根的玩具脚本升级为带分页、多币、多周期的历史抓取器,落 `data/klines.db`(复用现有 schema,`UNIQUE(symbol,interval,open_time)` 幂等)。目标 ~50 币 × 日线(2.75 年)+ 4h(锁为确认集,不进第一轮搜索)。
- **新建 `utils/candlestick_patterns.py`**:手写 ~28 种标准蜡烛形态识别器(TA-Lib 未装且为 C 编译痛点 → 零新依赖),**固定阈值禁调**,每形态预登记方向假设。
- **新建 `cf_pattern_edge_discovery.py`**:repo 根研究驱动(镜像 `cf_oi_divergence_ab.py`),复用 `resolve_counterfactual` + `cf_honesty_gate`;ATR-based 退出 + 上下文条件化(range_pos/趋势/前置移动)+ train(2023-24)/val(2025)/test(2026) 三分 + Bonferroni/FDR 多重比较校正;权重 = 样本外实测净 R(三关全过才非零,否则 0)。
- **`tests/test_cf_red_line_guard.py` 加 `test_decision_paths_do_not_read_pattern_research`**:守卫决策/风控路径禁读本研究产物(与现有 CF 红线一致)。

**非破坏**:纯新增 + 改造一个孤立玩具脚本;不碰 live 决策链路、不改 config 语义。

## Capabilities

### New Capabilities
- `pattern-edge-discovery`: 在日线/波段历史 OHLC 上,用预登记标准蜡烛形态库 + 上下文条件化 + 真实成本 SL/TP 回测 + 样本外三分 + 多重比较校正,量化每个(形态 × 上下文)桶的可交易 edge;observability-only,输出供人审,绝不接入 live 决策。

### Modified Capabilities
<!-- 无:不改任何现有 capability 的 spec 级行为。fetch_historical_klines.py 是孤立玩具脚本,无 spec。 -->

## Impact

- **新增文件**:`utils/candlestick_patterns.py`、`cf_pattern_edge_discovery.py`、`openspec/changes/daily-pattern-edge-lab/specs/pattern-edge-discovery/spec.md`。
- **改造文件**:`fetch_historical_klines.py`(玩具脚本升级,无下游依赖)、`tests/test_cf_red_line_guard.py`(加守卫断言)。
- **数据**:`data/klines.db` 新增 interval='1d'/'4h' 行(幂等,不影响现有 1h/15m legacy 行);不碰 `data/klines_1s.db`、不碰 live 状态文件。
- **依赖**:零新增(用已装 ccxt/pandas/numpy)。
- **红线**:严守 CF observability-only 红线——决策/风控路径(judge/executor/risk_guard/reviewer/position_analyst)禁读本研究产物,新增守卫测试。
- **风险**:数据有功效 ≠ 形态有 edge;诚实先验偏怀疑。骨架买到的是确定答案(正/负),非保证盈利。
```

## openspec/changes/daily-pattern-edge-lab/design.md

- Source: openspec/changes/daily-pattern-edge-lab/design.md
- Lines: 1-38
- SHA256: 6e1bb60b463a81a25e0bd7cac8fa01c4b42b29c76d8b65198d98be03fe660943

```md
## Context

现策略方向决策无 edge(根结点:赌动量但市场无动量,收益自相关≈0)。价格类 alpha 源已全证伪;套利/做市/carry 经团队排除。形态思路在日内已证无 cost-surviving edge,唯一可能成立的战场=日线/波段(成本可忽略 + 多体制)。已有反事实基础设施:`utils/counterfactual_pnl.py::resolve_counterfactual`(真实 CostModel + SL/TP 路径 + SL-first 保守)、`utils/cf_honesty_gate.py::summarize_bucket`(Wilson + bootstrap CI + 薄样本拒答)、repo 根 `cf_*.py` 研究驱动模式(observability-only)、`data/klines.db`(schema 含 symbol/interval/OHLC)。约束:严守 CF 红线(决策/风控路径禁读研究产物);零新依赖(TA-Lib 未装)。

## Goals / Non-Goals

**Goals:**
- 给「日线蜡烛形态有无可交易 edge」一个带样本外 + 多重比较校正的确定答案(正或负)。
- 全程 observability-only,复用现有 CF 骨架,零新依赖。
- 防过拟合为第一原则:预登记形态集 + 固定阈值 + OOS 三分 + 多重比较校正。

**Non-Goals:**
- 不接入 live 决策(纯研究);不自动改 config/权重上线。
- 第一轮不优化退出参数(固定 ATR 套);不做日内时间框架(已证无效)。
- 4h 不进第一轮搜索维度(仅锁为稳健性确认集)。

## Decisions

- **数据库复用 `data/klines.db`**(而非新库):schema 完全吻合,`fetch_historical_klines.py` 已 target 它,`UNIQUE(symbol,interval,open_time)` 给幂等。interval 区分 1d(主测)/4h(确认集)。*备选:新建 klines_daily.db — 否决,无必要的碎片化。*
- **形态库手写**(`utils/candlestick_patterns.py`,而非 TA-Lib):TA-Lib 未装且为 C 编译痛点;手写=零新依赖 + 定义可控 + "禁调阈值"更诚实。固定阈值,预登记 ~28 种(含反转/延续/中性)。
- **退出策略 ATR-based 固定**:`SL=entry∓1.5×ATR(14)`、`TP=entry±3.0×ATR`(~2:1)、`max 持仓 10 日`时间止损。SL/TP 路径优先 4h bar 解析(更细)否则日线 SL-first 保守。主测固定,扫描列次要。*备选:固定%SL/TP — 否决,跨币波动差异大,ATR 自适应更公平。*
- **OOS 三分**:train 2023-24 / val 2025 / test 2026。edge 须三段同号才算稳健。*备选:单段全样本 — 否决,无法防过拟合。*
- **多重比较校正**:~28 形态 × 方向 → Bonferroni/FDR 收紧显著门(校正因子 ~40-56)。复用 `cf_honesty_gate` 并叠加校正。
- **加权口径**:`weight=max(0, OOS净R/笔)`,须同时过 诚实门(n≥30 且 CI 不跨 0)+ 校正后显著 + 三段同号,否则 0。权重是验证的**输出**,非直觉输入。
- **驱动模式**:`cf_pattern_edge_discovery.py` 镜像 `cf_oi_divergence_ab.py`(load→define rules→fire(簇去重)→settle→aggregate→gate)。

## Risks / Trade-offs

- [数据有功效 ≠ 形态有 edge] → 诚实先验偏怀疑;骨架买的是确定答案非盈利保证;两种结局(挖到/证伪)都有价值。
- [新币历史短(TRUMP 521 根/TON 685/HYPE 不在 Binance)] → 按可得历史加权;短史币标注、不强行凑数。
- [日线 SL/TP 同根歧义] → 优先 4h 解析;否则 SL-first 保守(继承 resolve_counterfactual 既有处理)。
- [形态多 → 多重比较膨胀] → Bonferroni/FDR 收紧;预登记锁死形态集,严禁测试中追加/调阈值(防 p-hacking)。
- [红线泄漏:研究产物被决策路径读取] → 新增 `test_decision_paths_do_not_read_pattern_research` 守卫。

## Open Questions

- 上下文桶的具体切分(range_pos 阈值档、趋势用哪条 MA、前置移动窗口)→ 留 comet-design 深化(预登记后锁死)。
- 4h 解封后的确认口径(同号 + 强度衰减容忍)→ 候选出现后再定。
```

## openspec/changes/daily-pattern-edge-lab/tasks.md

- Source: openspec/changes/daily-pattern-edge-lab/tasks.md
- Lines: 1-29
- SHA256: a2382071c6049aa17cbbe32ee7e8e413a54c24299ec93eb9f54fa9c1ade1c7a8

```md
# Tasks: daily-pattern-edge-lab

## 1. 历史数据抓取
- [ ] 1.1 改造 `fetch_historical_klines.py`:加分页(while 循环按 since 翻页至无新数据)、多币列表、多周期参数
- [ ] 1.2 落 `data/klines.db`,沿用 `UNIQUE(symbol,interval,open_time)`,`INSERT OR IGNORE` 保证幂等
- [ ] 1.3 跑 ~50 币 × 1d(2.75 年)入库;4h 同步入库(锁为确认集,不进第一轮)
- [ ] 1.4 入库后自检:打印每币 interval 根数 + 起始日期 + 短史币标注

## 2. 形态库(预登记、固定阈值)
- [ ] 2.1 新建 `utils/candlestick_patterns.py`,实现 ~28 种标准形态识别器(单K/双K/三K,反转+延续+中性)
- [ ] 2.2 每形态返回 (名称, 预登记方向);阈值全部固定常量,无调参入口
- [ ] 2.3 形态库单测 `tests/test_candlestick_patterns.py`:对构造的已知形态序列断言识别正确

## 3. 边缘发现骨架
- [ ] 3.1 新建 `cf_pattern_edge_discovery.py`(镜像 `cf_oi_divergence_ab.py` 结构),载入 klines.db + 计算 ATR(14)
- [ ] 3.2 上下文条件化:range_pos(N 日区间位置)/ 趋势(价 vs MA)/ 前置移动,分桶
- [ ] 3.3 ATR 退出 + `resolve_counterfactual` 结算(SL/TP 优先 4h 解析否则日线 SL-first);簇去重
- [ ] 3.4 train(2023-24)/val(2025)/test(2026) 三分统计每(形态×上下文)桶
- [ ] 3.5 多重比较校正(Bonferroni/FDR)+ 复用 `cf_honesty_gate.summarize_bucket`
- [ ] 3.6 加权:`weight=max(0,OOS净R)`,三关全过才非零;输出 edge 报告(全桶 + 过关桶 + 权重)

## 4. 红线守卫
- [ ] 4.1 `tests/test_cf_red_line_guard.py` 加 `test_decision_paths_do_not_read_pattern_research`(判 judge/executor/risk_guard/reviewer/position_analyst 不 import 形态研究模块)
- [ ] 4.2 跑全量 pytest 确认无回归(基线 1359 passed)

## 5. 验收与汇报
- [ ] 5.1 跑骨架产出首版 edge 报告(日线主测)
- [ ] 5.2 诚实汇报:有无过三关的形态;若有 → 进入 4h 确认集解封;若无 → 干净证伪结论
- [ ] 5.3 结论写入项目记忆(更新 alpha-source-hunt-verdict 或新建条目)
```

## openspec/changes/daily-pattern-edge-lab/specs/pattern-edge-discovery/spec.md

- Source: openspec/changes/daily-pattern-edge-lab/specs/pattern-edge-discovery/spec.md
- Lines: 1-63
- SHA256: 7a31eb95a527603fce4957a515a318bc1065462e2e962e13edf8e8fb810edf31

```md
## ADDED Requirements

### Requirement: 历史 OHLC 幂等抓取
系统 SHALL 提供带分页的历史 OHLC 抓取器,支持多币种、多周期(至少 1d 与 4h),并以 `UNIQUE(symbol,interval,open_time)` 落入 `data/klines.db`,重复运行 MUST 幂等(不产生重复行)。

#### Scenario: 抓取日线并落库
- **WHEN** 对给定币种列表以 interval=1d 运行抓取器
- **THEN** 系统分页拉取全部可得历史(单次上限 1000 根)并写入 `data/klines.db`,interval 字段记为 `1d`

#### Scenario: 重复运行幂等
- **WHEN** 对同一币种/周期/时间范围再次运行抓取器
- **THEN** 已存在的 (symbol,interval,open_time) 行不被重复插入,行数不增加

#### Scenario: 短历史币标注
- **WHEN** 某币种可得历史不足(如新上市)
- **THEN** 系统按实际可得根数落库,不报错、不补造数据,并在汇总中标注其历史长度

### Requirement: 预登记标准形态库
系统 SHALL 提供一个手写的标准蜡烛形态识别库,形态集与每形态的方向假设(看涨/看跌/中性)在测试前预登记并固定;形态判定阈值 MUST 为固定常量,运行期 SHALL NOT 依据收益数据调参。

#### Scenario: 识别预登记形态
- **WHEN** 对一段 OHLC 序列调用形态库
- **THEN** 返回该序列上命中的预登记形态及其预登记方向(如 Hammer→看涨、Shooting Star→看跌、Rising Three Methods→看涨延续)

#### Scenario: 阈值不可调
- **WHEN** 在回测流程中运行形态识别
- **THEN** 形态定义阈值取自固定常量,流程 SHALL NOT 提供按结果调阈值的路径

### Requirement: 真实成本 ATR 退出回测
系统 SHALL 对每个形态信号以 ATR-based 退出(SL=entry∓1.5×ATR(14)、TP=entry±3.0×ATR、最长持仓 10 日)经 `resolve_counterfactual` 用真实 CostModel 与 K 线路径结算净收益;同根 SL/TP 冲突 MUST 取 SL-first 保守。

#### Scenario: 结算单个形态信号
- **WHEN** 在某日线 bar 命中一个看涨形态
- **THEN** 系统以该 bar 收盘为 entry、按 ATR 设 SL/TP,沿后续 K 线路径结算 outcome(tp/sl/expired)与净 R

#### Scenario: 簇去重
- **WHEN** 同一 symbol+方向在簇窗口内多次命中
- **THEN** 仅取一次进入样本,避免重叠样本自相关夸大显著性

### Requirement: 样本外三分与多重比较校正
系统 SHALL 将样本按时间切为 train(2023-24)/val(2025)/test(2026) 三段分别统计,并对所测形态数施加多重比较校正(Bonferroni 或 FDR);一个(形态×上下文)桶 SHALL 仅在三段同号、且校正后仍显著、且过诚实门时才被判为可信 edge。

#### Scenario: 三段同号校验
- **WHEN** 某形态在 train 为正、val 为负
- **THEN** 该形态判为不稳健,不计入可信 edge

#### Scenario: 校正后显著
- **WHEN** 测试 N 个形态且某形态名义 p 显著但未过 Bonferroni/FDR 校正
- **THEN** 该形态判为不显著(探索性),不计入可信 edge

### Requirement: 诚实加权(过关才非零)
系统 SHALL 按 `weight = max(0, OOS 样本外净 R/笔)` 赋权,且仅当该形态同时通过 诚实门(n≥30 且净 PnL CI 不跨 0)、校正后显著、三段同号时权重才非零,否则权重 MUST 为 0;权重 SHALL 为验证输出,SHALL NOT 由人工直觉设定。

#### Scenario: 不过关形态权重为零
- **WHEN** 某形态样本 n<30 或 CI 跨 0
- **THEN** 其权重被置为 0,不进入最终方向信号

### Requirement: Observability-only 红线
本能力的所有产物(抓取数据外的研究输出、形态信号、edge 报告)SHALL 为 observability-only;任何交易决策/风控路径(judge/executor/risk_guard/reviewer/position_analyst)MUST NOT import 或读取本研究模块/产物。

#### Scenario: 红线守卫测试
- **WHEN** 运行 `tests/test_cf_red_line_guard.py`
- **THEN** 存在断言验证决策/风控路径源码未 import 形态研究模块,违反则测试失败
```

