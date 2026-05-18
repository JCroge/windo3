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
| RESEARCH_INTERVAL | 研判层运行周期（秒） | 否（默认14400=4h） |
| MAX_ACTIVE_SYMBOLS | 最大同时交易标的数 | 否（默认5） |
| MAX_ACTIVE_SYMBOLS | 最大同时交易标的数 | 否（默认5） |
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

### 🔄 Phase 7: 待开发
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

1. **OKX错误11045**：设置杠杆偶发失败，不影响交易，可忽略
2. **Claude中转API偶尔被阻断**：系统自动降级为规则引擎，不影响交易
3. **个别标的资金费率API返回异常**（如UB-USDT）：已被try/except兜住，funding_rate回退None，不影响决策

## 开发注意事项

- 所有时间使用UTC
- 日志文件按日期分割
- 数据库自动创建表结构
- 配置文件修改后需重启
- API密钥为空时仅获取公开行情数据
