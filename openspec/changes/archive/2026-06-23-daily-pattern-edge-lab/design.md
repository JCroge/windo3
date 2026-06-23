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
