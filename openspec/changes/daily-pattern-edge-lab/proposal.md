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
