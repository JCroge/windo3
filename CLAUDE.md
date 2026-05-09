# Crypto Trading System - AI协作指南

## 项目概述

**目标**：加密货币趋势交易系统，基于技术分析和合约交易
**策略**：趋势跟踪 + 技术指标信号 + 反欺骗机制
**开发周期**：2026-05-06开始，MVP核心已完成

**重要变更（2026-05-06）**：
- 原方向：跨交易所套利
- 验证结果：所有测试0次机会，成本>收益
- 新方向：趋势交易 + 合约（更适合当前市场）
- MVP核心完成：技术指标、策略系统、回测引擎、样本外验证

**当前最佳策略参数（2026-05-06验证）**：
- 时间周期：1小时（1分钟/15分钟不盈利）
- MA快线：7，MA慢线：25
- RSI阈值：75
- 成交量因子：1.0
- 胜率：83.3%（训练集），100%（测试集）
- 盈亏比：7.68

## 项目结构

```
crypto-arbitrage/
├── core/                  # 套利系统（已归档）
│   ├── aggregator.py      # 行情聚合器
│   └── detector.py        # 套利发现引擎
├── utils/
│   ├── database.py        # SQLite数据库
│   └── logger.py          # 日志系统
├── indicators.py          # ✅ 技术指标计算
├── strategy_base.py       # ✅ 策略基类（Freqtrade架构）
├── strategy_trend.py      # ✅ 基础趋势策略
├── optimize_1h.py         # ✅ 稳健策略+参数优化
├── backtest.py            # ✅ 回测引擎
├── compare_timeframes.py  # ✅ 多时间周期对比
├── validate_out_of_sample.py  # ✅ 样本外验证
├── risk_manager.py        # ✅ 风控管理器
├── executor.py            # ✅ 合约执行器
├── live_trading.py        # ✅ 实时交易系统
├── test_backtest.py       # ✅ 回测测试
├── verify_system.py       # ✅ 系统完整性验证
├── verify_trading_flow.py # ✅ 交易Flow验证
├── verify_okx_real.py     # ✅ OKX真实账户验证
├── kline_collector.py     # ✅ K线数据采集器
├── data/
│   ├── market.db          # 套利数据（已归档）
│   ├── klines.db          # ✅ K线数据
│   ├── risk_state.json    # ✅ 风控状态持久化
│   ├── positions.json     # ✅ 持仓记录持久化
│   ├── trade_history.json # ✅ 交易历史持久化（ReviewerAgent）
│   └── riskguard_state.json # ✅ RiskGuard状态持久化
├── agents/                # ✅ 多Agent交易系统（两层架构）
│   ├── base.py            # Agent基类（生命周期、消息收发、LLM调用）
│   ├── orchestrator.py    # 编排器（两层：研判12h + 交易持续）
│   ├── message_bus.py     # 消息总线（asyncio Queue，支持topic:symbol路由）
│   ├── llm_client.py      # Claude API客户端（中转API支持）
│   ├── research/          # 研判层（6个Agent）
│   │   ├── market_scanner.py       # OKX永续合约扫描（量/波动/费率/多空比/OI）
│   │   ├── sentiment_researcher.py # 恐贪指数+CoinGecko热度+Taker比
│   │   ├── news_researcher.py      # 6家加密媒体RSS新闻
│   │   ├── synthesizer.py          # Claude综合研判（两阶段决策）
│   │   ├── censor.py               # 言官逆向审查（Devil's Advocate）
│   │   └── symbol_router.py        # 标的路由+轮换协议
│   └── trading/           # 交易层（7个Agent，多标的并行）
│       ├── multi_data_collector.py  # 9维度数据采集（K线/orderbook/OI/爆仓/费率/Taker/大单/多空比）
│       ├── tech_analyst.py          # 9维度信号解读（趋势/价位/动量/资金流/微观结构/散户/风险）
│       ├── judge.py                 # 精确交易计划（入场区间/止盈止损/动态杠杆1-20x/仓位）
│       ├── executor.py              # 多标的交易执行 + Daily Hard Stop响应
│       ├── portfolio_risk_guard.py  # 组合级风控盯盘 + 状态持久化
│       ├── reviewer.py              # 交易复盘 + 策略衰减检测 + Daily Hard Stop触发
│       └── telegram_notifier.py     # Telegram实时告警 + 每日摘要
├── run_agents.py          # ✅ 多Agent系统启动入口
├── test_p0_features.py    # ✅ P0功能测试（Reviewer/Hard Stop/Graceful Shutdown）
├── docs/                  # 文档
│   ├── architecture.md
│   ├── handoff.md
│   ├── integration-guide.md
│   └── runbook.md
├── logs/                  # 日志文件
├── ISSUES.md              # ✅ 问题清单（11/12已修复）
├── config.yaml            # 系统配置
├── .env                   # API密钥（不提交）
└── main.py                # 主程序入口
```

## 核心约束

### 风控参数（不可突破）
- 单次最大交易额：10 USDT
- 最大回撤：20%
- 每日最大亏损：50 USDT

### 交易所
- 主要：Binance + OKX
- 初始交易对：ETH/USDT
- 手续费：0.1%（Maker/Taker）

### 套利阈值
- 最小利润率：0.3%（扣除手续费后）
- 检查间隔：1秒

## 环境变量

| 变量名 | 说明 | 必需 |
|--------|------|------|
| EXCHANGE | 交易所选择（binance/okx） | 是 |
| BINANCE_API_KEY | Binance API密钥 | 否（使用Binance时必需） |
| BINANCE_SECRET | Binance Secret | 否（使用Binance时必需） |
| OKX_API_KEY | OKX API密钥 | 否（使用OKX时必需） |
| OKX_SECRET | OKX Secret | 否（使用OKX时必需） |
| OKX_PASSWORD | OKX密码（Passphrase） | 否（使用OKX时必需） |
| USE_TESTNET | 是否使用测试网（true/false） | 否（默认false） |
| LEVERAGE | 杠杆倍数 | 否（默认1） |
| MAX_TRADE_AMOUNT | 单次最大交易额 | 否（默认10） |
| MAX_DRAWDOWN | 最大回撤 | 否（默认0.20） |
| ANTHROPIC_API_KEY | Claude API密钥 | 否（使用多Agent系统时必需） |
| ANTHROPIC_BASE_URL | Claude API地址（支持中转） | 否（默认api.anthropic.com） |
| ANTHROPIC_MODEL | Claude模型名 | 否（默认claude-sonnet-4-6） |
| RESEARCH_INTERVAL | 研判层运行周期（秒） | 否（默认43200=12h） |
| MAX_ACTIVE_SYMBOLS | 最大同时交易标的数 | 否（默认3） |
| MAX_ACTIVE_SYMBOLS | 最大同时交易标的数 | 否（默认3） |
| TELEGRAM_BOT_TOKEN | Telegram Bot Token | 否（留空则不启用通知） |
| TELEGRAM_CHAT_ID | Telegram Chat ID | 否（留空则不启用通知） |

## 开发阶段

### ✅ Phase 1: 数据基础（2026-05-06完成）
- 行情聚合器（Binance + OKX）
- 套利检测引擎
- SQLite数据存储
- 日志系统
- K线数据采集器（WebSocket）

### ✅ Phase 2: MVP核心系统（2026-05-06完成）
- 技术指标计算（MA、MACD、RSI、布林带）
- 策略基类设计（参考Freqtrade）
- 稳健策略实现（4重入场确认）
- 回测引擎（防前视偏差）
- 多时间周期测试
- 参数优化
- 样本外验证

**关键成果**：
- 发现1小时周期最优（1分钟/15分钟不盈利）
- 反欺骗机制使胜率从46.67%提升至83.3%
- 样本外验证通过，策略稳健
- 最佳参数：MA 7/25，RSI阈值75，成交量因子1.0

### ✅ Phase 3: 实盘交易系统（2026-05-06完成）
- 风控管理器（余额/回撤/每日亏损限制，峰值余额持久化）
- 合约执行器（CCXT统一接口，支持Binance/OKX，持仓持久化）
- 实时交易系统（整合策略+执行+风控）
- 止损止盈自动触发
- 多空双向交易支持
- 实时K线获取（含数据库降级）
- 系统完整性验证（15/16测试通过）
- OKX真实账户连接验证

**关键修复**（参考Freqtrade/CCXT最佳实践）：
- 合约交易实现：杠杆设置、reduceOnly参数、盈亏计算含杠杆
- 使用已闭合K线（iloc[-2]）防止前视偏差
- 风控逻辑：只限制亏损不限制盈利
- 持久化：峰值余额和持仓记录重启不丢失

### ✅ Phase 4: 多Agent系统（2026-05-07完成）
- 消息总线（asyncio Queue，支持topic:symbol路由）
- Agent基类（生命周期管理、消息收发、LLM调用）
- 编排器（两层架构：研判层12h周期 + 交易层持续运行）
- Claude API客户端（中转API支持、限流、重试）
- 研判层6个Agent：MarketScanner、SentimentResearcher、NewsResearcher、Synthesizer、Censor、SymbolRouter
- 交易层6个Agent：MultiDataCollector、MultiTechAnalyst、MultiJudge、MultiExecutor、PortfolioRiskGuard、ReviewerAgent
- 两阶段研判决策（初选→言官谏言→终选）
- LLM降级机制（Claude不可用时回退到规则引擎）
- 集成测试通过（研判层→交易层完整流水线）

### ✅ Phase 5c: 交易层深度升级（2026-05-07完成）
- DataCollector 9维度采集：K线(1h/4h) + Orderbook 20档 + 资金费率历史(8期) + OI delta(Binance) + 爆仓订单(OKX) + Taker买卖比 + 大单检测(P90阈值) + 多空账户比 + 10s价格流
- TechAnalyst 9维度信号解读：趋势结构(含4h偏向) + 关键价位(swing+orderbook墙) + 动量(RSI背离检测) + 资金流向(OI-价格背离/费率极值/Taker压力) + 微观结构(鲸鱼方向/深度偏向/爆仓强度) + 散户反指 + 风险评估(杠杆/波动/流动性)
- Judge 精确交易计划：7维度加权评分 → 入场区间 + 基于支撑阻力的多级止盈止损 + 动态杠杆1-20x(三因子) + 仓位管理 + RSI极端值保护 + 反欺骗/反人性决策
- 反欺骗验证8场景全通过：诱多陷阱→做空、恐慌底部→反人性做多、假突破→hold、杠杆过热→拒绝、信号矛盾→hold、完美做空→12x、主力洗盘→不追空、缩量阴跌→不做多

### ✅ Phase 5d: P0风控增强（2026-05-07完成）
- ReviewerAgent：交易历史追踪、滚动窗口指标（胜率/盈亏比）、策略衰减检测
- Daily Hard Stop机制：双重熔断（单日亏损≤-50 USDT 或 连续3次亏损）
- Graceful Shutdown：信号处理（SIGINT/SIGTERM）、状态保存、优雅停机
- RiskGuard状态持久化：持仓追踪、价格缓存、熔断状态重启恢复
- 交易层Agent数量：5→6（新增ReviewerAgent）

**关键特性**：
- 反馈闭环：execution_result → Reviewer → 策略复盘 → 衰减检测
- 熔断保护：Reviewer检测触发 → Executor/RiskGuard响应 → 拒绝新交易 + 全平持仓
- 状态持久化：data/trade_history.json + data/riskguard_state.json
- 测试覆盖：7个P0功能测试全通过（test_p0_features.py）

**研判层流水线**：
MarketScanner+SentimentResearcher+NewsResearcher → Synthesizer(初选) → Censor(谏言) → Synthesizer(终选) → SymbolRouter → 交易层

**交易层流水线（per-symbol）**：
DataCollector →[market_data:symbol]→ TechAnalyst →[tech_analysis:symbol]→ Judge →[trade_decision:symbol]→ Executor →[execution_result:symbol]→ RiskGuard + Reviewer

**Reviewer反馈闭环**：
execution_result → Reviewer → 交易历史记录 → 策略复盘（每12h） → 衰减检测 → Daily Hard Stop触发（如需）

**已知问题**：
- Claude中转API（dorocli.cc）偶尔被阻断，系统自动降级为规则引擎
- OKX rubik多空比API已不可用，改用Binance topLongShortAccountRatio

### ✅ Phase 6a: P1-A Telegram通知（2026-05-07完成）
- TelegramNotifier Agent：实时推送交易通知、风控告警、每日摘要
- 零配置降级：无TELEGRAM_BOT_TOKEN/CHAT_ID时自动禁用
- 消息过滤：只推送critical级别风控告警（flash_move/max_drawdown/emergency_close）
- 每日摘要：UTC日切时自动发送（交易笔数/胜率/盈亏/告警次数）
- Rate limiting：1 msg/sec 防止Telegram API限流
- 交易层Agent数量：6→7（新增TelegramNotifier）
- 首次实盘验证（2026-05-07 16:53-17:05）：研判层完成选标的SOL-USDT，交易层持续监控，信号不足正确观望，11分钟无崩溃

### ✅ Phase 6b: OKX下单修复（2026-05-07完成）
- Judge余额检查：新增`_update_balance()`，每次决策前查询USDT余额，余额不足时自动调整仓位或放弃
- 杠杆圆整：`_calc_leverage()`圆整到OKX允许值 [1, 2, 3, 5, 10, 20]
- 下单数量计算修复：`amount = (size_usdt * leverage) / price`（修复前漏乘杠杆）
- 最小订单检查：executor.py下单前检查合约最小数量限制，不足则放弃
- 言官提示词调整：降低驳回阈值，避免过度保守导致无标的可交易

### ✅ Phase 6b: contractSize修复 + 杠杆上限（2026-05-08完成）
- contractSize修复：`amount = (size_usdt * leverage) / (price * contract_size)` + `amount_to_precision()`（修复前DOGE会多下1000倍）
- Judge杠杆上限20x：OKX允许值列表[1, 2, 3, 5, 10, 20]

### ✅ Phase 6d: 方向决策修复（2026-05-08完成）
- 根因分析：之前所有交易亏损是因为在RSI极端超卖区域做空（DOGE RSI=20.1、ETH RSI=29.1）
- _compute_score重写：RSI极端值硬性保护（RSI<25禁空score≥-15、RSI>75禁多score≤15）
- 趋势强度衰减：strength>90时 `effective_strength = 90 - (strength-90)*2`（趋势末期信号）
- 散户反指条件化：RSI极端区域禁用反指（超卖时散户做多可能是正确抄底）
- RSI背离权重提升：极端区域+背离从+15提升到+35（强反转信号）
- JUDGE_PROMPT增加【关键禁令】：明确RSI禁区规则给LLM
- 验证结果：DOGE/ETH场景从错误的open_short变为正确的hold

### ✅ Phase 6e: Post-mortem修复 + 入场质量优化（2026-05-09完成）
- **Post-mortem修复**：correlation_risk改用保证金计算（非名义价值），Judge force_close冷却300s
- **R:R门槛**（`judge.py`）：`risk_reward_ratio < 1.5` → hold，赔率不足不入场
- **负面催化剂否决**（`synthesizer.py`）：近4h内hack/exploit/监管关键词 → confidence=0 → censor reject
- **Censor兜底**（`censor.py`）：规则降级时confidence<40 → reject
- **30min新闻轮询**（`multi_data_collector.py`）：`_tick_news()`每30min抓3家RSS，发布`news_snapshot`
- **price-in检测**（`judge.py`）：订阅`news_snapshot`，近4h有新闻+价格移动>3% → score×0.5

### ✅ Phase 6c: 系统逻辑校验修复（2026-05-08完成）
- 资金费率API修复：调用前检查`market.get('swap')`，非swap市场直接返回None（3处：data_collector/market_scanner/coin_selector_v2）
- 杠杆上限调整为20x：OKX允许值列表[1,2,3,5,10,20]，RiskGuard高杠杆阈值同步更新为20
- 止损最小距离保护：过滤距当前价<1.5%的支撑/阻力位，防止高杠杆下正常波动触发止损
- 组合回撤计算修复：用保证金（amount_usdt/leverage）而非名义价值计算盈亏，消除高杠杆下的误报
- Daily Hard Stop浮亏感知：Reviewer订阅risk_alert，组合回撤超限时将浮亏计入当日PnL触发熔断
- 止盈orderbook墙逻辑修复：`r >= wall`时插入wall止盈（原`r > wall`导致wall恰好等于阻力位时漏加）
- 趋势评分阈值收紧：strength>70才加分（原>60），减少弱趋势主导决策的情况

### 🔄 Phase 7: 待开发
- 修复资金费率API（`fetchFundingRate() is only valid for swap markets`）
- Predictor（趋势预测Agent）
- 更多数据源（链上大额转账、清算数据）
- Paper Trading模式

## 技术栈

- **数据获取**：ccxt 4.3+, aiohttp（异步HTTP）, feedparser（RSS解析）
- **数据处理**：pandas 2.0+
- **数据库**：SQLite3
- **异步IO**：asyncio
- **LLM**：openai SDK（Claude API通过OpenAI兼容格式，支持中转）
- **配置管理**：pyyaml, python-dotenv

## 运行命令

```bash
# 测试连接
python3 test_connection.py

# 启动单策略实盘
python3 main.py

# 启动多Agent交易系统
python3 run_agents.py

# Agent系统集成测试
python3 test_agents_integration.py

# 或使用启动脚本
./start.sh
```

## 已知问题

1. **资金费率API**：`fetchFundingRate() is only valid for swap markets`（持续警告，待修复）
2. **OKX错误11045**：设置杠杆偶发失败，不影响交易，可忽略
3. **Claude中转API偶尔被阻断**：系统自动降级为规则引擎，不影响交易

## 开发注意事项

- 所有时间使用UTC
- 日志文件按日期分割
- 数据库自动创建表结构
- 配置文件修改后需重启
- API密钥为空时仅获取公开行情数据
