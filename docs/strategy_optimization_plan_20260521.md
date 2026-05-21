# 策略层优化方案：方向分层、动态 EV 与反事实信号闭环

日期：2026-05-21  
范围：`Judge` 策略层、事件回测、Paper/反事实信号跟踪、风控保护。  
输入材料：2026-05-20 至 2026-05-21 日志、`data/trade_history.json`、用户对过去约 18 小时未开仓信号的回溯结果。  

## 1. 结论摘要

过去 18 小时的数据不能直接证明策略已经有稳定 edge，样本太小，且很多信号不是独立样本。但它指出了一个明确方向：当前开仓门槛过于“全局化”，没有把 long / short、市场 regime、信号原型和 R:R 分开定价。

建议不要简单改成“全部放行做多”，而是落成四层策略调整：

1. **long / short 分离评估**：做多和做空应视为两套策略，拥有独立胜率、EV、R:R 下限和冷却。
2. **市场 regime 过滤**：偏多市场中，short 默认降权或禁用；只有高时间框架共振、资金拥挤/超买、15m 转弱同时成立时才允许做空。
3. **R:R 从硬门槛改为条件门槛**：在 bullish regime 的高质量 long 中，允许 `effective_rr` 从 1.5 放宽到 1.2-1.5，但必须降低仓位、分批止盈、移动止损；short 反而提高到 1.6-1.8。
4. **建立 rejected signal 反事实账本**：所有被 Judge 拒绝但已有 plan 的信号都进入 shadow ledger，后续用价格流判定假设 TP/SL，持续校准 `side x regime x archetype` 的真实胜率。

## 2. 本地日志证据

### 2.1 用户回溯结论

用户对过去约 18 小时代表性信号的回溯：

| 指标 | 数值 |
|---|---:|
| 代表性信号 | 21 |
| 已触发 TP | 7 |
| 已触发 SL | 9 |
| 未触发 | 4 |
| 整体假设胜率 | 43.8% |
| long 假设胜率 | 66.7%（6/9） |
| short 假设胜率 | 14.3%（1/7） |

这个结果最重要的信息不是“应该无脑做多”，而是“全局胜率会掩盖方向差异”。如果继续用一个 `_recent_win_rate` 或一个 Bayesian prior 给所有方向定价，系统会在 short 连续亏损后误杀潜在 long 机会。

### 2.2 日志抽样统计

基于 `logs/agent_judge_20260520.log` 的简单解析：

| 项目 | 观察 |
|---|---:|
| `Plan` 记录数 | 456 |
| 质量门拦截 | 391 |
| `confidence < min_confidence` | 376 |
| `liquidity_score=0` | 15 |
| `R:R < 1.5` 触发回调/追价逻辑 | 11 |
| LLM 确认 short | 48 |
| LLM 确认 long | 13 |
| plan 平均 effective R:R | 1.30 |
| 正 EV plan | 166 |
| 负 EV plan | 290 |

高频出现的质量门拦截集中在 `CHZ-USDT`、`TON-USDT`、`ONDO-USDT`、`HYPE-USDT`、`UNI-USDT`、`BCH-USDT`。其中 `BCH-USDT` 的 LLM short 确认在日志里特别集中，说明做空不是偶发，而是当前评分/LLM 修正体系在偏多市场中持续产生 short 候选。

典型日志：

```text
ONDO-USDT R:R=1.31<1.5，score=-40弱信号，等待回调
INJ-USDT R:R=1.27<1.5，score=-35弱信号，等待回调
TON-USDT LLM确认open_short方向，confidence提升至70
TON-USDT 实盘开仓质量门拦截: liquidity_score=0<min_liquidity_for_weak_signal
HYPE-USDT 实盘开仓质量门拦截: confidence=40<min_confidence=60
```

### 2.3 当前机制的核心问题

`agents/trading/judge.py` 当前已有不少保护，但几个机制叠加后会产生副作用：

1. **全局 EV 口径过粗**  
   `_get_p_win()` 当前使用近期全局胜率或 Bayesian prior。short 亏损会拖低所有后续 long 的 p_win，导致 long 被 EV 门拦截。

2. **R:R 语义和胜率没有联动**  
   当前 `effective_rr < 1.5` 大多进入回调/追价，而不是根据方向胜率动态计算。若 long 在 bullish regime 的真实胜率能稳定 >60%，1.2-1.5 的 R:R 可以成立；若 short 胜率只有 14%，即使 1.5 R:R 也不应该放行。

3. **short 缺少市场 regime 闸门**  
   代码有 RSI、4h RSI、HTF bias 保护，但缺少“全市场偏多时 short 只做极端反转”的顶层闸门。

4. **PaperExecutor 不能覆盖 rejected plan**  
   当前 PaperExecutor 订阅的是 `trade_decision:*`。如果 Judge 直接 hold 或质量门拒绝，它不会把“被拒但有 plan 的信号”作为反事实交易继续跟踪，导致系统很难量化“错过机会”。

## 3. 对标项目经验

### 3.1 Freqtrade：保护机制要支持 pair / side 分层

Freqtrade 的 `StoplossGuard` 会在一定窗口内止损次数过多时停交易；`MaxDrawdown` 用窗口内交易评估回撤；`LowProfitPairs` 可按 pair 锁定低收益标的，并且 futures 场景支持 `only_per_side`，即只锁某一方向；`CooldownPeriod` 用于平仓后的 pair 级冷却。参考：<https://docs.freqtrade.io/en/stable/plugins/>

可迁移到本项目：

- 新增 `SidePerformanceGuard`：按 `(symbol, side)` 和 `(side, regime)` 统计 PF、胜率、连续 SL。
- 新增 `ShortRegimeGuard`：bullish regime 下，short 需要更高阈值；连续 short SL 后只锁 short，不影响 long。
- 现有 `ArchetypeCooldown` 应扩展方向字段，否则 `standard` / `counter_trend` 仍会把 long 和 short 混在一起。

### 3.2 Freqtrade：入场确认、动态 ROI、动态止损、加减仓是不同职责

Freqtrade 的 strategy callbacks 把 `confirm_trade_entry()` 作为下单前最后确认；`custom_roi()` 支持按 side、pair、entry tag 或 ATR 设置 ROI；`custom_stoploss()` 支持动态/阶梯止损；`adjust_trade_position()` 支持加减仓；`leverage()` 可动态返回杠杆。参考：<https://docs.freqtrade.io/en/latest/strategy-callbacks/>

可迁移到本项目：

- `Judge._open_quality_rejection()` 对齐 `confirm_trade_entry()`：只做轻量最终闸门，不重新计算复杂指标。
- `_build_plan()` 对齐 `custom_roi()`：TP/R:R 应允许按 side/regime/entry_type 变化。
- `PositionAnalyst` 对齐 `custom_stoploss()` 与 `adjust_trade_position()`：long 低 R:R 入场后必须更早移动 SL、分批止盈。
- `_calc_risk_budget()` 对齐 `leverage()`：低 R:R 放行时杠杆必须下降，不允许用 20x 放大低质量边界。

### 3.3 Freqtrade / Hummingbot：参数优化要多目标，且要防过拟合

Freqtrade Hyperopt 支持优化 ROI、stoploss、trailing、max open trades，并有 Sharpe、Sortino、ProfitDrawDown、MultiMetric 等目标函数；文档也强调结果可复现、参数文件覆盖顺序和避免过大搜索空间。参考：<https://www.freqtrade.io/en/stable/hyperopt/>

Hummingbot V2 把 strategy 拆成 Controller 和 Executor；回测会模拟 executor 行为，包括 triple barrier、止损、止盈、时间限制、trailing、费用、滑点，并建议参数网格搜索、walk-forward、paper trading 验证。参考：<https://hummingbot.org/strategies/> 和 <https://mintlify.wiki/hummingbot/hummingbot/development/backtesting>

可迁移到本项目：

- `event_backtest.py` 应先补齐线上 Judge 的新参数，再做 grid search。
- 优化目标不能只看总 PnL，应同时约束：PF、max drawdown、avg loss、交易数、long/short 分项 PF、short 在 bullish regime 的亏损。
- 反事实信号账本要像 Hummingbot executor simulator 一样按 plan 追踪 TP/SL，不依赖真实下单。

## 4. 策略改造设计

### 4.1 引入 MarketRegime

新增 `utils/market_regime.py`，输出：

```python
{
    "regime": "bullish|bearish|mixed|choppy",
    "confidence": 0-100,
    "basis": {
        "active_symbols_above_ma": 0.0,
        "btc_4h_bias": "bullish",
        "eth_4h_bias": "bullish",
        "breadth_24h_change": 0.0,
        "risk_on_count": 0,
    }
}
```

首版不需要复杂模型，用现有数据即可：

- 活跃标的中 1h/4h/daily bullish votes 的占比。
- BTC/ETH 或市场扫描 TopN 的 24h 中位涨跌幅。
- 资金费率极端和 RSI 过热比例。

建议初始规则：

| 条件 | regime |
|---|---|
| BTC/ETH 4h 不弱 + active bullish 占比 >= 60% + TopN 24h 中位涨幅 > 1% | bullish |
| BTC/ETH 4h 不强 + active bearish 占比 >= 60% + TopN 24h 中位涨幅 < -1% | bearish |
| bullish/bearish 都不成立且 ATR/振幅高 | mixed |
| 低波动 + 多数 neutral | choppy |

### 4.2 long / short 独立 EV

新增 `utils/performance_stats.py` 或扩展 `ReviewerAgent`，提供：

```python
get_p_win(symbol=None, side=None, regime=None, archetype=None) -> {
    "p_win": 0.0,
    "source": "rolling|bayesian_prior|fallback",
    "sample_size": 0,
    "avg_win": 0.0,
    "avg_loss": 0.0,
    "profit_factor": 0.0,
}
```

优先级：

1. `(side, regime, archetype)` 最近 20-50 笔。
2. `(side, regime)` 最近 20-50 笔。
3. `(side)` 最近 30-100 笔。
4. 全局 fallback。

Bayesian prior 也要分方向：

| key | prior 建议 |
|---|---|
| long/bullish | `prior_wins=3, prior_total=6` |
| short/bullish | `prior_wins=1, prior_total=6` |
| short/bearish | `prior_wins=3, prior_total=6` |
| mixed/choppy | `prior_wins=2, prior_total=6` |

这样 short 在 bullish regime 的连续亏损不会误杀 long；long 的表现也不会给 short 背书。

### 4.3 R:R 动态门槛

替换固定 `min_rr=1.5` 的决策语义。建议首版参数：

| 场景 | 放行条件 |
|---|---|
| bullish + long + htf_votes>=2 + 15m confirm + score>=50 | `effective_rr >= 1.2`，仓位乘 `rr_scale` |
| bullish + long + htf_votes<2 或 15m neutral | `effective_rr >= 1.4` |
| bullish + short | `effective_rr >= 1.8` 且 score<=-70 且 15m/1h/4h 同向 |
| bearish + short + htf_votes>=2 | `effective_rr >= 1.3` |
| bearish + long | `effective_rr >= 1.8` 或只允许反转极端信号 |
| mixed/choppy | `effective_rr >= 1.5`，维持保守 |

仓位缩放：

```text
effective_rr 1.20-1.30: size_usdt *= 0.40
effective_rr 1.30-1.40: size_usdt *= 0.60
effective_rr 1.40-1.50: size_usdt *= 0.80
effective_rr >=1.50: size_usdt *= 1.00
```

杠杆上限：

```text
RR<1.5: leverage <= 10x
RR<1.3: leverage <= 5x
short in bullish regime: leverage <= 5x
```

### 4.4 做空强过滤

基于这次回溯，short 不能继续与 long 同口径竞争。建议新增 `ShortRegimeGuard`：

bullish regime 下，short 只有以下任一组合可放行：

1. `daily_bias != bullish` 且 `higher_tf_bias == bearish` 且 `trend.direction == bearish`。
2. RSI >= 78 + bearish divergence + 15m bearish confirm。
3. 资金费率极端正值 + crowd 极端多 + 价格跌破 15m/1h 关键位。
4. 最近 2 小时市场 breadth 从 bullish 快速转 mixed/bearish。

否则：

- 不发 live open。
- 写入 rejected signal ledger。
- 可允许 PaperExecutor 或反事实账本跟踪，但不能占用 live 槽位。

### 4.5 低 R:R long 的持仓管理

只放宽入场不够，必须改退出：

1. TP1 = 0.8R 或 1.0R，平 40%-50%。
2. TP1 后 SL 移到 entry + 手续费缓冲。
3. TP2 = 原结构性 TP 或 1.8R。
4. 若 2 小时内未达到 0.5R，且 15m 转弱，提前减仓或退出。
5. 若 funding 成本升高或盘口流动性下降，禁止加仓。

这与当前 `event_backtest.py` 已有的 partial TP / break-even / trailing 方向一致，但需要同步到线上 `PositionAnalyst` 和 `ContractExecutor` 的实际执行路径。

## 5. 代码落点

### 5.1 第一阶段：只加 shadow 观察，不改 live 行为

目标：验证用户回溯结论能否在连续样本中成立。

新增：

- `utils/counterfactual_ledger.py`
- `data/rejected_signal_events.jsonl`
- `data/rejected_signal_lifecycle.json`

改动：

- `Judge` 在以下场景发布 `rejected_signal` 或写 ledger：
  - `confidence < min_confidence`
  - `liquidity_score=0`
  - `effective_rr < threshold`
  - EV gate 拒绝
  - 15m blocked / deferred
- `PaperExecutor` 或新 `SignalShadowTracker` 订阅 price_tick，按原 plan 追踪 TP/SL。

输出字段：

```json
{
  "symbol": "ONDO-USDT",
  "side": "long",
  "regime": "bullish",
  "entry_type": "rule_signal",
  "score": 53,
  "confidence": 40,
  "effective_rr": 1.31,
  "ev": 1.52,
  "reject_reason": "confidence<60",
  "entry_price": 0.3811,
  "sl": 0.3611,
  "tp": 0.4111,
  "created_at": 1770000000
}
```

验收：

- 每个被拒 plan 都可在 24 小时内归因为 `shadow_tp`、`shadow_sl`、`expired`。
- 报表能输出 long/short、regime、reject_reason 的胜率和 PF。

### 5.2 第二阶段：回测同构

改 `event_backtest.py`：

- 支持 `long_rr_floor` / `short_rr_floor`。
- 支持 `regime` 列。
- 支持 `side_regime_pwin` 或按历史滚动估算。
- 支持 `rr_scaled_position`。
- 支持 `short_regime_guard`。

新增测试：

- `test_directional_ev.py`
- `test_short_regime_guard.py`
- `test_counterfactual_ledger.py`
- `test_event_backtest_side_regime.py`

建议 grid：

```text
long_rr_floor: 1.15, 1.2, 1.3, 1.4, 1.5
short_rr_floor_bullish: 1.6, 1.8, 2.0
short_score_threshold_bullish: 60, 70, 80
long_min_score_bullish: 35, 45, 50, 55
rr_scaled_position: true/false
tp1_r: 0.8, 1.0, 1.2
move_sl_after_tp1: true
```

优化目标：

```text
primary: profit_factor >= 1.3 and max_drawdown <= 10%
secondary: long_bullish_pf, short_bullish_pf, avg_loss, trade_count
reject if: short_bullish_pf < 0.8 and short_bullish_trade_count >= 5
reject if: total_trade_count < 30 in validation window
```

### 5.3 第三阶段：live 小步放行

只有当 shadow + 回测同时通过，才改 live 行为：

1. 启用 `SHORT_REGIME_GUARD_ENABLED=true`。
2. 启用 `SIDE_REGIME_EV_ENABLED=true`。
3. 对 bullish long 允许 `RR_FLOOR_LONG_BULLISH=1.25`，但 `RR_LOW_POSITION_SCALE=true`。
4. 保持 `MAX_TRADE_AMOUNT=30`、`MAX_CONCURRENT_POSITIONS=3` 不变。
5. 连续 paper/testnet 至少 7 天，观察 long/short 分项 PF。

## 6. 建议参数首版

不建议直接上最大幅度。首版保守配置：

```dotenv
SIDE_REGIME_EV_ENABLED=true
SHORT_REGIME_GUARD_ENABLED=true
COUNTERFACTUAL_LEDGER_ENABLED=true

RR_FLOOR_DEFAULT=1.50
RR_FLOOR_LONG_BULLISH=1.30
RR_FLOOR_LONG_BULLISH_STRONG=1.20
RR_FLOOR_SHORT_BULLISH=1.80
RR_FLOOR_SHORT_BEARISH=1.30

LOW_RR_MAX_LEVERAGE=10
VERY_LOW_RR_MAX_LEVERAGE=5
SHORT_BULLISH_MAX_LEVERAGE=5

TP1_R_MULT=1.0
TP1_REDUCE_PCT=0.5
MOVE_SL_TO_BE_AFTER_TP1=true
```

对应 live 放行条件：

```text
long/bullish/strong:
  score >= 50
  htf_votes >= 2
  15m_confirm_long == true
  liquidity_score > 0
  effective_rr >= 1.2
  expected_value_side_regime > 0

short/bullish:
  score <= -70
  htf_votes_bearish >= 2
  daily_bias != bullish
  15m_confirm_short == true
  effective_rr >= 1.8
  expected_value_side_regime > 0
```

## 7. 验证矩阵

### 7.1 必跑本地测试

```bash
python3 -m pytest -q test_risk_budget.py test_ev_gate.py test_p2o_params.py
python3 -m pytest -q test_event_backtest.py test_event_backtest_real_data.py
python3 -m pytest -q test_p2p3_grid_search.py
python3 -m pytest -q test_paper_executor.py test_live_ledger.py
```

### 7.2 新增验收

| 验收项 | 通过标准 |
|---|---|
| side/regime EV | short 亏损不会降低 bullish long 的 p_win |
| short guard | bullish regime 中普通 short 只进 shadow，不进 live |
| low RR long | `RR=1.2-1.5` long 仅在强确认下放行，且仓位/杠杆缩放 |
| shadow ledger | 被拒 plan 能完整归因 TP/SL/expired |
| backtest 同构 | 线上 Judge 参数在 event_backtest 有对应参数 |
| paper 观察 | 7 天 long/short 分项 PF、胜率、平均亏损输出稳定 |

### 7.3 决策门槛

进入 live 小幅放行前，至少满足：

- shadow long/bullish 样本 >= 30，PF >= 1.3。
- short/bullish 样本 >= 10 时 PF < 0.8，则 live 禁 short/bullish。
- 回测 validation 窗口 `max_drawdown <= 10%`。
- Paper 7 天内没有 Daily Hard Stop。

## 8. 风险说明

1. 18 小时样本不足以证明长期收益，只能作为策略诊断线索。
2. “只做多”在单边上涨阶段有效，但 regime 反转时会变成系统性风险，所以必须配 regime gate。
3. 放宽 R:R 必须绑定仓位缩放和更积极的退出，否则只是扩大尾部亏损。
4. 被拒信号跟踪必须先做，否则下一次优化仍会依赖人工回溯，无法形成自动校准。

## 9. 推荐实施顺序

1. 做 `CounterfactualSignalLedger`，先不改 live。
2. 给 `ReviewerAgent` 或新工具补 `side/regime/archetype` 统计。
3. 在 `event_backtest.py` 加 side/regime 参数并跑 grid。
4. 上 `ShortRegimeGuard`，先只影响 short。
5. 在 bullish long strong 场景下小幅放宽 R:R 到 1.3，再观察；不要一开始放到 1.2。
6. 满足 7 天 paper/testnet 验收后，再考虑实盘放量。

