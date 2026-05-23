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
│   ├── judge_state.json   # ✅ Judge风险状态持久化（deferred/cooldown/sl_timestamps）
│   ├── trade_history.json # ✅ 交易历史持久化（ReviewerAgent）
│   └── riskguard_state.json # ✅ RiskGuard状态持久化
├── agents/                # ✅ 多Agent交易系统（两层架构）
│   ├── base.py            # Agent基类（生命周期、消息收发、LLM调用）
│   ├── orchestrator.py    # 编排器（两层：研判4h + 交易持续）
│   ├── message_bus.py     # 消息总线（asyncio Queue，支持topic:symbol路由）
│   ├── llm_client.py      # Claude API客户端（中转API支持）
│   ├── research/          # 研判层（6个Agent）
│   │   ├── market_scanner.py       # OKX永续合约扫描（量/波动/费率/多空比/OI）
│   │   ├── sentiment_researcher.py # 恐贪指数+CoinGecko热度+Taker比
│   │   ├── news_researcher.py      # 6家加密媒体RSS新闻
│   │   ├── synthesizer.py          # Claude综合研判（两阶段决策）
│   │   ├── censor.py               # 言官逆向审查（Devil's Advocate）
│   │   └── symbol_router.py        # 标的路由+轮换协议
│   └── trading/           # 交易层（9个Agent，多标的并行）
│       ├── multi_data_collector.py  # 9维度数据采集（K线1h/4h/1d + orderbook/OI/爆仓/费率/Taker/大单/多空比）
│       ├── tech_analyst.py          # 9维度信号解读（多周期共振1h+4h+1d/日线价位/动量/资金流/微观结构/散户/风险）
│       ├── judge.py                 # 精确交易计划（rule_signal主驱动±35分/LLM修正/日线反欺骗/动态杠杆1-20x）
│       ├── executor.py              # 多标的交易执行 + Daily Hard Stop响应
│       ├── paper_executor.py        # 影子账户（并行实盘，订阅同样信号但不下真单，独立持久化data/paper_*）
│       ├── portfolio_risk_guard.py  # 组合级风控盯盘 + 状态持久化
│       ├── reviewer.py              # 交易复盘 + 策略衰减检测 + Daily Hard Stop触发
│       ├── position_analyst.py      # 持仓7因子评分 + 裁决引擎（每1h，防遗憾优化）
│       ├── behavioral_critic.py     # 行为金融学偏差检测（7种认知偏差，趋势保护）
│       └── telegram_notifier.py     # Telegram实时告警 + 每日摘要 + 远程命令控制
├── run_agents.py          # ✅ 多Agent系统启动入口（支持远程重启循环）
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
- 单次最大交易额：500 USDT（config.yaml，HARD_LIMITS上限10000）
- 最大回撤：20%
- 每日最大亏损：300 USDT（config.yaml）

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
| EFFECTIVE_BALANCE_CAP | 逻辑账户拆分：风控按此上限算余额（不影响真实余额查询）。范围[10, 1_000_000]。留空=用真实余额 | 否 |
| DRAWDOWN_BASELINE_MODE | 回撤基准模式：`session_start`（默认，启动重置）/ `persisted_peak`（继承历史峰值） | 否 |
| RESET_RISK_BASELINE_ON_START | 启动时是否重置本轮回撤基准 | 否（默认true） |
| ANTHROPIC_API_KEY | Claude API密钥 | 否（使用多Agent系统时必需） |
| ANTHROPIC_BASE_URL | Claude API地址（支持中转） | 否（默认api.anthropic.com） |
| ANTHROPIC_MODEL | Claude模型名 | 否（默认claude-opus-4-7） |
| RESEARCH_INTERVAL | 研判层运行周期（秒） | 否（默认14400=4h） |
| MAX_ACTIVE_SYMBOLS | 最大同时交易标的数 | 否（默认5） |
| MAX_CONCURRENT_POSITIONS | 最大并发持仓数 | 否（默认3） |
| RANK_FLUSH_DELAY | Ranking flush窗口秒数 | 否（默认5.0） |
| SHORT_LIVE_MIN_RSI | 空单入场最低RSI（防超卖追空） | 否（默认40） |
| SHORT_LIVE_MIN_RANGE_POS | 空单入场最低24h区间位置 | 否（默认0.45） |
| SHORT_LIVE_REQUIRE_DAILY_BEARISH | 空单是否要求日线偏空 | 否（默认true） |
| SHORT_LIVE_MAX_PRE_MOVE | 空单入场前12h最大跌幅 | 否（默认-0.01） |
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
- 编排器（两层架构：研判层4h周期 + 交易层持续运行）
- Claude API客户端（中转API支持、限流、重试）
- 研判层6个Agent：MarketScanner、SentimentResearcher、NewsResearcher、Synthesizer、Censor、SymbolRouter
- 交易层6个Agent：MultiDataCollector、MultiTechAnalyst、MultiJudge、MultiExecutor、PortfolioRiskGuard、ReviewerAgent
- 两阶段研判决策（初选→言官谏言→终选）
- LLM降级机制（Claude不可用时回退到规则引擎）
- 集成测试通过（研判层→交易层完整流水线）

### ✅ Phase 5c: 交易层深度升级（2026-05-07完成）
- DataCollector 9维度采集：K线(1h/4h/1d) + Orderbook 20档 + 资金费率历史(8期) + OI delta(Binance) + 爆仓订单(OKX) + Taker买卖比 + 大单检测(P90阈值) + 多空账户比 + 10s价格流
- TechAnalyst 9维度信号解读：趋势结构(多周期共振1h+4h+1d) + 关键价位(swing+orderbook墙+日线价位) + 动量(RSI背离检测) + 资金流向(OI-价格背离/费率极值/Taker压力) + 微观结构(鲸鱼方向/深度偏向/爆仓强度) + 散户反指 + 风险评估(杠杆/波动/流动性)
- Judge 精确交易计划：rule_signal主驱动(±35基础分) + 7维度辅助加减分 → 入场区间 + 基于日线支撑阻力的多级止盈止损 + 动态杠杆1-20x(三因子) + 仓位管理 + RSI极端值保护 + 日线反欺骗（阻力区做多衰减70%/支撑区做空衰减70%）+ LLM修正因子（非否决权）
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

**持仓管理流水线（每1h）**：
PositionAnalyst(7因子评分) →[position_review:symbol]→ BehavioralCritic(偏差检测) →[position_verdict:symbol]→ PositionAnalyst(裁决) →[trade_decision:symbol]→ Executor

**Reviewer反馈闭环**：
execution_result → Reviewer → 交易历史记录 → 策略复盘（每4h） → 衰减检测 → Daily Hard Stop触发（如需）

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

### ✅ Phase 6f: 日线多周期升级（2026-05-09完成）
- **DataCollector**：`_collect_1d()` 每慢周期采集30根日线K线，payload新增`klines_1d`
- **TechAnalyst多周期共振**：1h+4h+1d三周期投票（一致+20强度，矛盾-20）；4h RSI计算
- **TechAnalyst日线价位**：`daily_near_resistance/support`检测（距20日高低点**1.5%**以内）；日线swing支撑阻力
- **Judge日线反欺骗**：接近日线阻力区做多信号衰减70%；接近日线支撑区做空信号衰减70%
- **Judge止损锚点**：优先用日线支撑阻力（比1h swing更可靠）
- **Synthesizer放开限制**：所有USDT永续合约均可选（含XAU/CL等），波动率范围扩至50%，成交量门槛$30M
- **MarketScanner并发**：asyncio.gather替代串行enrichment

### ✅ Phase 6g: Judge主驱动修复（2026-05-09完成）
- **根因**：rule_signal（回测83%胜率的MA交叉信号）未参与_compute_score评分，系统永远hold
- **修复**：rule_signal触发时给±35基础分，确保过30分入场门槛
- **LLM降权**：rule_signal触发时LLM从一票否决改为仓位修正（最多降30%仓位，不能阻止入场）
- **保守逻辑保留**：无rule_signal时维持原有逻辑（LLM可否决弱信号）

### ✅ 2026-05-09 Bug修复（做空+ticker+阈值）
- **做空信号修复**（`optimize_1h.py`）：RobustStrategy新增`entry_short`/`exit_short`，做空4重确认（MA死叉+RSI>25+放量+价格下跌），`exit_short`（MA金叉或RSI<20）
- **PROS-USDT ticker修复**（`multi_data_collector.py`）：`_fetch_price_tick`改用`symbol.replace('-USDT', '/USDT:USDT')`，修复OKX symbol格式不匹配
- **日线阻力区阈值收紧**（`tech_analyst.py`）：3%→1.5%，横盘行情不再持续误触发衰减

### ✅ Phase 6h: MA alignment信号 + Symbol sync修复（2026-05-11完成）
- **MA alignment信号**（`tech_analyst.py` + `judge.py`）：新增`ma_aligned_long/short`（MA fast/slow已对齐≥3根K线），Judge给±20基础分作为次驱动；解决MA crossover仅触发1根K线后系统永远hold的根因
- **Symbol sync修复**（`executor.py` `sync_positions`）：OKX返回`LAYER/USDT:USDT`自动转换为内部格式`LAYER-USDT-SWAP`，防止每次sync循环删除并重建持仓（导致SL/TP丢失）
- **Daily Hard Stop reset**：清除`data/trade_history.json`中4条`entry_price=0`的ETH脏数据，重置`trading_halted=false`
- **首次成功开仓**：LAYER-USDT short @ 0.12171，3x杠杆，SL=0.1254，TP=0.1181
- **止损止盈计算修复**（`judge.py`，2026-05-13）：R:R硬性门槛1.5（不因confidence放松）；SL距离ATR封顶（2.5×ATR，max 5%，Turtle Traders方法论）；TP下限=SL×1.5

### ✅ Phase 6i: flash_move修复 + 研判扩容 + 持仓管理三角决策（2026-05-12完成）
- **flash_move修复**（`executor.py` + `portfolio_risk_guard.py`）：从全平所有持仓改为只平触发标的，单币闪崩不等于系统性风险（修复INJ因BILL暴涨被误平的问题）
- **Synthesizer扩容**（`synthesizer.py`）：初选上限3→12，prompt更新为"5-12个"，增加机会面供Censor筛选
- **持仓监控补充**（`multi_data_collector.py`）：新增`_get_position_symbols()`，自动将持仓标的纳入监控，即使不在SymbolRouter活跃列表中，确保所有持仓持续接收技术分析
- **PositionAnalyst**（`position_analyst.py`）：6因子规则评分（趋势对齐/动量变化/时间衰减/浮盈状态/成交量确认/剩余R:R）+ 5条硬性覆盖规则 + 4级severity裁决矩阵，每30min评估
- **BehavioralCritic**（`behavioral_critic.py`）：LLM检测7种认知偏差（loss_aversion/sunk_cost/anchoring/fomo/disposition/overconfidence/panic），规则降级兜底
- **交易层Agent数量**：7→9（新增PositionAnalyst + BehavioralCritic）

### ✅ Phase 6j: 持仓管理防遗憾优化 + Telegram远程命令（2026-05-13完成）
- PositionAnalyst防遗憾优化：7因子(+entry_thesis_intact ±25)、2h周期、阈值放宽(loss>15%/72h+3%/HTF反转+5%)、裁决引擎趋势保护
- BehavioralCritic防遗憾优化：规则降级增加trend_aligned/htf_aligned验证
- Telegram远程命令：getUpdates轮询+7命令(/status/positions/stop/restart/halt/resume/log)+system_command总线
- Orchestrator：system_command订阅+_command_listener协程
- Executor：system_command订阅，halt/resume响应
- run_agents.py：while循环+.restart_flag检测，支持远程重启

### ✅ Judge LLM-Rule方向冲突修复（2026-05-14完成）
- **根因**：ZEC-USDT在RSI=29~30时被开20x做空，三层缺陷叠加
- **Fix 1**：confidence提升需LLM方向与规则方向一致；方向冲突时衰减50%
- **Fix 2**：RSI禁区阈值统一为>=70禁多、<=30禁空（inclusive，与JUDGE_PROMPT一致）
- **Fix 3**：rule_signal+LLM反向开仓=强冲突衰减60%（confidence降至30-40，低于Executor的60门槛）
- **PositionAnalyst规则3b**：浮亏>10%+趋势非顺向(neutral或反转)→强制平仓
- **llm_client.py**：chat_json()支持temperature参数传递（修复BehavioralCritic调用失败）
- **验证**：16个Judge场景+11个PositionAnalyst场景全通过；Monte Carlo模拟开仓率12.6%→7.8%，预估胜率58%→75%
- **设计参考**：Freqtrade confirm_trade_entry模式、Jesse Livermore "When in doubt, stay out"

### ✅ 统一风险预算框架（2026-05-14完成）
- **核心公式**：`leverage = max_loss / (margin × sl_dist) = 0.5 / sl_dist`
- **设计原则**：杠杆不是独立输入，而是从风险约束推导的结果
- **固定参数**：margin = min(余额×10%, max_trade_amount)，max_loss = 余额×5%
- **杠杆向下圆整**到OKX允许值[1,2,3,5,10,20]，保证max_loss不超预算
- **size_usdt语义**：= 保证金（margin），Executor内部乘leverage得名义价值
- **effective_rr**：(gross_profit - funding_cost - fee) / (max_loss + costs)，含资金费率方向性
- **资金费率方向性**：正费率做多付费/做空收费，负费率反之
- **ATR持仓时间估算**：高ATR→16h，中→32h，低→48h
- **删除旧函数**：`_calc_leverage` + `_calc_size` → 统一为 `_calc_risk_budget`
- **实盘验证**：BTC 20x/ETH 10x/ZEC 10x，单笔最大亏损≤5%余额，高费率做多被R:R拒绝
- **Monte Carlo模拟**：开仓率31%，日均1.6笔，日化预期1.0%~1.5%

### ✅ 回调入场机制（2026-05-14完成）
- **问题**：统一风险预算上线后，多个正确方向信号因R:R<1.5被拒（BASED score=-65 R:R=1.18, TON score=-45 R:R=1.39）
- **理论基础**：Al Brooks Signal/Entry Bar、ICT Fair Value Gap回填、Turtle Traders回踩确认
- **三级响应矩阵**：
  - R:R≥1.5 → 正常入场（现有逻辑）
  - 1.2≤R:R<1.5 且 |score|≥50 → 追价入场（仓位=rr/1.5，min 60%）
  - 1.2≤R:R<1.5 且 |score|<50 → 回调等待（target_price由R:R反推，3h有效）
  - R:R<1.2 → 放弃
- **deferred_entry状态机**：每tick检查回调到位/追价触发(移动>1.5%无回调)/过期(3h)/趋势反转取消
- **余额保护**：回调/追价触发时重新调用_build_plan，size_usdt<1.0则放弃
- **Executor适配**：confidence=60满足门槛，通过key_factors区分入场类型
- **验证**：8个单元测试全通过，实盘TIA/INJ通过deferred entry入场并盈利

### ✅ Censor分批审查 + LLM超时修复（2026-05-14完成）
- **根因**：Synthesizer扩容（3→12标的）后，9个symbol一次性发给Censor LLM，prompt过长超Cloudflare 100s网关超时
- **修复**：censor.py分批处理BATCH_SIZE=4，每批独立LLM调用，失败则该批规则降级
- **LLM客户端加固**：llm_client.py新增httpx.Timeout(connect=10, read=90, write=10, pool=10) + max_retries=2
- **Executor required_margin修复**：`required_margin = size_usdt`（不再除以leverage，因size_usdt在统一风险预算中已是margin语义）

### ✅ HYPE重复做空事故修复：5层防护（2026-05-15完成）
- **事故**：HYPE-USDT在日线强上升趋势中被连续做空15+次（RSI=85+bearish_div，无rule_signal）
- **Fix 1**（judge.py `_compute_score`）：RSI背离在日线强趋势中降权（35→15）。htf=bullish时1h bearish_div降权，反之亦然
- **Fix 2**（judge.py）：无rule_signal/ma_aligned时入场门槛从25提高到40（辅助维度需强共振才允许入场）
- **Fix 3**（judge.py）：无rule_signal时LLM confidence上限55，方向确认boost到60（非65）
- **Fix 4**（judge.py）：开仓成功后300s冷却（防止止损后立即重开同方向）
- **Fix 5**（agents/trading/executor.py）：开仓失败后120s冷却（防OKX报错刷屏）
- **SL/TP方向校验**（executor.py）：下单前验证SL/TP方向合法性，价格变动导致方向错误时自动修正
- **PositionAnalyst评估周期**：2h→1h（更及时的持仓管理）
- **设计参考**：Al Brooks "With-trend pullbacks are not reversals"、Freqtrade stoploss_on_exchange_update
- **验证**：HYPE场景score=-25 < 门槛40 → hold（第2层直接拦截），正常rule_signal入场不受影响

### ✅ 加仓/减仓功能修复（2026-05-15完成）
- **加仓bug**：PositionAnalyst发add(open_long/open_short)，Executor只在position=None时执行 → 已有持仓时静默丢弃
- **减仓bug**：PositionAnalyst发reduce(action=close, size_pct=0.5)，Executor忽略size_pct直接全平
- **加仓修复**（executor.py `add_to_position`）：加权平均入场价 + SL/TP按原距离比例重算 + 保证金上限max_trade_amount×2
- **减仓修复**（executor.py `reduce_position`）：取消旧SL条件单 + 精度格式化 + 浮点兜底(剩余<min_amount视为全平)
- **全系统同步**：execution_result新增`is_add`标记(加仓增量更新) + `risk_reduced`状态(减仓) + `reduce_pct`参数
- **下游适配**：RiskGuard/PositionAnalyst/TelegramNotifier均正确处理加仓增量更新和减仓比例更新
- **设计参考**：Freqtrade adjust_trade_position + stoploss_on_exchange_update + partial exit

### ✅ PA动态阈值 + Close冷却 + Telegram去重（2026-05-15完成）
- **PA Rule 1事故**：ZEC 10x杠杆，原价差1.5%被PA计算为-20.9%（含杠杆），触发固定15%阈值被误平
- **PA Rule 1修复**（position_analyst.py）：阈值=SL含杠杆距离（第三道防线，只在交易所SL+Executor轮询都失败时触发）
- **PA Rule 3b修复**（position_analyst.py）：阈值=SL距离×50%（替代固定10%，入场逻辑失效早期信号）
- **三层止损防线**：交易所SL条件单(实时) → Executor本地5s轮询 → PA规则1(1h周期)
- **Close冷却60s**（executor.py）：平仓后sync_positions不重新发现该标的（防API延迟导致幽灵持仓循环）
- **Telegram sync过滤**（telegram_notifier.py）：source=sync的持仓不推送开仓通知
- **Telegram close去重**（telegram_notifier.py）：同symbol 60s内不重复推送平仓通知
- **加仓后SL更新**（executor.py add_to_position）：cancel旧SL + place新SL（数量和价格都变了）

### ✅ Symbol格式统一修复 + UB事故Fix A（2026-05-15完成）
- **根因**：系统内symbol格式不统一——DataCollector/TechAnalyst/Judge用`ZEC-USDT`，ContractExecutor positions dict用`ZEC-USDT-SWAP`，`closed_externally`通知携带`ZEC-USDT-SWAP`
- **后果**：Judge/PA/RiskGuard收到`closed_externally`时用错误key查state → 冷却无效、幽灵持仓不清除 → ZEC重复开仓3次、SL被sync覆盖
- **Fix**（judge.py + position_analyst.py + portfolio_risk_guard.py）：execution_result handler入口strip `-SWAP`后缀，统一为tech_analysis格式
- **即时冷却**（judge.py）：deferred_entry/追价/正常决策发出open信号后立即设`last_open_time`，不等execution_result回来
- **验证**：2026-05-16运行19h，ZEC无重复开仓，closed_externally正确清除state（UB-USDT、HYPE-USDT均正确处理）
- **UB事故Bug A已修复**：closed_externally通知 → PA/RiskGuard正确清除幽灵持仓

### ✅ UB-USDT事故修复（Bug A/B/C全部完成）
- **Bug A**（2026-05-15）：closed_externally通知 → PA/RiskGuard正确清除幽灵持仓（Symbol格式统一修复）
- **Bug B**（已在代码中修复）：`_open_position`已正确除以contractSize + amount_to_precision
- **Bug C**（已在代码中修复）：`_open_position`已在OKX设置SL条件单

### ✅ Phase 6p: PnL追踪 + 递增冷却 + 上线时间过滤（2026-05-17完成）
- **closed_externally PnL追踪**：sync_positions保存被移除持仓数据 → `_estimate_close_pnl`优先用unrealized_pnl，降级用SL价格。Daily Hard Stop现在能检测交易所SL触发的真实亏损
- **递增冷却StoplossGuard**：4h窗口内连续SL次数递增冷却（300→600→1200→3600s），参考Freqtrade StoplossGuard
- **研判层上线时间过滤**：OKX月K线<12根的标的不进入初选（上线不足1年）
- **初选固定12标的**：SYNTHESIS_PROMPT从"5-12个"改为"12个"
- **Telegram启动flush旧消息**：_flush_old_updates()跳过所有pending消息，防止历史/stop命令杀进程
- **终选prompt优化+代码保底**：区分reject/warning，终选<非reject数一半时自动补充非reject标的
- **Logger防重复**：propagate=False + handler去重，解决日志打印7次的问题

### ✅ Phase 6c: 系统逻辑校验修复（2026-05-08完成）
- 资金费率API修复：调用前检查`market.get('swap')`，非swap市场直接返回None（3处：data_collector/market_scanner/coin_selector_v2）
- 杠杆上限调整为20x：OKX允许值列表[1,2,3,5,10,20]，RiskGuard高杠杆阈值同步更新为20
- 止损最小距离保护：过滤距当前价<1.5%的支撑/阻力位，防止高杠杆下正常波动触发止损
- 组合回撤计算修复：用保证金（amount_usdt/leverage）而非名义价值计算盈亏，消除高杠杆下的误报
- Daily Hard Stop浮亏感知：Reviewer订阅risk_alert，组合回撤超限时将浮亏计入当日PnL触发熔断
- 止盈orderbook墙逻辑修复：`r >= wall`时插入wall止盈（原`r > wall`导致wall恰好等于阻力位时漏加）
- 趋势评分阈值收紧：strength>70才加分（原>60），减少弱趋势主导决策的情况

### ✅ PA NameError修复（2026-05-19）
- **Bug**：`position_analyst.py:336` — `position.get('amount_usdt', 0)` 中 `position` 未定义
- **修复**：改为 `pos.get('amount_usdt', 0)`（方法参数名是 `pos`）
- **影响**：PA每次评估持仓时崩溃，三角决策完全失效

### ✅ Phase 7: Trailing Stop + 分批止盈（2026-05-19完成）
- **问题**：止盈位到不了，趋势回落把盈利变亏损（ZEC两笔交易PnL -134.53）
- **三阶段利润保护**：
  - 阶段1 Break-Even：浮盈≥1R → SL移到入场价+手续费（消除亏损风险）
  - 阶段2 分批止盈：TP1触发(tp_levels[0])平50%+SL移+0.5R；TP2触发(tp_levels[1])再平25%+SL移+1.5R
  - 阶段3 Trailing Stop：tp_filled≥1后激活，跟踪距离`max(atr_pct, R×0.5)`，棘轮机制只向有利方向移动
- **持仓数据扩展**（executor.py `open_position_with_plan`）：新增`original_sl`/`highest_price`/`lowest_price`/`tp_filled`/`atr_pct`/`original_amount`
- **新方法**：`_update_trailing()`检测TP/BE/Trailing触发，`_move_sl()`节流更新（变动>0.3%且间隔>30s）+ 持久化
- **加仓后基准重置**（executor.py `add_to_position`）：`original_sl = position['stop_loss']`（按新加权SL重新计算R）
- **MultiExecutor适配**（agents/trading/executor.py）：`_check_all_positions`处理`partial_tp_1`/`partial_tp_2`触发器，发布`risk_reduced`状态
- **Judge plan输出**（judge.py `_build_plan`）：新增`atr_pct`字段传递给Executor用于trailing距离计算
- **向后兼容**：旧持仓无新字段时走原逻辑（单TP+固定SL）

### ✅ Phase 8: 市场 Regime 优化（2026-05-21完成）
- **问题**：28h 实盘 449 plans / 0 openings，R:R<1.5 全拦；long 66.7% win rate 被浪费，short 14.3% 持续亏损
- **RegimeManager**（`utils/market_regime.py`）：BTC/ETH bias + 全标的趋势共识 → bullish/bearish/mixed/choppy，2次确认切换 + 30min min_hold 防抖
- **CounterfactualLedger**（`utils/counterfactual_ledger.py`）：被拒信号影子追踪，24h TP/SL 解析，验证策略有效性
- **Short Regime Guard**（judge.py）：牛市普通做空拦截，强做空（score≤-70, htf≥2, rr≥1.8, 15m confirm）放行
- **Probe Short**：牛市 BTC RSI 反转/breadth 恶化时小仓位探针做空（30% position, 3x, 24h cooldown）
- **Dynamic R:R**：牛市多头 1.30 / 牛市空头 1.80 / 默认 1.50
- **Low R:R Extra Slot**（candidate_ranker.py）：低 R:R 多头独立额外槽位，rank score 打 70% 折扣
- **Feature Flags**：REGIME_HYSTERESIS_ENABLED / SHORT_REGIME_GUARD_ENABLED / PROBE_SHORT_ENABLED / LOW_RR_SLOT_ENABLED / COUNTERFACTUAL_LEDGER_ENABLED
- **验证**：373 passed / 4 deselected / 1 warning

### ✅ Phase 1.5: 观测与回测同构补齐（2026-05-21完成）
- **EventBacktest同构Phase 1 live策略**（`event_backtest.py`）：regime列支持、动态R:R floor（bullish long 1.30/bullish short 1.80/default 1.50）、short regime guard、probe short with cooldown、low R:R position scaling、segmented metrics输出（side×regime×slot_type）、insufficient_sample标记
- **Reviewer分层策略复盘**（`agents/trading/reviewer.py`）：`_calculate_segmented_metrics()`输出metrics_by_side/metrics_by_regime/metrics_by_slot_type，每项含trade_count/win_rate/profit_factor/total_pnl/insufficient_sample
- **PA entry_regime grace**（`agents/trading/position_analyst.py`）：`_get_current_regime()`读取`data/regime_state.json`；low_rr仓位在entry_regime=bullish→current≠bullish的60min内不触发trend-based reduce/close
- **验证**：329 passed / 4 deselected / 0 failed
- **Phase 2 Go/No-Go**：主路径已通过，但 deferred open / ranking-disabled 的统一 slot gate 仍需收口后再进入 Phase 2

### ✅ Phase 2: 决策语义拆分 + 分桶EV + 动量探针（2026-05-22完成）
- **EPIC A: Confidence Split**（`judge.py`）：signal_score(原始评分) / execution_confidence(是否执行) / position_scale(0-1仓位缩放)三层拆分；LLM hold+rule_signal+HTF aligned时conf floor从40提升到60
- **EPIC B: Momentum Probe Long**（`judge.py`）：RSI 70-85区间+强趋势+HTF bullish+无背离→小仓位追趋势（30% position, 3x leverage, max 1 concurrent）
- **EPIC C: 趋势饱和修正**（`judge.py _compute_score`）：strength>90 cap at 90（不再线性压缩）；4h RSI动态衰减（70-75→0.7, 75-80→0.5, >80→0.3替代固定0.5）
- **EPIC D: 分桶EV门**（`judge.py _check_expected_value`）：per side×regime×entry_type×slot_type胜率替代全局fallback；Reviewer segmented metrics注入；insufficient sample→60%缩仓不冻结强信号
- **EPIC E: request_id + replay_report**（`judge.py` + `replay_report.py`）：每次决策UUID追踪；回放报表脚本（时间窗口过滤、bucket PF、shadow TP/SL）
- **Side-Aware Short Gates**（`judge.py _apply_regime_policy`）：daily_bias/range_pos/pre_move/rsi/score/htf_votes六重做空入场门（所有regime生效）
- **Unified Dispatch Path**（`judge.py _gate_and_publish_open`）：所有open决策带`dispatch_path`归因（main_direct/main_ranking/deferred_15m/deferred_pullback/deferred_chase）
- **`_can_route_probe_short`返回`(bool, str)`元组**：reason包含probe_disabled/probe_active_full/probe_cooldown/probe_pending_full/probe_not_eligible/score_too_low/15m_not_confirmed/rr_too_low/liquidity_zero
- **Probe R:R Floor**：probe仓位使用1.3 floor（不受bullish short 1.80限制）
- **Feature Flags**：PHASE2_SIGNAL_CONFIDENCE_SPLIT_ENABLED / PHASE2_MOMENTUM_PROBE_LONG_ENABLED / PHASE2_TREND_SATURATION_ENABLED / PHASE2_BUCKETED_EV_ENABLED；request_id 已改为 always-on，旧 PHASE2_REQUEST_ID_ENABLED 仅视为兼容 no-op
- **新增测试**：test_phase2_confidence_split(9) + test_phase2_momentum_probe(10) + test_phase2_bucketed_ev(7) + test_phase2_replay_report(4) + test_phase2_regressions(6) = +36 tests
- **验证**：408 passed / 4 deselected / 0 failed

### ✅ 系统审计阻断项修复（2026-05-22完成）
- **AC-01 EV正数loss契约**：`_check_expected_value`公式修复（`+`→`-`），`net_loss_usdt`统一为正数，测试契约修正
- **AC-02 win_rate单位统一**：event_backtest输出`win_rate_ratio`(0-1)+`win_rate_pct`(0-100)；Judge `_normalize_bucket_win_rate`自动检测>1并转换；Reviewer增加`win_rate_ratio`/`win_rate_pct`/`gross_profit`/`gross_loss`字段
- **AC-03 统一open dispatcher**：`_flush_ranked_candidates` selected路径改为调用`_gate_and_publish_open`（不再直接publish）
- **AC-04 request_id全链路**：dispatcher生成`{date8}-{symbol}-{uuid8}`格式request_id；顶层+attribution双写；Executor/PaperExecutor透传；Reviewer记录`entry_request_id`/`exit_request_id`/`dispatch_path`
- **AC-05 Executor拒单终态**：`result is None`时发布`rejected:unknown_none_result`；所有execution_result带`schema_version=execution_result.v2`+`request_id`
- **AC-06 probe_long控制面**：CandidateRanker分离probe_short/probe_long独立slot；Judge slot_occupancy增加probe_long；event_backtest slot_type循环增加probe_long
- **AC-07 接口契约**：open decision带`schema_version`/`request_id`/`signal_score`/`execution_confidence`/`position_scale`/`dispatch_path`
- **当前待解决事项**：统一收敛到 `docs/待解决事项.md`（OKX testnet 验收仍待执行）
- **新增测试**：test_executor_terminal_result(5) + test_request_id_flow(5) + test_metrics_contract(5) = +15 tests
- **验证**：423 passed / 4 deselected / 0 failed

### ✅ 回撤基准修正（2026-05-23完成）
- **问题**：用户从OKX转出资金后（6268→4864 USDT），历史`peak_balance=6268`误判22%回撤，拒绝所有开仓
- **RiskManager session基准**（`risk_manager.py`）：新增`initialize_session(real_total, cap)`，启动时用`min(real_balance, EFFECTIVE_BALANCE_CAP)`作为本轮`session_peak_equity`
- **check_can_open()**：基于`session_peak_equity`计算回撤（替代历史`peak_balance`），reason包含`risk_equity/peak/drawdown_pct`
- **close/reduce绕过风控**（`agents/trading/executor.py`）：只对`open_long/open_short`执行回撤检查，close/reduce不拦截
- **状态文件v2**（`data/risk_state.json`）：`schema_version=risk_state.v2`，含`session_baseline_equity/session_peak_equity/legacy_peak_balance/baseline_mode`，兼容旧v1格式
- **配置项**：`DRAWDOWN_BASELINE_MODE`（默认`session_start`，兼容`persisted_peak`）、`RESET_RISK_BASELINE_ON_START`（默认true）
- **启动日志**：`[RiskBaseline] real_total=4864.46 cap=300 risk_equity=300.00 mode=session_start peak=300.00`
- **验证**：469 passed / 4 deselected / 0 failed

### 🔄 Phase 9: 待开发
- Predictor（趋势预测Agent）
- 更多数据源（链上大额转账、清算数据）
- 参数 grid search（基于 event_backtest）
- P3-R 验收测试体系
- OKX testnet 端到端矩阵验证

### ✅ RQ-15M: 15m 入场确认层（2026-05-20完成）
- **问题**：1h setup 成立时直接开仓，多次开仓方向与 15m K 线趋势背驰
- **DataCollector**：`_collect_15m(symbol)` 采集 100 根 15m K 线，含新鲜度追踪（`klines_15m_updated_at`/`klines_15m_last_ts`/`klines_15m_error`），stale 判定 age > 2×15min
- **TechAnalyst**：`_analyze_entry_timing_15m()` 使用 iloc[-2]（已闭合），MA(7)/MA(25)、RSI(14)、recent 3 closes 方向，输出 bias/confirm/block
- **Judge 硬过滤**：`_check_15m_entry_timing()` 在 EV gate 通过后、发布 open 前执行。block→deferred_15m_confirmation / confirm→pass / neutral+strong+HTF同向→pass
- **Deferred 15m**：`deferred_15m_confirmation` 类型不检查价格回调，只等 15m 转向，超时由 `entry_timing_15m_timeout_hours` 控制
- **Attribution**：所有开仓决策包含 `tf_15m_bias`/`tf_15m_rsi`/`tf_15m_ma_alignment`/`tf_15m_recent_closes`/`tf_15m_entry_status`/`tf_15m_block_reason`
- **配置**：`ENTRY_TIMING_15M_ENABLED`/`_REQUIRED`/`_NEUTRAL_ALLOWS_STRONG_SIGNAL`/`_STRONG_SCORE_THRESHOLD`/`_DEFER_ON_BLOCK`/`_TIMEOUT_HOURS`

### ✅ 第三次审计 P1/P2 修复（2026-05-20完成）
- **P1-1**：15m 数据新鲜度 stale 检测（age > 30min → tf_15m_stale=True）
- **P1-2**：deferred_15m_confirmation 路径补齐 `_open_quality_rejection()` + `_build_attribution()`
- **P1-3**：Research cycle_id 切换修复（新 cycle market_data 到达时清空 pending 并切换）
- **P1-4**：Ledger limit open + add lifecycle（`_execute_limit_order` 返回 order_id，limit/fallback/add 路径均调用 `ledger.record_open()`）
- **P1-5**：CandidateRanker docstring 明确"仅用于归因，Top-N 未启用"
- **P1-6**：Judge state 持久化 `data/judge_state.json`（deferred_entry/sl_timestamps/cooldown timestamps，原子写入，启动时恢复有效状态）
- **P1-7**：LLM degraded 连续 3 次失败后发布 `risk_alert` 告警
- **P2-1**：15m 配置纳入 `config_loader.py` DEFAULTS + env_map
- **P2-2**：neutral 强信号放行增加 `_has_directional_confirmation()` HTF 同向条件
- **P2-3**：新增 `test_15m_e2e.py` 端到端集成测试（5 tests）
- **P2-4**：Telegram `/status` 展示 HaltState 对账状态
- **P2-5**：PA `closed_externally` 时清理 `_pending_reviews`
- **P2-6**：LiveLedger `position_id` 改用 uuid 防碰撞
- **最终 CI**：`python3 -m pytest -q` → 373 passed / 4 deselected / 1 warning / ~186s

### ✅ 最终审计报告4项修复（2026-05-20晚完成）
- **P1-1 Synthesizer cycle分桶**：`_pending_by_cycle[cycle_id][msg_type]` 按 cycle 分桶缓存三路数据；market_data 到达时激活并恢复桶内已到达数据；保留最新2桶防泄漏
- **P2-1 Executor拒单+Judge pending TTL**：所有 open 拒单路径（halt/reconciliation/cooldown/balance/confidence）发布 `execution_result:rejected`；Judge `_sweep_stale_pending()` 120s TTL 自动释放
- **P2-2 配置化**：`RANK_FLUSH_DELAY`(float) + `MAX_CONCURRENT_POSITIONS`(int) 纳入 env_map/HARD_LIMITS/DEFAULTS/banner/.env.example/runbook
- **P2-3 Reconciler接入**：`MultiExecutor.setup()` 初始化 Reconciler；`tick()` 每10min对账；偏差发布 `risk_alert`(type=reconciliation_mismatch)
- **新增测试**：`test_synthesizer_cycle.py`(3) + `test_ranking_slots.py` TTL sweep(1) = +4 tests

### ✅ Regime优化最终审计P0/P1/P2全修复（2026-05-21完成）
- **P0-1 Probe slot/pending全链路闭环**：`_can_route_probe_short()`检查`_pending_open_slots`中已有probe_short + liquidity gate(liquidity_score>0)；Final slot gate增加probe_short分支；`CandidateRanker.rank_and_select()`将probe候选独立分类(available_probe=max(0,1-probe_used))
- **P1-1 `_record_rejected_plan`支持显式attribution**：新增`attribution: dict = None`参数，无显式传入时自动调用`_rejection_attribution()`生成完整attribution
- **P1-3 Final slot gate + ranked_out补attribution**：main/low_rr/probe三个slot gate分支都生成并传递gate_attr；ranked_out hold决策也带完整attribution
- **P2-1 PA补漏仓位normalize**：抽取`_normalize_position_record()`静态方法，`_load_positions()`和`_evaluate_all_positions()`补漏路径共用
- **新增测试**：6个probe slot chain测试（同窗口两probe只选一个/probe slot满拒绝/main满probe可进/pending probe阻止第二个/liquidity gate拦截+放行）
- **验证**：329 passed / 4 deselected / 0 failed

### ✅ Short-Side Fix + Unified Dispatch（2026-05-21完成）
- **Side-Aware Short Entry Gates**（`tech_analyst.py` + `judge.py` + `event_backtest.py`）：
  - `position_in_24h_range`：24h高低点区间内当前价格位置，<0.45禁止做空（底部追空）
  - `pre_12h_return_pct`：前12h价格变动，<=-1%禁止做空（已跌太多）
  - `daily_bias=bearish`：日线必须偏空才允许正常做空（否则路由到probe或拒绝）
  - RSI>=40：超卖区禁止做空（反弹风险）
- **Unified Open Dispatch**（`judge.py`）：`_gate_and_publish_open`统一入口，所有open决策带`dispatch_path`归因
- **dispatch_path类型**：main_direct / main_ranking / deferred_15m / deferred_pullback / deferred_chase
- **`_can_route_probe_short`**：返回`(bool, str)`元组，reason包含probe_disabled/probe_active_full/probe_cooldown/probe_not_eligible/probe_low_liquidity
- **新增环境变量**：SHORT_LIVE_MIN_RSI(40) / SHORT_LIVE_MIN_RANGE_POS(0.45) / SHORT_LIVE_REQUIRE_DAILY_BEARISH(true) / SHORT_LIVE_MAX_PRE_MOVE(-0.01)
- **回测验证**：BTCUSDT 1h×1000 bars，side-aware gates将short从4笔(0%WR, -9.18 PnL)过滤为0笔，long从7→9笔
- **新增测试**：test_short_side_guard.py(10) + test_ac_fix_unified_dispatch.py(24) = +34 tests
- **验证**：367 passed / 4 deselected / 0 failed

### ✅ Phase 7+: 4h RSI 衰减 + 逻辑账户拆分 + Paper Trading（2026-05-19完成）
- **4h RSI 二级保护**：`judge.py _compute_score` 末尾——1h RSI 未触发硬cap但 4h RSI ≥70/≤30 时 score×0.5。根因 ZEC 事故（1h=64 但 4h=73.9 仍开多 20x→-135）
- **逻辑账户拆分**：新增 `EFFECTIVE_BALANCE_CAP` 环境变量，真实余额 6020 但风控按 1000 算，单笔 max_loss 250→50 与 Daily Hard Stop -50 对齐。cap=None 时等价旧逻辑
- **Paper Trading 全并行**：`agents/trading/paper_executor.py` 新建，与 MultiExecutor 并行收同样信号，独立 in-memory 余额持久化到 `data/paper_*`，发布独立 topic `paper_execution_result`
- 交易层 Agent 数：9→10（新增 PaperExecutor）

### ✅ 第五轮审计修复（2026-05-19完成）
- **订单预检全覆盖**（`executor.py` `_execute_limit_order`）：limit 路径（line 753-761）+ fallback 市价路径（line 807-815）补 `precheck_order()`，5 个 `create_order` 落点全部覆盖（市价/limit/fallback/加仓/旧路径）；所有点都传 `size_usdt`=margin，内部 `notional = size_usdt × leverage`
- **默认 pytest 干净 CI**：`conftest.py` `collect_ignore = ["test_kline.py"]`（websockets 非测试文件），`pytest.ini addopts = -m "not network"`（默认排除外部数据测试），`test_backtest/indicators/strategy.py` 加 `@pytest.mark.network`。默认 `python3 -m pytest -q` → 184 passed / 3 deselected / ~170s

### ✅ 第六轮审计修复（2026-05-19完成）
- **test_kline.py 网络标记**：加 `@pytest.mark.network`，`conftest.py` 删除 `collect_ignore`（marker 已足够），默认 CI 变为 4 deselected
- **`_get_balance()` 实数校验**（`agents/trading/executor.py`）：`numbers.Real` + `math.isfinite()` 双重校验，非实数/非有限值返回 `-1.0`；测试 mock 中 `balance_adapter = None` 强制走 `fetch_balance` 路径

### ✅ 第七轮审计修复（2026-05-19完成）
- **event_backtest 权益曲线污染**（`event_backtest.py`）：开仓信号触发时先 append 权益（position=None），再 `_open_position()`，再 `continue`，消除前视偏差导致的 max_dd/Sharpe 高估
- **PaperExecutor 原子写入**（`agents/trading/paper_executor.py`）：`_persist_state()` 改用 `atomic_write_json()`（write-to-temp + rename），防崩溃时写出半截 JSON
- **live_trading.py DEPRECATED**：docstring 首行标注绕过多 Agent 系统，生产环境用 `run_agents.py`
- **test_p2p3_grid_search.py**：删除三处测试函数的 `return dict`，消除 `PytestReturnNotNoneWarning`
- **最终 CI**：`python3 -m pytest -q` → 184 passed / 4 deselected / 264 warnings / 229s

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

# 完整 CI 回归（默认排除 network 标记，469 passed / 4 deselected / 1 warning，2026-05-23）
python3 -m pytest -q

# 或使用启动脚本
./start.sh
```

## 已知问题

1. **OKX错误11045**：设置杠杆偶发失败，不影响交易，可忽略
2. **Claude中转API偶尔被阻断**：系统自动降级为规则引擎，不影响交易
3. **个别标的资金费率API返回异常**（如UB-USDT）：已被try/except兜住，funding_rate回退None，不影响决策

## 开发注意事项

- 所有时间使用UTC
- 日志文件按日期分割
- 数据库自动创建表结构
- 配置文件修改后需重启
- API密钥为空时仅获取公开行情数据
