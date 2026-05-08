# 系统架构文档

## 概述

加密货币趋势交易系统，基于技术分析和合约交易，支持多AI Agent协作决策。

**重要变更**：
- 2026-05-06：原套利策略经全面验证不可行（0次机会），转向趋势交易+合约策略
- 2026-05-07：多Agent系统完成，两层架构（研判层6 Agent + 交易层6 Agent），含言官逆向审查机制
- 2026-05-07：P0风控增强完成（ReviewerAgent + Daily Hard Stop + Graceful Shutdown + 状态持久化）
- 2026-05-07：P1-A Telegram通知完成（TelegramNotifier，交易层7个Agent）
- 2026-05-08：contractSize修复（DOGE/ETH等非1合约单位正确计算），Judge杠杆上限10x

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
│         两层架构：研判层(12h) + 交易层(持续)                     │
└──────────┬───────────────────────────────────────────────────┘
           │ asyncio Queue 消息总线（支持 topic:symbol 路由）
           ▼
┌──────────────────────────────────────────────────────────────┐
│              研判层 Tier 1（每12小时运行，6个Agent）              │
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
│              交易层 Tier 2（持续运行，7个Agent）               │
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
  
- **RobustStrategy稳健策略**：带反欺骗机制的趋势跟踪策略
  - 4重入场确认：MA金叉 + RSI不超买 + 成交量确认 + 价格上涨
  - 2重出场保护：MA死叉 或 RSI超买
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
| `orchestrator.py` | 基础 | 两层编排器（研判12h周期+交易持续） | 无 |
| `research/market_scanner.py` | 研判 | OKX永续合约扫描（量/波动/费率/多空比/OI） | 无 |
| `research/sentiment_researcher.py` | 研判 | 恐贪指数+CoinGecko热度+Binance Taker比 | 无 |
| `research/news_researcher.py` | 研判 | 6家加密媒体RSS新闻采集+币种提及统计 | 无 |
| `research/synthesizer.py` | 研判 | Claude综合研判（两阶段：初选→终选） | Claude选币 |
| `research/censor.py` | 研判 | 言官逆向审查（Devil's Advocate） | Claude质疑 |
| `research/symbol_router.py` | 研判 | 标的路由+轮换协议（平仓旧标的） | 无 |
| `trading/multi_data_collector.py` | 交易 | 9维度数据采集（K线/orderbook/OI/爆仓/费率/Taker/大单/多空比） | 无 |
| `trading/tech_analyst.py` | 交易 | 9维度信号解读（趋势/价位/动量/资金流/微观结构/散户/风险） | Claude综合研判 |
| `trading/judge.py` | 交易 | 精确交易计划（入场区间/止盈止损/动态杠杆1-10x/仓位） | Claude最终裁决 |
| `trading/executor.py` | 交易 | 多标的交易执行 | 无 |
| `trading/portfolio_risk_guard.py` | 交易 | 组合级风控盯盘 | 无 |
| `trading/reviewer.py` | 交易 | 交易复盘+策略衰减+Daily Hard Stop触发 | 无 |
| `trading/telegram_notifier.py` | 交易 | Telegram实时告警+每日摘要 | 无 |

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
- Judge 精确交易计划：7维度加权评分 + 基于支撑阻力的止盈止损 + 动态杠杆1-10x + 反欺骗/反人性决策
- 反欺骗验证通过：诱多陷阱识别、恐慌底部反人性做多、假突破拒绝、杠杆过热拒绝、主力洗盘识别

**Phase 5d - P0风控增强（2026-05-07）**：
- ReviewerAgent：交易历史追踪 + 滚动窗口指标（胜率/盈亏比） + 策略衰减检测
- Daily Hard Stop：双重熔断（单日亏损≤-50 USDT 或 连续3次亏损）
- Graceful Shutdown：SIGINT/SIGTERM信号处理 + 状态保存 + 优雅停机
- RiskGuard状态持久化：持仓追踪/价格缓存/熔断状态重启恢复
- Executor/RiskGuard升级：动态杠杆+限价单+条件单 + risk_alert接入强制平仓

### Phase 6: 智能增强（下一阶段）
- Predictor（趋势预测Agent）
- Telegram实时告警
- Paper Trading模式
- Claude提示词持续优化
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
