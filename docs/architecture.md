# 系统架构文档

## 概述

加密货币趋势交易系统，基于技术分析和合约交易，支持多AI Agent协作决策。

**重要变更**：
- 2026-05-06：原套利策略经全面验证不可行（0次机会），转向趋势交易+合约策略
- 2026-05-07：多Agent系统完成，两层架构（研判层6 Agent + 交易层7 Agent），含言官逆向审查机制
- 2026-05-07：P0风控增强完成（ReviewerAgent + Daily Hard Stop + Graceful Shutdown + 状态持久化）
- 2026-05-07：P1-A Telegram通知完成（TelegramNotifier，交易层7个Agent）
- 2026-05-08：contractSize修复（DOGE/ETH等非1合约单位正确计算），Judge杠杆上限20x
- 2026-05-08：方向决策修复（_compute_score重写：RSI极端值保护+趋势强度衰减+条件化散户反指）
- 2026-05-09：post-mortem修复（correlation_risk用保证金计算、Judge force_close冷却300s）
- 2026-05-09：入场质量优化（R:R门槛≥1.5、负面催化剂否决、30min新闻轮询+price-in检测）
- 2026-05-09：日线多周期升级（1d K线采集、日线趋势/价位/反欺骗、多周期共振1h+4h+1d、标的限制放开）
- 2026-05-09：Judge主驱动修复（rule_signal±35基础分、LLM降为修正因子不再一票否决）
- 2026-05-09：做空信号修复（RobustStrategy新增entry_short：MA死叉+RSI不超卖+放量+价格下跌）
- 2026-05-09：日线阻力区阈值收紧（3%→1.5%，减少横盘误触发）
- 2026-05-09：PROS-USDT ticker格式修复（_fetch_price_tick统一用/USDT:USDT格式）
- 2026-05-11：MA alignment信号（tech_analyst.py+judge.py）：ma_aligned_long/short给±20分，解决crossover后系统永远hold
- 2026-05-11：Symbol sync修复（executor.py）：OKX格式BASE/USDT:USDT自动转换为BASE-USDT-SWAP
- 2026-05-13：持仓管理防遗憾优化（position_analyst.py）：7因子评分+entry_thesis_intact+2h周期+阈值放宽
- 2026-05-13：R:R硬性门槛修复（judge.py）：min_rr=1.5不可绕过 + SL距离ATR封顶(2.5×ATR) + TP下限=SL×1.5
- 2026-05-14：Judge LLM-Rule方向冲突修复（judge.py）：confidence提升需方向一致 + LLM反向衰减50% + rule_signal+LLM反向衰减60% + RSI禁区inclusive(>=70/<=30)
- 2026-05-14：PositionAnalyst规则3b（position_analyst.py）：浮亏>10%+趋势非顺向→强制平仓
- 2026-05-14：llm_client.py chat_json支持temperature参数传递
- 2026-05-14：统一风险预算框架（judge.py）：杠杆由风险约束推导 `leverage = 0.5/sl_dist`，删除旧`_calc_leverage`+`_calc_size`，新增`_calc_risk_budget`统一函数，effective_rr含资金费率+手续费
- 2026-05-14：回调入场机制（judge.py）：R:R<1.5分级响应（追价/等回调/放弃），deferred_entry状态机
- 2026-05-14：Censor分批审查（censor.py）：BATCH_SIZE=4避免Cloudflare超时，LLM timeout=90s+max_retries=2
- 2026-05-14：Executor required_margin修复（executor.py）：size_usdt即margin，不再除以leverage
- 2026-05-15：HYPE重复做空5层防护（judge.py）：RSI背离HTF降权+入场门槛40+LLM conf cap 55+开仓冷却300s+失败冷却120s
- 2026-05-15：SL/TP方向校验（executor.py）：下单前验证方向合法性，价格变动导致方向错误时自动修正
- 2026-05-15：PositionAnalyst评估周期2h→1h（position_analyst.py）
- 2026-05-15：加仓/减仓功能修复（executor.py+agents/trading/executor.py）：加仓(add_to_position加权均价+SL/TP重算+上限2x)+减仓(reduce_position精度+取消旧SL)+全系统execution_result同步(is_add/risk_reduced)
- 2026-05-15：PA Rule 1/3b动态阈值（position_analyst.py）：Rule 1=SL含杠杆距离（第三道防线），Rule 3b=SL距离×50%（替代固定15%/10%）
- 2026-05-15：Executor close冷却60s（executor.py）：平仓后60s内sync_positions不重新发现该标的（防API延迟导致幽灵持仓）
- 2026-05-15：Telegram去重（telegram_notifier.py）：sync发现的持仓不推送开仓通知 + 同symbol平仓通知60s去重
- 2026-05-15：Symbol格式统一修复（judge.py+position_analyst.py+portfolio_risk_guard.py）：execution_result handler strip `-SWAP`后缀 + deferred_entry触发即时冷却，解决ZEC重复开仓+SL覆盖+PA幽灵持仓三个级联故障
- 2026-05-17：closed_externally PnL追踪（executor.py）：sync_positions保存被移除持仓数据，_estimate_close_pnl优先用unrealized_pnl（~30s误差），降级用SL价格计算。Daily Hard Stop现在能检测交易所SL触发的真实亏损
- 2026-05-17：递增冷却StoplossGuard（judge.py）：4h窗口内连续SL次数递增冷却（300→600→1200→3600s），窗口过期自动重置。参考Freqtrade StoplossGuard protection
- 2026-05-17：研判层上线时间过滤（market_scanner.py）：OKX月K线<12根的标的不进入初选（上线不足1年），enrich前并行检查节省API调用
- 2026-05-17：初选固定12标的（synthesizer.py）：LLM固定选12个标的（原5-12个浮动）
- 2026-05-17：Telegram启动flush旧消息（telegram_notifier.py）：_flush_old_updates()跳过所有pending消息，防止历史/stop命令杀掉新启动的进程
- 2026-05-17：终选prompt优化（synthesizer.py）：明确区分reject（移除）和warning（保留降置信度），代码保底防LLM过度收窄
- 2026-05-17：Logger防重复（utils/logger.py）：propagate=False + handler去重，解决每条日志打印7次的问题

## 架构图

### 单策略模式（live_trading.py）

```
┌─────────────────────────────────────────┐
│      K线数据采集 (WebSocket/REST)        │
│      kline_collector.py                 │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│      技术指标计算                         │
│      indicators.py                      │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│      策略系统                             │
│      optimize_1h.py (RobustStrategy)    │
│      - 4重入场确认、信号生成              │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│      实时交易系统                         │
│      live_trading.py                    │
│      ├─ 策略分析                         │
│      ├─ 风控检查 (risk_manager.py)       │
│      └─ 交易执行 (executor.py)           │
└─────────────────────────────────────────┘
```

### 多Agent模式（run_agents.py）

```
┌──────────────────────────────────────────────────────────────┐
│                  Orchestrator（编排器）                         │
│         两层架构：研判层(4h) + 交易层(持续)                      │
└──────────┬───────────────────────────────────────────────────┘
           │ asyncio Queue 消息总线（支持 topic:symbol 路由）
           ▼
┌──────────────────────────────────────────────────────────────┐
│              研判层 Tier 1（每4小时运行，6个Agent）               │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │MarketScanner │  │ Sentiment    │  │    News      │       │
│  │OKX合约扫描   │  │恐贪+热度+Taker│  │ RSS新闻采集  │       │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘       │
│         └──────────────────┼─────────────────┘               │
│                            ▼                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ Synthesizer  │←→│   Censor     │  │ SymbolRouter │       │
│  │Claude综合研判 │  │言官逆向审查   │  │标的路由+轮换  │       │
│  └──────────────┘  └──────────────┘  └──────┬───────┘       │
└─────────────────────────────────────────────┼───────────────┘
                                              │ symbol_update
┌─────────────────────────────────────────────┼───────────────┐
│              交易层 Tier 2（持续运行，9个Agent）               │
│                                              ▼               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │DataCollector  │  │ TechAnalyst  │  │    Judge     │       │
│  │多标的数据采集 │  │多标的技术分析 │  │多标的裁判决策 │       │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘       │
│         │                  │                  │               │
│  ┌──────┴───────┐  ┌──────┴───────┐                         │
│  │  Executor    │  │PortfolioRisk │                         │
│  │  多标的执行   │  │ 组合风控盯盘  │                         │
│  └──────────────┘  └──────────────┘                         │
│                                                              │
│  ┌──────────────┐                                           │
│  │  Reviewer    │                                           │
│  │ 交易复盘+熔断 │                                           │
│  └──────────────┘                                           │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐                         │
│  │PositionAnalyst│  │BehavioralCritic│                       │
│  │持仓分析+裁决  │  │行为偏差检测    │                         │
│  └──────────────┘  └──────────────┘                         │
└──────────────────────────────────────────────────────────────┘
```

### 研判层决策流水线（两阶段）

```
MarketScanner ─────┐
SentimentResearcher─┼─→ Synthesizer(初选) → Censor(谏言) → Synthesizer(终选)
NewsResearcher ────┘                                            │
                                                                ▼
                                                         SymbolRouter
                                                                │
                                                         symbol_update
                                                                │
                                    ┌───────────────────────────┼──────┐
                                    ▼               ▼           ▼      ▼
                              DataCollector   TechAnalyst    Judge  RiskGuard
```

### 交易层决策流水线（per-symbol）

```
DataCollector
    │ [market_data:SOL-USDT]
    ▼
TechAnalyst（规则引擎 + Claude分析）
    │ [tech_analysis:SOL-USDT]
    ▼
Judge（Claude裁判 / 规则降级）
    │ [trade_decision:SOL-USDT]
    ▼
Executor（风控审核 → 执行）
    │ [execution_result:SOL-USDT]
    ▼
PortfolioRiskGuard（组合级实时监控）
```

### 持仓管理决策流水线（每1小时）

```
PositionAnalyst（7因子规则评分）
    │ [position_review:SOL-USDT]
    ▼
BehavioralCritic（LLM偏差检测 / 规则降级）
    │ [position_verdict:SOL-USDT]
    ▼
PositionAnalyst 裁决引擎（纯规则矩阵）
    │ 硬性覆盖 > 裁决矩阵 > 分析官建议
    ▼
[trade_decision:SOL-USDT] → Executor执行
```

**7因子评分**：趋势对齐(±20) + 动量变化(±20) + 时间衰减(-15~0) + 浮盈状态(±20) + 成交量确认(±10) + 剩余R:R(±15) + 入场逻辑验证(-10~+25)

**硬性覆盖规则**（无视分析官和批判官）：
- 规则1：浮亏超过SL含杠杆距离 → close（第三道防线：交易所SL→Executor 5s轮询→PA 1h周期）
- 规则2：持仓>72h+浮亏>3% → close
- 规则3：HTF趋势反转+浮亏>5% → close
- 规则3b：浮亏超过SL距离×50%+趋势非顺向 → close（入场逻辑失效早期信号）
- 规则4：浮盈>15%+动量反转 → reduce 50%
- 规则5：剩余R:R<0.3 → close

**三层止损防线**：交易所SL条件单(实时) → Executor本地5s轮询 → PA规则1(1h周期，兜底)

**防遗憾机制**：高时间框架（4h/日线）仍确认入场方向时，裁决引擎保护持仓（批判官close→reduce，reduce→hold）

**加仓/减仓执行**（2026-05-15修复）：
- 加仓：score≥50 + conviction≥70 + 保证金<上限(max_trade_amount×2) → Executor.add_to_position（加权平均入场价 + SL/TP按原距离比例重算）
- 减仓：score∈[-60,-30) 或 硬性规则4 → Executor.reduce_position（取消旧SL条件单 + 精度格式化 + 浮点兜底）
- execution_result区分：新开仓(executed) / 加仓(executed+is_add) / 减仓(risk_reduced+reduce_pct) / 全平(executed+close)
- 下游同步：RiskGuard/PositionAnalyst/TelegramNotifier均正确处理增量更新

**执行优先级**：RiskGuard强制平仓 > 硬性覆盖 > 裁决矩阵 > 分析官建议

## 核心模块

### 1. K线数据采集器 (kline_collector.py) ✅

**职责**：实时采集K线数据并存储

**实现**：
- Binance WebSocket订阅
- 支持多币种、多周期
- SQLite存储

**数据流**：
```python
WebSocket stream
  → kline{open, high, low, close, volume}
  → database.insert_kline()
  → klines表
```

**表结构**：
```sql
CREATE TABLE klines (
    symbol TEXT,
    interval TEXT,
    open_time INTEGER,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    close_time INTEGER,
    UNIQUE(symbol, interval, open_time)
);
```

### 2. 技术指标计算 (indicators.py) ✅

**职责**：基于K线数据计算技术指标

**已实现**：
- MA（移动平均线）- 支持任意周期
- EMA（指数移动平均线）
- MACD（指数平滑异同移动平均线）- 返回MACD线、信号线、柱状图
- RSI（相对强弱指标）- 14周期默认
- 布林带 - 上轨、中轨、下轨

**实现方式**：
- 使用pandas向量化操作，高效计算
- 静态方法设计，易于复用
- 支持自定义参数

### 3. 策略系统 (strategy_base.py + optimize_1h.py) ✅

**职责**：基于技术指标生成交易信号

**已实现**：
- **StrategyBase基类**：参考Freqtrade架构，三步式策略设计
  - populate_indicators()：计算指标
  - populate_entry_signals()：入场信号
  - populate_exit_signals()：出场信号
  
- **RobustStrategy稳健策略**：带反欺骗机制的趋势跟踪策略，支持多空双向
  - 做多4重确认：MA金叉 + RSI不超买(<75) + 成交量确认 + 价格上涨
  - 做空4重确认：MA死叉 + RSI不超卖(>25) + 成交量确认 + 价格下跌
  - 做多出场：MA死叉 或 RSI超买(>80)
  - 做空出场：MA金叉 或 RSI超卖(<20)
  - 最佳参数：MA 7/25，RSI阈值75，成交量因子1.0
  - 验证结果：83.3%胜率，7.68盈亏比

### 4. 回测引擎 (backtest.py) ✅

**职责**：历史数据回测验证策略

**已实现**：
- 防前视偏差：信号在第i根K线产生，在第i+1根K线开盘价执行
- 手续费计算：每笔交易扣除0.1%手续费
- 完整绩效指标：
  - 总交易次数、盈利/亏损交易数
  - 胜率、盈亏比
  - 总收益、平均收益
  - 最大回撤
- 交易详情记录：每笔交易的入场/出场价格和收益

**验证结果**：
- 多时间周期测试：1小时周期最优（46.67%胜率）
- 参数优化：找到最佳参数组合（MA 7/25，RSI 75）
- 样本外验证：测试集100%胜率，策略稳健

### 5. 合约执行器 (executor.py) ✅

**职责**：执行合约交易

**已实现**：
- 基于CCXT的统一交易接口（Binance/OKX）
- 杠杆设置、开仓/平仓、止损止盈检查
- 持仓持久化（`data/positions.json`）
- reduceOnly参数、盈亏计算含杠杆

### 6. 风控管理器 (risk_manager.py) ✅

**职责**：风险控制

**已实现**：
- 余额/回撤/每日亏损限制
- 止损止盈计算（多空双向）
- 峰值余额持久化（`data/risk_state.json`）

**硬限制**：
- 单次最大交易额：10 USDT
- 最大回撤：20%
- 每日最大亏损：50 USDT

### 7. 多Agent系统 (agents/) ✅

**职责**：两层Agent协作决策——研判层选标的，交易层执行

**核心组件**：

| 文件 | 层级 | 职责 | LLM使用 |
|------|------|------|---------|
| `base.py` | 基础 | Agent基类（生命周期、消息收发） | 提供ask_claude接口 |
| `message_bus.py` | 基础 | asyncio Queue消息总线（支持topic:symbol路由） | 无 |
| `llm_client.py` | 基础 | Claude API客户端（OpenAI兼容格式） | 核心 |
| `orchestrator.py` | 基础 | 两层编排器（研判4h周期+交易持续） | 无 |
| `research/market_scanner.py` | 研判 | OKX永续合约扫描（量/波动/费率/多空比/OI） | 无 |
| `research/sentiment_researcher.py` | 研判 | 恐贪指数+CoinGecko热度+Binance Taker比 | 无 |
| `research/news_researcher.py` | 研判 | 6家加密媒体RSS新闻采集+币种提及统计 | 无 |
| `research/synthesizer.py` | 研判 | Claude综合研判（两阶段：初选→终选） | Claude选币 |
| `research/censor.py` | 研判 | 言官逆向审查（Devil's Advocate） | Claude质疑 |
| `research/symbol_router.py` | 研判 | 标的路由+轮换协议（平仓旧标的） | 无 |
| `trading/multi_data_collector.py` | 交易 | 9维度数据采集（K线/orderbook/OI/爆仓/费率/Taker/大单/多空比） | 无 |
| `trading/tech_analyst.py` | 交易 | 9维度信号解读（趋势/价位/动量/资金流/微观结构/散户/风险） | Claude综合研判 |
| `trading/judge.py` | 交易 | 精确交易计划（统一风险预算/入场区间/止盈止损/动态杠杆1-20x/仓位/RSI极端值保护/回调入场） | Claude最终裁决 |
| `trading/executor.py` | 交易 | 多标的交易执行 | 无 |
| `trading/portfolio_risk_guard.py` | 交易 | 组合级风控盯盘 | 无 |
| `trading/reviewer.py` | 交易 | 交易复盘+策略衰减+Daily Hard Stop触发 | 无 |
| `trading/telegram_notifier.py` | 交易 | Telegram实时告警+每日摘要 | 无 |
| `trading/position_analyst.py` | 交易 | 持仓7因子评分+裁决引擎（每1h） | 无 |
| `trading/behavioral_critic.py` | 交易 | 行为金融学偏差检测（7种认知偏差） | Claude检测偏差 |

**LLM降级机制**：Claude不可用时自动回退到规则引擎，系统不中断。

**研判层消息类型**：
- `research_trigger`：编排器触发研判（Orchestrator → 研判层）
- `research_market_data`：市场扫描结果（MarketScanner → Synthesizer）
- `research_sentiment_data`：情绪数据（SentimentResearcher → Synthesizer）
- `research_news_data`：新闻数据（NewsResearcher → Synthesizer）
- `research_preliminary`：初选结果（Synthesizer → Censor）
- `research_challenge`：言官谏言（Censor → Synthesizer）
- `research_result`：终选结果（Synthesizer → SymbolRouter）
- `symbol_update`：活跃标的更新（SymbolRouter → 交易层全体）

**交易层消息类型**：
- `market_data:{symbol}`：9维度数据（K线+orderbook+OI+爆仓+费率历史+Taker比+大单+多空比）（DataCollector → TechAnalyst, RiskGuard）
- `price_tick:{symbol}`：10秒价格流（DataCollector → RiskGuard）
- `tech_analysis:{symbol}`：9维度信号解读（趋势/价位/动量/资金流/微观结构/散户/风险）（TechAnalyst → Judge）
- `trade_decision:{symbol}`：精确交易计划（入场区间/止盈止损/杠杆/仓位）（Judge → Executor）
- `execution_result:{symbol}`：执行结果（Executor → RiskGuard, Reviewer, TelegramNotifier）
- `risk_alert`：风控警报（RiskGuard → broadcast，Executor + TelegramNotifier响应）
- `daily_hard_stop_triggered`：熔断信号（Reviewer → broadcast，Executor + RiskGuard + TelegramNotifier响应）
- `strategy_review`：策略复盘报告（Reviewer → TelegramNotifier）
- `news_snapshot`：30min新闻快照（DataCollector → Judge，用于price-in检测）
- `position_review:{symbol}`：持仓评估结果（PositionAnalyst → BehavioralCritic）
- `position_verdict:{symbol}`：偏差检测结果（BehavioralCritic → PositionAnalyst裁决引擎）

**Symbol格式约定**：
- 消息总线（DataCollector/TechAnalyst/Judge/PA/RiskGuard）：`ZEC-USDT`（不带-SWAP）
- ContractExecutor positions dict key：`ZEC-USDT-SWAP`（`_normalize_symbol`自动添加）
- OKX API返回：`ZEC/USDT:USDT`（sync_positions自动转换为`-SWAP`格式）
- 规则：Agent层收到execution_result时strip `-SWAP`后缀，确保与tech_analysis key一致

## 数据流

### K线采集流程（已实现）

```
1. kline_collector.py 启动WebSocket连接
2. 订阅币种K线流（如BTCUSDT@kline_1m）
3. 接收K线数据
4. K线闭合时存储到数据库
5. 持续监听
```

### 交易执行流程

**单策略模式（live_trading.py）**：
```
1. 从交易所获取最新K线（降级：数据库）
2. 技术指标计算 + 信号生成
3. 风控检查（余额、回撤、每日亏损）
4. 执行合约开仓/平仓
5. 止损止盈监控
6. 60秒后重复
```

**多Agent模式（run_agents.py）**：
```
1. DataCollector 9维度采集（10s价格/30s深度+爆仓/60s全量/5min 4h K线）
2. TechAnalyst 收到数据后：规则引擎解读9维度 + Claude综合研判
3. Judge 收到分析后：信号聚合评分 + Claude裁决 → 精确交易计划（入场/止盈止损/杠杆/仓位）
   - R:R≥1.5 → 正常入场
   - 1.2≤R:R<1.5 + 强信号(|score|≥50) → 追价入场（缩仓）
   - 1.2≤R:R<1.5 + 弱信号 → deferred_entry等回调（3h有效）
   - R:R<1.2 → 放弃
4. Executor 收到决策后：风控审核 → 执行交易
5. RiskGuard 持续监控：闪崩检测、敞口超限
```

## 配置系统

**config.yaml**：
- 交易所配置
- 交易对列表
- 技术指标参数
- 风控参数

**.env**：
- API密钥
- 敏感配置

## 日志系统

**位置**：`logs/`
**格式**：`{module}_{YYYYMMDD}.log`
**级别**：INFO

**关键日志**：
- K线数据采集
- 技术指标计算
- 交易信号生成
- 交易执行结果
- 错误和异常

## MVP开发路线

### Phase 1: 数据基础 ✅ (2026-05-06完成)
- K线数据采集器
- SQLite存储

### Phase 2: 技术分析 ✅ (2026-05-06完成)
- 技术指标计算（indicators.py）
- 策略基类设计（strategy_base.py）
- 稳健策略实现（optimize_1h.py）

### Phase 3: 回测验证 ✅ (2026-05-06完成)
- 回测引擎（backtest.py）
- 多时间周期测试（compare_timeframes.py）
- 参数优化（optimize_1h.py）
- 样本外验证（validate_out_of_sample.py）

**关键发现**：
- 1小时周期最优，1分钟/15分钟不盈利
- 反欺骗机制使胜率从46.67%提升至83.3%
- 最佳参数：MA 7/25，RSI阈值75，成交量因子1.0
- 样本外验证通过，策略稳健

### Phase 4: 实盘交易系统 ✅ (2026-05-06完成)
- 合约执行器（executor.py）
- 风控管理器（risk_manager.py）
- 实时交易系统（live_trading.py）
- OKX真实账户验证通过

### Phase 5: 多Agent系统 ✅ (2026-05-07完成)

**Phase 5a - 基础框架**：
- 消息总线（asyncio Queue，支持topic:symbol路由）
- Agent基类 + 编排器
- Claude API客户端（OpenAI兼容格式，中转站支持）
- 5个核心交易Agent：DataCollector、TechAnalyst、Judge、Executor、RiskGuard
- LLM降级机制

**Phase 5b - 两层研判架构**：
- 研判层6个Agent：MarketScanner、SentimentResearcher、NewsResearcher、Synthesizer、Censor、SymbolRouter
- 交易层5个Agent升级为多标的并行处理
- 两阶段决策：Synthesizer初选 → Censor谏言 → Synthesizer终选
- 标的轮换协议（旧标的平仓、新标的接入）
- 数据源：OKX 324合约扫描 + 恐贪指数 + CoinGecko热度 + Binance Taker比 + 6家RSS新闻

**Phase 5c - 交易层深度升级（2026-05-07）**：
- DataCollector 9维度采集：K线(多周期) + Orderbook 20档 + 资金费率历史 + OI delta + 爆仓订单 + Taker买卖比 + 大单检测 + 多空账户比 + 实时价格流
- TechAnalyst 9维度信号解读：趋势结构 + 关键价位(含orderbook墙) + 动量(RSI背离) + 资金流向(OI背离/费率极值) + 微观结构(鲸鱼/深度偏向) + 散户反指 + 风险评估
- Judge 精确交易计划：7维度加权评分 + 基于支撑阻力的止盈止损 + 动态杠杆1-20x + RSI极端值保护 + 反欺骗/反人性决策
- 反欺骗验证通过：诱多陷阱识别、恐慌底部反人性做多、假突破拒绝、杠杆过热拒绝、主力洗盘识别

**Phase 5d - P0风控增强（2026-05-07）**：
- ReviewerAgent：交易历史追踪 + 滚动窗口指标（胜率/盈亏比） + 策略衰减检测
- Daily Hard Stop：双重熔断（单日亏损≤-50 USDT 或 连续3次亏损）
- Graceful Shutdown：SIGINT/SIGTERM信号处理 + 状态保存 + 优雅停机
- RiskGuard状态持久化：持仓追踪/价格缓存/熔断状态重启恢复
- Executor/RiskGuard升级：动态杠杆+限价单+条件单 + risk_alert接入强制平仓

**Phase 6 - 智能增强（2026-05-07~08）**：
- P1-A Telegram通知：TelegramNotifier实时推送+每日摘要+零配置降级
- contractSize修复：`amount = (size_usdt * leverage) / (price * contract_size)` + `amount_to_precision()`
- 方向决策修复（2026-05-08）：_compute_score重写，RSI极端值硬性保护+趋势强度衰减+条件化散户反指+RSI背离权重提升
- 系统逻辑校验：止损最小距离1.5%、组合回撤用保证金计算、止盈orderbook墙逻辑修复

**Phase 6e - 入场质量优化（2026-05-09）**：
- Post-mortem修复：correlation_risk改用保证金计算（非名义价值），Judge force_close冷却300s
- R:R门槛：`risk_reward_ratio < 1.5` → hold（参考Freqtrade minimal_roi原则）
- 负面催化剂否决：synthesizer近4h新闻关键词检测 → confidence=0 → censor reject（veto层设计）
- 30min新闻轮询：DataCollector新增`_tick_news()`，发布`news_snapshot`消息到交易层
- price-in检测：Judge订阅`news_snapshot`，近4h有新闻+价格移动>3% → score×0.5

**Phase 6f - 日线多周期升级（2026-05-09）**：
- DataCollector：`_collect_1d()` 每慢周期采集30根日线K线，payload新增`klines_1d`
- TechAnalyst：`_analyze_trend()` 新增日线偏向+`daily_near_resistance/support`检测（距20日高低点**1.5%**以内）
- TechAnalyst：多周期共振投票（1h+4h+1d三周期一致+20强度，矛盾-20）；4h RSI计算
- TechAnalyst：`_analyze_levels()` 新增日线swing支撑阻力（更可靠的止损止盈锚点）
- Judge：接近日线阻力区（1.5%以内）做多信号衰减70%（防假突破）；接近日线支撑区（1.5%以内）做空信号衰减70%（防反弹陷阱）
- Judge：止损优先用日线价位锚点（daily_support/daily_resistance）
- Synthesizer：放开标的限制（含XAU/CL等非加密标的），波动率范围扩至50%，成交量门槛降至$30M
- MarketScanner：并发enrichment（asyncio.gather替代串行循环）

**Phase 6g - Judge主驱动修复（2026-05-09）**：
- 根因：rule_signal（回测83%胜率的MA交叉信号）未参与评分，系统永远hold
- 修复：rule_signal触发时给±35基础分，确保过30分入场门槛
- LLM从一票否决改为仓位修正：rule_signal触发时LLM最多降30%仓位，不能阻止入场
- 无rule_signal时保持原有保守逻辑（LLM可否决弱信号）

**Phase 6h - MA alignment信号 + Symbol sync修复（2026-05-11）**：
- 根因：MA crossover是点事件，crossover后下一根K线entry_short=0，score≈0，系统永远hold
- 修复：tech_analyst.py新增`ma_aligned_long/short`（MA fast/slow已对齐≥3根K线），judge.py给±20基础分作为次驱动
- Symbol sync修复：executor.py sync_positions将OKX格式`BASE/USDT:USDT`自动转换为内部格式`BASE-USDT-SWAP`，防止每次sync循环删除并重建持仓
- 首次成功开仓：LAYER-USDT short @ 0.12171，3x杠杆
- 止损止盈计算修复（2026-05-13）：SL距离ATR封顶（2.5倍ATR，max 5%，Turtle Traders方法论）；TP下限=SL×1.5（保证R:R≥1.5）；R:R硬性门槛1.5（不因confidence高而放松）。根因：旧公式用confidence动态计算min_rr，LLM提升confidence到65时min_rr降至0.538，R:R=0.6即可通过

**Phase 6i - 持仓管理三角决策 + flash_move修复（2026-05-12）**：
- PositionAnalyst：6因子规则评分（趋势对齐/动量变化/时间衰减/浮盈状态/成交量确认/剩余R:R），每30min评估所有持仓
- BehavioralCritic：LLM检测7种认知偏差（loss_aversion/sunk_cost/anchoring/fomo/disposition/overconfidence/panic），LLM不可用时规则降级
- 裁决引擎（内嵌PositionAnalyst）：5条硬性覆盖规则 + 4级severity裁决矩阵（none/low/medium/high）
- flash_move修复：从全平所有持仓改为只平触发标的（单币闪崩≠系统性风险）
- Synthesizer扩容：初选上限3→12，增加机会面供Censor筛选
- 持仓监控补充：DataCollector自动将持仓标的纳入监控（即使不在SymbolRouter活跃列表）
- 交易层Agent数量：7→9（新增PositionAnalyst + BehavioralCritic）

### Phase 7: 待开发
- 资金费率API修复（`fetchFundingRate() is only valid for swap markets`）
- Predictor（趋势预测Agent）
- Paper Trading模式
- 更多数据源接入（链上大额转账、清算数据）

## 性能考虑

- **实时数据**：WebSocket推送，延迟<100ms
- **数据库**：SQLite适合单机MVP，后期可升级PostgreSQL
- **计算效率**：技术指标计算使用pandas向量化操作

## 安全考虑

- API密钥存储在.env，不提交代码库
- 风控硬限制：单次10 USDT，回撤20%
- 只读模式：无API密钥时仅采集数据

## 套利系统归档说明

原套利系统代码保留在以下文件中作为参考：
- `core/aggregator.py` - 行情聚合器
- `core/detector.py` - 套利检测引擎
- `depth_validator.py` - 深度验证器
- `market_scanner.py` - 市场扫描器
- `websocket_monitor.py` - WebSocket监控
- `triangular_arbitrage.py` - 三角套利

**放弃原因**：2026-05-06全面验证，所有测试0次机会，市场效率极高，成本>收益。
