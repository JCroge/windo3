# 项目交接文档

## 项目状态

**开始日期**：2026-05-06
**当前阶段**：2026-06-10 默认回归基线 `1066 passed / 4 deselected / 1 warning`。第四次审计 F4-001/F4-002/F4-003 阻断在 2026-05-29 闭环，真实 OKX owner-tag 补验 T0/T1/T6 PASS；TG 新增 `/halts` `/resume_symbol` `/pnl` `/pnl_id`，Entry Drift Hybrid Policy 对 open 路径执行 4 档 drift gate，Pullback Entry Paper Parity 对齐 paper/live 限价撮合契约，Short Main Path Risk Guard Parity 把短单结构性风险 gate 收敛到 `Judge._classify_short_entry_risk` 单一函数（main + deferred 三路径共用）。2026-06-07 研究层低流动性硬过滤器上线（`MarketScanner._apply_liquidity_hard_filter`，volume+OI 双 gate、缺 OI fail-closed，BABY-USDT 事件根因），2026-06-10 补 OpenSpec change `2026-06-07-research-liquidity-hard-filter` + master spec `research-liquidity-filter` + verify 报告，完成流程闭环。2026-06-10 再上线两项（均走 comet/openspec 全流程归档）：Paper Dual-Track Simulation（PaperExecutor 加 `book ∈ {realistic, idealized}` 维度 + `/paper_gap`，`1010→1035`）与 Data Source Provenance（跨源 `source/freshness_sec/confidence` 穿透至 tech_analysis + Judge attribution + Reviewer 分桶，observability-only 决策零变更，`1035→1066`）。
**下一阶段**：live 扩容为 CONDITIONAL GO。扩容前需将 `BOT_INSTANCE_ID` 写入 systemd / pm2 等启动配置，完成真实 TG 命令链与 drift gate 运维验收，并继续每日复核 `data/live_position_lifecycle.json` 与 OKX algo 残留情况。

## 重大决策：放弃套利策略（2026-05-06）

经过全面测试验证，跨交易所套利策略在当前市场环境下不可行：

**验证结果：**
- REST API扫描：16分钟，122币种，196次检查 → 0次机会
- WebSocket实时监控：30分钟，30币种 → 0次机会
- 三角套利：565个组合 → 0次机会
- 深度验证：所有币种利润率为负

**原因分析：**
- 市场效率极高，价差被瞬间抹平
- 成本（手续费0.2% + 滑点0.1%）> 可获得的价差
- 高频交易公司占据优势（微秒级速度、VIP费率、机房托管）

**新方向：趋势交易 + 合约**
- 更适合我们的技术栈和资金规模
- 可以做多做空，机会更多
- 利用AI做市场分析和信号生成
- 参考用户的完整架构文档，采用MVP方式实施

## 已完成功能

### ✅ Phase 1: 套利策略验证（2026-05-06）

**数据基础设施：**
1. **行情聚合器** (`core/aggregator.py`) - 实时获取ticker数据
2. **套利检测引擎** (`core/detector.py`) - 计算跨交易所价差
3. **数据存储** (`utils/database.py`) - SQLite数据库
4. **日志系统** (`utils/logger.py`) - 按模块分离日志

**套利策略测试：**
1. **深度验证器** (`depth_validator.py`) - 验证orderbook深度
2. **市场扫描器** (`market_scanner.py`) - 122币种全市场扫描
3. **WebSocket监控** (`websocket_monitor.py`) - 实时价格监控
4. **三角套利检测** (`triangular_arbitrage.py`) - 565组合检测

**结论：套利策略不可行，已转向新方向。**

### ✅ 新方向：趋势交易系统（2026-05-06完成MVP核心）

1. **K线数据采集器** (`kline_collector.py`)
   - WebSocket实时订阅K线数据
   - SQLite存储（`data/klines.db`）
   - 支持多币种、多周期
   - 已验证：BTC/ETH 1分钟K线正常采集

2. **技术指标计算** (`indicators.py`)
   - MA、EMA、MACD、RSI、布林带
   - pandas向量化操作，高效计算
   - 静态方法设计，易于复用

3. **策略系统** (`strategy_base.py`, `optimize_1h.py`)
   - 参考Freqtrade架构的三步式策略基类
   - RobustStrategy稳健策略（4重入场确认）
   - 最佳参数：MA 7/25，RSI阈值75，成交量因子1.0

4. **回测引擎** (`backtest.py`)
   - 防前视偏差设计
   - 完整绩效指标（胜率、盈亏比、最大回撤）
   - 交易详情记录

5. **策略验证** (`compare_timeframes.py`, `validate_out_of_sample.py`)
   - 多时间周期测试：1小时最优
   - 样本外验证：测试集100%胜率
   - 反欺骗机制验证：胜率从46.67%提升至83.3%

### ✅ Phase 3: 实盘交易系统（2026-05-06完成）

1. **风控管理器** (`risk_manager.py`)
   - 余额/回撤/每日亏损限制
   - 止损止盈计算（多空双向）
   - 仓位计算（最多10%余额）
   - 峰值余额持久化（`data/risk_state.json`）

2. **合约执行器** (`executor.py`)
   - 基于CCXT的统一交易接口
   - 支持Binance和OKX
   - 杠杆设置（set_leverage）
   - OKX posMode-aware 参数构造（2026-05-25 完成）：启动期探测 posMode + `_build_okx_open_params` / `_build_okx_close_params` / `_build_okx_algo_params` 三入口构造器 + close/reduce 前 `availPos` 钳制 + 51169/51205/51112/51333 拒单状态复核
   - 止损止盈自动检查
   - 盈亏计算含杠杆倍数
   - 持仓持久化（`data/positions.json`）

3. **实时交易系统** (`live_trading.py`)
   - 整合策略+执行+风控
   - 实时K线获取（优先交易所，降级数据库）
   - 使用已闭合K线（iloc[-2]）
   - 60秒检查周期
   - 多空双向交易支持
   - 环境变量配置（USE_TESTNET, LEVERAGE）

4. **系统验证** (`verify_*.py`)
   - 基础验证：9/9通过
   - 交易Flow验证：6/7通过
   - OKX真实账户验证：5/5通过
   - 总计15/16测试通过

5. **关键问题修复** (`ISSUES.md`)
   - 11/12问题已修复
   - 参考Freqtrade和CCXT最佳实践
   - 合约交易实现、K线使用、风控逻辑、持久化等

6. **代码管理**
   - GitHub推送完成（commit: bef07d1）
   - 敏感信息已通过.gitignore排除
   - OKX真实账户连接成功（余额19.33 USDT）

### ✅ Phase 5: 多Agent系统（2026-05-07完成）

**Phase 5a - 基础框架**：

1. **消息总线** (`agents/message_bus.py`)
   - asyncio Queue进程内通信
   - 主题订阅 + 定向消息 + topic:symbol路由
   - 广播隔离（发送者不收自己的消息）

2. **Agent基类** (`agents/base.py`)
   - 生命周期管理（setup → run → stop）
   - 消息收发（publish/receive）
   - LLM调用接口（ask_claude/ask_claude_json）

3. **Claude API客户端** (`agents/llm_client.py`)
   - OpenAI兼容格式调用中转站
   - 限流、重试
   - JSON结构化输出

4. **编排器** (`agents/orchestrator.py`)
   - 两层架构生命周期管理
   - 研判层每4小时触发，交易层持续运行
   - 信号处理（SIGTERM优雅退出）

**Phase 5b - 研判层（6个Agent）**：

5. **MarketScanner** (`agents/research/market_scanner.py`)
   - OKX 324永续合约扫描
   - 指标：价格、24h量、波动率、涨跌幅、资金费率、多空比（Binance）、持仓量（OKX）
   - 按成交量排序，取Top50

6. **SentimentResearcher** (`agents/research/sentiment_researcher.py`)
   - Alternative.me恐贪指数（0-100，含7日趋势）
   - CoinGecko热门币种（社交热度Top15）
   - Binance Taker买卖比（10个主流标的）

7. **NewsResearcher** (`agents/research/news_researcher.py`)
   - 6家加密媒体RSS：CoinDesk、Cointelegraph、The Block、CryptoSlate、Decrypt、Bitcoin Magazine
   - 币种提及统计（35个已知币种）
   - 按发布时间排序，取最新30条

8. **Synthesizer** (`agents/research/synthesizer.py`)
   - 两阶段决策：初选（Claude综合分析）→ 终选（纳入言官谏言）
   - LLM不可用时规则降级选币
   - 保底机制：言官全部驳回时保留1个低置信度标的

9. **Censor** (`agents/research/censor.py`)
   - 言官/Devil's Advocate角色
   - 逆向思维审查：共识陷阱、利好出尽、时间窗口过期
   - 对每个标的给出：风险等级、反对理由、盲点、最坏情况、建议（accept/reject/reduce_size）

10. **SymbolRouter** (`agents/research/symbol_router.py`)
    - 标的路由：研判结果 → 交易层活跃标的列表
    - 轮换协议：新标的接入、旧标的发送平仓指令

**Phase 5b - 交易层（6个Agent，多标的并行）**：

11. **MultiDataCollector** (`agents/trading/multi_data_collector.py`)
    - 9维度采集：K线(1h/4h) + Orderbook 20档 + 资金费率历史(8期) + OI delta + 爆仓订单 + Taker买卖比 + 大单检测 + 多空账户比 + 10s价格流
    - 按频率分档：10s(ticker) / 30s(orderbook+爆仓) / 60s(全量) / 5min(4h K线)
    - 数据源：OKX REST API + Binance Futures Data API
    - K线连续性保障（缺口检测+补数据）+ 健康监控（连续失败告警）

12. **MultiTechAnalyst** (`agents/trading/tech_analyst.py`)
    - 9维度信号解读：趋势结构(含4h偏向) + 关键价位(swing high/low + orderbook墙) + 动量(RSI背离检测) + 资金流向(OI-价格背离/费率极值/Taker压力) + 微观结构(鲸鱼方向/深度偏向/爆仓强度) + 散户反指 + 风险评估(杠杆/波动/流动性)
    - 规则计算层（纯Python）+ LLM综合层（Claude识别跨维度组合模式）
    - LLM失败时规则降级输出完整结果

13. **MultiJudge** (`agents/trading/judge.py`)
    - 7维度加权评分（趋势25%/RSI背离15%/OI背离15%/鲸鱼15%/散户反指10%/Taker10%/高周期10%）
    - 精确交易计划：入场区间 + 基于支撑阻力的多级止盈止损 + 动态杠杆1-20x(三因子) + 仓位管理
    - 反欺骗/反人性决策：诱多陷阱识别、恐慌底部反人性做多、杠杆过热拒绝、主力洗盘不追空
    - 8个极端场景验证全通过

14. **MultiExecutor** (`agents/trading/executor.py`)
    - 智能执行：读Judge plan动态杠杆+限价单+条件单
    - Daily Hard Stop响应：收到熔断信号后拒绝新交易

15. **PortfolioRiskGuard** (`agents/trading/portfolio_risk_guard.py`)
    - 6维度风控 + risk_alert接入Executor强制平仓
    - 状态持久化：持仓追踪/价格缓存/熔断状态（data/riskguard_state.json）
    - Daily Hard Stop响应：收到熔断信号后全平持仓

16. **ReviewerAgent** (`agents/trading/reviewer.py`)
    - 交易历史追踪（data/trade_history.json）
    - 滚动窗口指标：胜率、盈亏比、连续亏损
    - 策略衰减检测：近期表现 vs 历史基线
    - Daily Hard Stop触发：单日亏损≤-50 USDT 或 连续3次亏损

**关键技术决策**：
- Claude中转API通过OpenAI兼容接口调用（绕过Cloudflare Bot防护）
- LLM不可用时自动降级为规则引擎
- 两阶段研判（初选+言官谏言+终选）防止过度自信
- 集成测试验证完整消息流水线（2/2通过）

## 待开发功能

### ✅ Phase 6a: Telegram通知（2026-05-07完成）

1. **TelegramNotifier** (`agents/trading/telegram_notifier.py`)
   - 实时推送：交易执行、critical级别风控告警、Daily Hard Stop触发
   - 每日摘要：UTC日切时自动发送（交易笔数/胜率/盈亏/告警次数）
   - 零配置降级：无TELEGRAM_BOT_TOKEN/CHAT_ID时自动禁用
   - Rate limiting：1 msg/sec 防止API限流
   - 交易层Agent数量：6→7

### ✅ Phase 6b: 关键Bug修复（2026-05-08完成）

1. **contractSize修复** (`executor.py`)
   - 修复前：`amount = (size_usdt * leverage) / price`（对DOGE contractSize=1000会多下1000倍）
   - 修复后：`amount = (size_usdt * leverage) / (price * contract_size)` + `amount_to_precision()`
   - 影响：DOGE/ETH等非1合约单位的标的下单数量正确

2. **Judge杠杆上限20x** (`agents/trading/judge.py`)
   - OKX允许值列表：[1, 2, 3, 5, 10, 20]
   - 高杠杆时RiskGuard高杠杆阈值同步为20

### ✅ Phase 6d: 方向决策修复（2026-05-08完成）

**根因**：之前所有交易亏损（-1.34 USDT）是因为在RSI极端超卖区域做空（DOGE RSI=20.1、ETH RSI=29.1）。`_compute_score`中趋势+鲸鱼+散户反指+Taker信号累加压过RSI背离的+15分。

**修复**（`agents/trading/judge.py` `_compute_score`方法重写）：
1. RSI极端值硬性保护：RSI<25时score不低于-15（禁空），RSI>75时score不高于15（禁多）
2. 趋势强度衰减：strength>90时 `effective_strength = 90 - (strength-90)*2`
3. 散户反指条件化：RSI极端区域禁用反指信号
4. RSI背离权重提升：极端区域+背离从+15提升到+35
5. JUDGE_PROMPT增加【关键禁令】：明确RSI禁区规则给LLM

**验证**：DOGE/ETH场景从错误的open_short变为正确的hold

### ✅ Phase 6e: Post-mortem修复 + 入场质量优化（2026-05-09完成）

**Post-mortem根因（2026-05-08四场全负）**：
1. `correlation_risk`用名义价值（4 USDT×20x=80 USDT）触发20 USDT阈值 → 每60s减仓50%循环 → 已修复为用保证金计算
2. Judge无force_close记忆 → 强平后立即重开同方向 → 已修复为300s冷却

**入场质量优化（参考Freqtrade/QuantConnect最佳实践）**：
1. **R:R门槛**（`judge.py`）：`risk_reward_ratio < 1.5` → 强制hold，赔率不足不入场
2. **负面催化剂否决**（`synthesizer.py`）：近4h内hack/exploit/监管等关键词 → confidence=0 → Censor自动reject
3. **Censor兜底**（`censor.py`）：规则降级时confidence<40 → reject
4. **30min新闻轮询**（`multi_data_collector.py`）：交易层每30min抓3家RSS，发布`news_snapshot`
5. **price-in检测**（`judge.py`）：近4h有新闻+价格已同向移动>3% → score×0.5（催化剂已消化）

### ✅ Phase 6g: Judge主驱动修复（2026-05-09完成）

- rule_signal±35基础分，确保MA交叉信号能过30分入场门槛
- LLM从一票否决改为仓位修正（rule_signal触发时最多降30%仓位）
- 无rule_signal时保持保守逻辑（LLM可否决弱信号）

### ✅ 2026-05-09 Bug修复

1. **做空信号修复**（`optimize_1h.py` `RobustStrategy`）
   - 修复前：只有`entry_long`，`entry_short`从未被赋值，系统无法做空
   - 修复后：新增做空4重确认（MA死叉+RSI>25+放量+价格下跌）和`exit_short`（MA金叉或RSI<20）

2. **PROS-USDT ticker格式修复**（`agents/trading/multi_data_collector.py`）
   - 修复前：`fetch_ticker('PROS-USDT')` → OKX报错 `does not have market symbol`
   - 修复后：`fetch_ticker('PROS/USDT:USDT')` 统一用永续合约格式

3. **日线阻力区阈值收紧**（`agents/trading/tech_analyst.py`）
   - 修复前：距20日高低点3%以内触发，横盘行情持续误触发导致信号被衰减
   - 修复后：1.5%以内才触发，只有真正贴近关键位时才衰减

### ✅ Phase 6h: MA alignment信号 + Symbol sync修复（2026-05-11完成）

1. **MA alignment信号**（`agents/trading/tech_analyst.py` + `agents/trading/judge.py`）
   - 根因：MA crossover是点事件，crossover后下一根K线`entry_short=0`，score≈0，系统永远hold
   - 修复：新增`ma_aligned_long/short`（MA fast/slow已对齐≥3根K线），Judge给±20基础分作为次驱动
   - 效果：LAYER-USDT score=-52.6，R:R=1.91，首次成功开仓

2. **Symbol sync修复**（`executor.py` `sync_positions`）
   - 根因：OKX返回`LAYER/USDT:USDT`，内部格式`LAYER-USDT-SWAP`，每次sync删除本地持仓再重建（SL/TP丢失）
   - 修复：sync_positions中将`BASE/USDT:USDT`格式自动转换为`BASE-USDT-SWAP`

3. **止损止盈计算修复**（`judge.py`，2026-05-13）
   - R:R硬性门槛1.5（不因confidence高而放松，修复LLM提升confidence绕过旧公式的漏洞）
   - SL距离ATR封顶：2.5×ATR，max 5%（Turtle Traders方法论，修复远距离结构性止损导致R:R天然不达标）
   - TP下限=SL×1.5（plan构建阶段保证R:R≥1.5）

### ✅ Phase 6i: 持仓管理三角决策 + flash_move修复（2026-05-12完成）

1. **PositionAnalyst**（`agents/trading/position_analyst.py`）
   - 6因子规则评分：趋势对齐(±20) + 动量变化(±20) + 时间衰减(-15~0) + 浮盈状态(±20) + 成交量确认(±10) + 剩余R:R(±15)
   - 每30分钟评估所有持仓
   - 5条硬性覆盖规则（浮亏>12%/持仓>48h+浮亏/趋势反转+浮亏>3%/浮盈>15%+动量反转/R:R<0.3）
   - 4级severity裁决矩阵（综合分析官建议 × 批判官偏差检测）

2. **BehavioralCritic**（`agents/trading/behavioral_critic.py`）
   - LLM检测7种认知偏差：loss_aversion/sunk_cost/anchoring/fomo/disposition/overconfidence/panic
   - LLM不可用时规则降级（基于浮盈/持仓时间/杠杆的简单检测）

3. **flash_move修复**（`executor.py` + `portfolio_risk_guard.py`）
   - 从全平所有持仓改为只平触发标的（单币闪崩≠系统性风险）

4. **Synthesizer扩容**（`synthesizer.py`）
   - 初选上限3→12，prompt更新为"5-12个"

5. **持仓监控补充**（`multi_data_collector.py`）
   - 新增`_get_position_symbols()`，自动将持仓标的纳入监控

6. **交易层Agent数量**：7→9（新增PositionAnalyst + BehavioralCritic）

### ✅ Phase 6j: 持仓管理防遗憾优化 + Telegram远程命令（2026-05-13完成）

1. **PositionAnalyst防遗憾优化**（`agents/trading/position_analyst.py`）
   - 评估周期30min→2h（减少过度干预）
   - 6因子→7因子：新增`entry_thesis_intact`（高时间框架方向保护，±25分）
   - 动量因子区分pullback vs reversal（MACD histogram趋势）
   - 时间衰减：盈利持仓豁免
   - 浮盈状态：杠杆感知正常波动范围 `min(5, leverage*0.5)`
   - 动作阈值放宽：reduce从-21→-31，close从-51→-61
   - 硬性覆盖放宽：loss>15%（原12%）、72h+3%loss（原48h+浮亏）、趋势反转需HTF确认+5%loss
   - 裁决引擎：趋势顺向时批判官close→reduce、reduce→hold

2. **BehavioralCritic防遗憾优化**（`agents/trading/behavioral_critic.py`）
   - 规则降级增加趋势方向验证：`trend_aligned`和`htf_aligned`
   - loss_aversion/sunk_cost只在趋势已反转时才标记
   - sunk_cost时间阈值24h→36h

3. **Telegram远程命令**（`agents/trading/telegram_notifier.py`）
   - getUpdates轮询（每5秒），只响应配置的chat_id
   - 7个命令：/status、/positions、/stop、/restart、/halt、/resume、/log
   - /stop和/restart通过消息总线发送system_command→Orchestrator触发优雅退出
   - /halt和/resume通过system_command→Executor切换熔断状态
   - /restart写入`data/.restart_flag`，run_agents.py检测后通过 `execv` 置换解释器镜像

4. **Orchestrator远程控制**（`agents/orchestrator.py`）
   - 注册system_command订阅 + _command_listener协程
   - 收到shutdown命令时触发优雅停机

5. **Executor远程熔断**（`agents/trading/executor.py`）
   - 订阅system_command，响应halt/resume切换_trading_halted

6. **run_agents.py重启循环**
   - while循环包裹Orchestrator.start()
   - 退出后检测`data/.restart_flag`，命中时执行 `os.execv(...)` 重新加载代码

### ✅ Phase 6k: 回调入场 + Censor超时修复 + Executor margin修复（2026-05-14完成）

1. **回调入场机制**（`agents/trading/judge.py`）
   - 问题：统一风险预算上线后，正确方向信号因R:R<1.5被拒（BASED R:R=1.18, TON R:R=1.39）
   - 三级响应：R:R≥1.5正常 / 1.2≤R:R<1.5强信号追价(仓位=rr/1.5) / 弱信号等回调(3h) / R:R<1.2放弃
   - deferred_entry状态机：每tick检查回调到位/追价触发/过期/趋势反转取消
   - 余额保护：触发时重新_build_plan，size_usdt<1.0则放弃
   - 理论基础：Al Brooks Signal/Entry Bar、Turtle Traders回踩确认

2. **Censor分批审查**（`agents/research/censor.py`）
   - 根因：Synthesizer扩容12标的后单次LLM调用超Cloudflare 100s网关超时
   - 修复：BATCH_SIZE=4分批处理，每批独立调用，失败则该批规则降级

3. **LLM客户端加固**（`agents/llm_client.py`）
   - 新增httpx.Timeout(connect=10, read=90, write=10, pool=10)
   - max_retries=2（OpenAI SDK内置指数退避）

4. **Executor required_margin修复**（`executor.py`）
   - 修复前：`required_margin = size_usdt / leverage`（错误，因size_usdt已是margin）
   - 修复后：`required_margin = size_usdt`（统一风险预算语义对齐）

5. **RiskGuard陈旧数据清理**（`data/riskguard_state.json`）
   - 清除13条已被SL/TP平仓但未从state中移除的持仓记录

### ✅ Phase 6l: HYPE重复做空事故修复（2026-05-15完成）

1. **5层防护**（`agents/trading/judge.py`）
   - 事故：HYPE-USDT在日线强上升趋势中被连续做空15+次
   - Fix 1：RSI背离在日线强趋势中降权（div_score从35降到15）
   - Fix 2：无rule_signal时入场门槛25→40
   - Fix 3：无rule_signal时LLM confidence上限55（方向确认boost到60）
   - Fix 4：开仓成功后300s冷却（防止止损后立即重开）
   - Fix 5：Executor开仓失败后120s冷却（防OKX报错刷屏）

2. **SL/TP方向校验**（`executor.py`）
   - 根因：Judge计算SL用决策时刻价格，Executor下单时价格已变动，导致做空SL<入场价
   - 修复：下单前校验方向，不合法时基于当前价重新设置默认距离

3. **PositionAnalyst评估周期**（`agents/trading/position_analyst.py`）
   - 2h→1h，更及时的持仓管理响应

### ✅ Phase 6m: 加仓/减仓功能修复（2026-05-15完成）

1. **加仓功能**（`executor.py` + `agents/trading/executor.py`）
   - 根因：PositionAnalyst发add信号(open_long/open_short)，Executor只在position=None时执行，已有持仓时静默丢弃
   - 修复：MultiExecutor新增`position is not None + source=position_analyst`分支 → `add_to_position()`
   - 加权平均入场价、SL/TP按原距离比例重算（Freqtrade stoploss_on_exchange_update模式）
   - 保证金上限：max_trade_amount×2，防止无限加仓

2. **减仓功能**（`executor.py` + `agents/trading/executor.py`）
   - 根因：PositionAnalyst发reduce信号(action=close, size_pct=0.5)，Executor忽略size_pct直接全平
   - 修复：`size_pct < 1.0 + source=position_analyst` → `reduce_position()`
   - 减仓前取消旧SL条件单（数量不匹配会被OKX拒绝）
   - 精度格式化 + 浮点兜底（剩余<min_amount视为全平）

3. **全系统execution_result同步**
   - 新增`is_add`标记：RiskGuard/PositionAnalyst增量更新而非覆盖
   - 新增`risk_reduced`状态：区分减仓和全平，下游按实际reduce_pct更新
   - TelegramNotifier：区分加仓(➕)/减仓(✂️)/全平通知

### ✅ Phase 6n: PA动态阈值 + Close冷却 + Telegram去重（2026-05-15完成）

1. **PA Rule 1/3b动态阈值**（`agents/trading/position_analyst.py`）
   - 事故：ZEC-USDT 10x杠杆，原价差1.5%被PA计算为-20.9%（含杠杆），触发固定15%阈值被误平
   - Rule 1修复：阈值=SL含杠杆距离（第三道防线，只在交易所SL+Executor轮询都失败时触发）
   - Rule 3b修复：阈值=SL距离×50%（替代固定10%，入场逻辑失效的早期信号）
   - 无SL时兜底：Rule 1=-30%，Rule 3b=-20%
   - 设计原则：PA不抢跑SL，三层防线各司其职

2. **Executor close冷却60s**（`executor.py`）
   - 根因：close_position后OKX API有延迟，sync_positions在延迟期间重新发现已平仓位→重建本地记录→再次被sync移除→循环
   - 修复：close_position后写入`_close_cooldown[symbol] = now + 60`
   - sync_positions中removed检测和newly_synced都检查冷却期

3. **Telegram通知去重**（`agents/trading/telegram_notifier.py`）
   - 问题1：sync发现的持仓推送"做多 置信度0%"刷屏 → 过滤source=sync
   - 问题2：closed_externally重复推送3次 → 同symbol 60s内去重
   - 加仓后SL更新：cancel旧SL + place新SL（数量和价格都变了）

### ✅ Phase 6o: Symbol格式统一修复（2026-05-15完成）

1. **根因**：系统内symbol格式不统一——DataCollector/TechAnalyst/Judge用`ZEC-USDT`，ContractExecutor positions dict用`ZEC-USDT-SWAP`
2. **后果**：`closed_externally`通知携带`-SWAP`格式 → Judge/PA/RiskGuard用错误key查state → 冷却无效、幽灵持仓不清除 → ZEC重复开仓3次
3. **修复**：execution_result handler入口strip `-SWAP`后缀 + deferred_entry触发即时冷却

### ✅ Phase 6p: PnL追踪 + 递增冷却 + 上线时间过滤（2026-05-17完成）

1. **closed_externally PnL追踪**（`executor.py` + `agents/trading/executor.py`）
   - 问题：交易所SL/TP触发时PnL记录为0，Daily Hard Stop无法检测真实亏损（14/28笔交易失明）
   - 修复：sync_positions保存被移除持仓完整数据 → `_estimate_close_pnl`优先用`unrealized_pnl`（~30s误差），降级用SL价格计算
   - 对标：Freqtrade `update_trade_stoploss_order_status` 始终计算close_profit

2. **递增冷却StoplossGuard**（`agents/trading/judge.py`）
   - 问题：AI-USDT 2h内7次连续SL，固定300s冷却不够（rule_signal持续触发）
   - 修复：4h滑动窗口计数，冷却递增 300→600→1200→3600s，窗口过期自动重置
   - 对标：Freqtrade `StoplossGuard` protection（trade_limit + timeframe）

3. **研判层上线时间过滤**（`agents/research/market_scanner.py`）
   - 问题：新币历史数据不足，技术分析不可靠
   - 修复：enrich前并行获取月K线数量，<12根（上线不足1年）的标的排除
   - 效率：在enrich之前过滤，节省4个API调用/标的

4. **初选固定12标的**（`agents/research/synthesizer.py`）
   - 修改：SYNTHESIS_PROMPT从"5-12个"改为"12个"，确保机会面充足

5. **Telegram启动flush旧消息**（`agents/trading/telegram_notifier.py`）
   - 问题：每次启动getUpdates从offset=0开始，重新处理历史/stop命令导致系统立即被杀
   - 修复：setup()中调用`_flush_old_updates()`跳过所有pending消息后再开始轮询
   - 验证：日志显示"启动时跳过5条旧消息"，系统不再被误杀

6. **终选prompt优化 + 代码保底**（`agents/research/synthesizer.py`）
   - 问题：12个初选 - 4个reject = 8个候选，但终选只出3个（LLM把warning也当reject处理）
   - Prompt修复：明确区分reject（移除）和warning/reduce_size（保留降置信度），要求≥5个
   - 代码保底：终选数量<非reject数量一半时，从初选中补充非reject标的（置信度×0.8）
   - 设计原则：对标Freqtrade max_open_trades——如果市场只有3个好标的就只交易3个，不强行凑数

7. **Logger防重复**（`utils/logger.py`）
   - 问题：每条日志打印7次（多次启动/停止累积handler + propagate到root logger）
   - 修复：`if logger.handlers: return logger` + `logger.propagate = False`

### ✅ Phase 7: 4h RSI 衰减 + 逻辑账户拆分 + Paper Trading（2026-05-19完成）

1. **4h RSI 二级保护**（`agents/trading/judge.py` `_compute_score` 末尾）
   - 根因：ZEC 事故——1h RSI=64（未触发硬cap），但 4h RSI=73.9 超买区，仍开多 20x → 单笔亏 -135 USDT
   - 修复：1h RSI 未触发硬cap但 4h RSI ≥70 且 score>0 时 score×0.5（4h ≤30 且 score<0 同样衰减）
   - 设计取舍：软衰减而非硬阻断，强趋势叠加 4h 超买仍可入场但 confidence 被压低（仓位自动减）
   - 测试：`test_4h_rsi_decay.py` 7/7 通过

2. **逻辑账户拆分**（`utils/config_loader.py` + `agents/trading/judge.py _calc_risk_budget`）
   - 目的：真实余额 6020 USDT 但风控按 1000 USDT 算，单笔 max_loss 从 250→50 与 Daily Hard Stop -50 对齐
   - 实现：新增 `EFFECTIVE_BALANCE_CAP` 环境变量，`_calc_risk_budget` 用 `min(real_balance, cap)`
   - 边界：cap=None 等价旧逻辑（向后兼容），cap<10 USDT 被 HARD_LIMITS 拒绝
   - 测试：`test_logical_account_split.py` 7/7 通过

3. **Paper Trading 全并行**（`agents/trading/paper_executor.py` 新建 ~340 行）
   - 设计：与 MultiExecutor 平行运行，订阅同样 `trade_decision:*` 和 `price_tick:*`
   - 隔离：不下任何真实订单，不查交易所，不订阅 `risk_alert` 和 `daily_hard_stop_triggered`
   - 独立 topic：发布 `paper_execution_result`（实盘是 `execution_result`），不污染下游
   - 持久化：`data/paper_positions.json` / `data/paper_equity.json` / `data/paper_trades.jsonl`
   - 初始 equity：`EFFECTIVE_BALANCE_CAP` 或 1000 USDT
   - 功能：open/close/add/reduce/SL/TP 自动触发/halt 阻塞/CostModel 一致手续费
   - PnL 公式与实盘对齐：`gross = margin × pnl_pct × leverage`，扣 entry+exit fee
   - 测试：`test_paper_executor.py` 9/9 通过（含 PnL 公式一致性 + topic 隔离）
   - 交易层 Agent 数：9→10

### ✅ 第五轮审计修复（2026-05-19完成）

1. **订单预检全覆盖**（`executor.py` `_execute_limit_order`）
   - 问题：限价单和限价超时 fallback 市价单未走 `precheck_order()`，OKX 侧才暴露最小张数/精度错误
   - 修复：limit 路径（line 753-761）与 fallback 路径（line 807-815）下单前都调用 `precheck_order(symbol, side, size_usdt, price, leverage)`
   - 5 个 `create_order` 落点全部覆盖：`_open_position`(166) / `open_position_with_plan` market(639) / limit(754) / fallback(808) / `add_to_position`(1094)
   - 参数语义一致：所有点都传 `size_usdt`（统一风险预算下=margin），内部 `notional = size_usdt × leverage`

2. **默认 pytest 干净 CI 口径**
   - `conftest.py:5` `collect_ignore = ["test_kline.py"]`：collect 阶段跳过依赖 websockets 且只含 `async def test()` 的非测试文件
   - `pytest.ini:5` `addopts = -m "not network"`：默认排除 network 标记的测试
   - `test_backtest.py` / `test_indicators.py` / `test_strategy.py`：加 `@pytest.mark.network`（依赖 `data/klines.db`，被 conftest tmp_path 隔离）
   - 历史基线：默认 `python3 -m pytest -q` → 184 passed / 3 deselected / 169s

3. **留尾（非阻塞）**
   - `_get_balance()` 对 MagicMock 经 `float()` 得 1.0：仅测试替身松散，生产路径走 `BalanceAdapter.get_total()` 返回真实 float
   - `test_event_backtest_real_data.py` 真实回测 PF=0.33：策略层瓶颈，下一阶段重点是把网格搜索推荐参数（`entry_threshold=25, rr_floor=1.8, cooldown=3, partial_tp=True`）放进 event_backtest 复验

### ✅ 第六轮审计修复（2026-05-19完成）

1. **test_kline.py 网络标记**（`test_kline.py`）
   - 问题：`async def test()` 依赖 `data/klines.db`，被 `conftest.py` 的 `monkeypatch.chdir(tmp_path)` 隔离后找不到文件
   - 修复：加 `@pytest.mark.network`，`conftest.py` 删除 `collect_ignore`（marker 已足够），默认 CI 变为 4 deselected

2. **`_get_balance()` 实数校验**（`agents/trading/executor.py`）
   - 问题：`BalanceAdapter.get_total()` 在极端情况下可能返回非实数（bool/None/inf）
   - 修复：`import math, numbers`；`isinstance(val, bool)` 或 `not isinstance(val, numbers.Real)` 或 `not math.isfinite(result)` 时返回 `-1.0` 并记录 error 日志
   - 测试修复：`test_executor_upgrade.py` + `test_full_pipeline.py` 中 `mock_exec.balance_adapter = None`（强制走 `fetch_balance` 路径，避免 MagicMock 触发实数校验）

3. **留尾（非阻塞）**
   - 真实回测 PF=0.33 仍待优化（策略层，非本轮范围）

### ✅ 第七轮审计修复（2026-05-19完成）

1. **event_backtest 权益曲线污染修复**（`event_backtest.py`）
   - 问题：开仓信号触发时，`equity_curve.append()` 在 `_open_position()` 之后执行，导致当前 K 线的权益快照已包含下一根才入场的仓位（前视偏差）
   - 修复：检测到入场信号后，先 `equity_curve.append()`（此时 position 仍为 None），再 `_open_position()`，最后 `continue` 跳过末尾的重复 append
   - 影响：max_drawdown 和 Sharpe 计算结果更准确，消除权益曲线的系统性高估

2. **PaperExecutor 原子写入**（`agents/trading/paper_executor.py`）
   - 问题：`_persist_state()` 用 `open(..., 'w')` 直接覆盖，进程崩溃时可能写出半截 JSON
   - 修复：改用 `from utils.atomic_io import atomic_write_json`（write-to-temp + rename，原子操作）

3. **live_trading.py DEPRECATED 标注**（`live_trading.py`）
   - 问题：旧入口绕过多 Agent 系统（PortfolioRiskGuard/Reviewer/PaperExecutor 等），误用风险高
   - 修复：docstring 首行加 `DEPRECATED: 此入口绕过多 Agent 系统。生产环境请使用 run_agents.py。`

4. **test_p2p3_grid_search.py 消除 PytestReturnNotNoneWarning**（`test_p2p3_grid_search.py`）
   - 问题：`test_grid_search_trending` / `test_grid_search_choppy` / `test_grid_search_robustness` 三个测试函数返回 dict，pytest 报 `PytestReturnNotNoneWarning`
   - 修复：删除三处 `return` 语句（assert 已足够，返回值无意义）
   - 历史结果：`python3 -m pytest -q` → 184 passed / 4 deselected / 264 warnings / 229s

### ✅ 最终审计收尾（2026-05-20）

1. **已闭环修复**
   - 15m 入场确认改为记录已闭合 K 线时间戳，避免用未收盘 15m K 线做入场判断
   - Judge Ranking 启用 Top-N 短窗口裁决，selected 后进入 `_pending_open_symbols`，收到 `execution_result` 后释放或确认
   - LiveLedger 加仓改为 `record_add()`，同一持仓生命周期维护加权均价、累计保证金和加仓次数
   - Reconciler 查询失败改为 `query_ok=False` 并返回告警，避免 API 不可用时误判“无偏差”
   - Telegram `/status` 读取 `HaltState.reason`，修复熔断状态展示字段错误
   - README 和 runbook 已同步当前实盘风控口径与主入口

2. **最终验证结果**
   - `PYTHONPYCACHEPREFIX=/private/tmp/crypto-arbitrage-pycache python3 -m compileall -q .` 通过
   - `PYTHONPYCACHEPREFIX=/private/tmp/crypto-arbitrage-pycache python3 -m pytest -q` → 263 passed / 4 deselected / 1 warning / 143.68s

3. **下次优先处理**
   - ~~Synthesizer 第 2 轮及以后可能丢弃先返回的 sentiment/news~~ → ✅ 已修复：按 `cycle_id` 分桶缓存，任一路可初始化桶，market_data 激活时恢复
   - ~~Executor 对 halt、cooldown、已有持仓等静默拒单路径应统一发布 rejected `execution_result`~~ → ✅ 已修复：所有拒单路径发布 rejected + Judge pending TTL 120s 自动释放
   - ~~`rank_flush_delay` 有默认值和硬限制，但缺 `RANK_FLUSH_DELAY` 环境变量映射~~ → ✅ 已修复：RANK_FLUSH_DELAY + MAX_CONCURRENT_POSITIONS 纳入 env_map/HARD_LIMITS/banner/runbook/.env.example
   - ~~PnL `utils.reconciliation.Reconciler` 已有单测，但尚未接入运行期定时告警~~ → ✅ 已修复：Executor tick 每 10min 执行对账，偏差发布 risk_alert

### ✅ 最终审计收尾第二轮（2026-05-20 晚）

1. **Synthesizer cycle 分桶**（`agents/research/synthesizer.py`）
   - 问题：第 2 轮及以后 sentiment/news 先到会被丢弃（cycle_id 不匹配当前 cycle）
   - 修复：新增 `_pending_by_cycle = {}` 按 cycle_id 分桶缓存；任一路数据可初始化桶；market_data 到达时激活该 cycle 并从桶恢复已到达数据；保留最新 2 个桶防内存泄漏
   - 测试：`test_synthesizer_cycle.py` 3 tests（sentiment先到不丢失 / 旧cycle challenge丢弃 / 桶清理保留最新2个）

2. **Executor 拒单事件 + Judge pending TTL**（`agents/trading/executor.py` + `agents/trading/judge.py`）
   - 问题：halt/reconciliation/cooldown/balance_fail/low_confidence 等拒单路径不发布 execution_result，导致 Judge pending 槽位永久占用
   - 修复 Executor：所有 open_long/open_short 拒单路径统一发布 `{"status": "rejected", "reason": "...", ...}`
   - 修复 Judge：新增 `_sweep_stale_pending()` 方法，在两处 `occupied` 计算前调用；超过 120s 未收到 execution_result 的 pending 自动释放
   - 测试：`test_ranking_slots.py` 新增 TTL sweep 测试（共 9 tests 全过）

3. **RANK_FLUSH_DELAY + MAX_CONCURRENT_POSITIONS 配置化**（`utils/config_loader.py`）
   - 新增 HARD_LIMITS：`max_concurrent_positions: (1, 20)`
   - 新增 DEFAULTS：`max_concurrent_positions: 3`
   - 新增 env_map：`RANK_FLUSH_DELAY` + `MAX_CONCURRENT_POSITIONS`
   - banner 新增"最大并发持仓"行
   - `.env.example` 和 `docs/runbook.md` 同步

4. **Reconciler 运行期接入**（`agents/trading/executor.py`）
   - 修复：`MultiExecutor.setup()` 初始化 `Reconciler(exchange, ledger)`
   - `tick()` 每 10min（`should_run(interval_sec=600)`）执行 `run_and_report()`
   - 偏差或 API 失败时发布 `risk_alert`（type: reconciliation_mismatch），Telegram 和 RiskGuard 自动接收

5. **验证结果**
   - 历史结果：`python3 -m pytest -q` → 373 passed / 4 deselected / 1 warning / ~186s
   - 1 flaky（test_phase_c.py MessageBus 单例状态泄漏，单独运行通过）
   - 系统已重启（PID 11132）

### ✅ Phase 8: 市场 Regime 优化（2026-05-21完成）

**问题**：28h 实盘 449 plans / 0 openings（R:R<1.5 全拦），long 66.7% win rate 被浪费，short 14.3% win rate 持续亏损。

**解决方案**：
1. **RegimeManager**（`utils/market_regime.py`）：基于 BTC/ETH bias + 全标的趋势共识计算 bullish/bearish/mixed/choppy，2 次确认切换 + 30min min_hold 防抖
2. **CounterfactualLedger**（`utils/counterfactual_ledger.py`）：被拒信号影子追踪，24h 内 TP/SL 解析，验证 regime 策略有效性
3. **Short Regime Guard**：牛市中普通做空被拦截，强做空（score≤-70, htf≥2, rr≥1.8, 15m confirm）放行
4. **Probe Short**：牛市中 BTC RSI 反转/breadth 恶化时允许小仓位探针做空（30% position, 3x leverage, 24h cooldown）
5. **Dynamic R:R**：牛市多头 1.30 / 牛市空头 1.80 / 默认 1.50
6. **Low R:R Extra Slot**：低 R:R 多头使用独立额外槽位，不挤占主槽位，rank score 打 70% 折扣
7. **全部 feature-flagged**：5 个 env 开关，关闭即回退原行为

**验证**：293 passed / 4 deselected / 0 failed

### ✅ R:R Floor Policy 修复（2026-05-26 完成）

**问题**：INJ-USDT 类信号（`effective_rr=1.45`, `score=45`, regime=choppy/mixed, trend=bullish, daily=bullish）被默认 `min_rr=1.50` 拦截。Judge 主开仓路径直接对比 `min_rr_threshold=1.5`，`_apply_regime_policy`（deferred 路径）又重写了一份 if/else，两边随时漂移；probe 路径硬编码 `1.30`；attribution 不带 R:R floor 来源，事后无法复盘"为什么这次走 1.50 而不是 1.30"。

**解决方案**：
1. **统一函数**（`agents/trading/judge.py: _select_rr_floor(action, plan, tech, score)`）：唯一入口，主路径与 `_apply_regime_policy` 共用，按顺序匹配 `probe` / `long_bullish_low_rr` / `long_aligned_low_rr` / `short_bullish_strong` / `default` 五个分支并返回 `(min_rr, rr_policy, rr_floor_reason)`。修改 R:R floor **必须改这一处**。
2. **新策略 `long_aligned_low_rr`**：mixed/choppy regime 下，仅当 `trend.direction=bullish` AND (`htf_bias=bullish` OR `daily_bias=bullish`) AND 未 `block_long` AND `|score|≥min_deferred_signal_score` 时使用 `RR_FLOOR_LONG_ALIGNED_CHOPPY=1.30`，进 low_rr_extra slot。
3. **不放宽空头**：mixed/choppy 空头默认仍 `RR_FLOOR_DEFAULT=1.50`；bullish 空头仍 `RR_FLOOR_SHORT_BULLISH=1.80`。
4. **probe 路径配置化**：`PROBE_RR_FLOOR=1.30` 替换硬编码，`_can_route_probe_short` / 主路径 / deferred 路径全部从同一函数取值。
5. **Attribution 全链路**：`trade_decision.attribution` 新增 `rr_floor_used` / `rr_floor_reason` / `symbol_trend` / `symbol_higher_tf_bias` / `symbol_daily_bias`；被拒决策同样带这五个字段，落 `data/journal/events_*.jsonl`。
6. **配置化**（`utils/config_loader.py`）：新增 `RR_FLOOR_LONG_ALIGNED_CHOPPY` / `PROBE_RR_FLOOR` / `LOW_RR_LONG_ALIGNED_ENABLED`，全部进 HARD_LIMITS + env_map + banner（启动 banner 显式打印五个 floor 当前值）。
7. **测试**：新增 `test_rr_floor_policy.py` 20 case，覆盖 AC-RR-01..09（config 默认、bullish 多头、choppy aligned 多头、choppy 非 aligned 拒绝、空头不放宽、bullish 强空头、probe 一致性、主路径 = deferred 路径、attribution 完整）。

**验证**：551 passed / 4 deselected / 0 failed（531 → 551，新增 20 个 R:R floor 单测）。

详见 `docs/rr_floor_policy_prd.md` / `docs/rr_floor_policy_acceptance.md`。

### ✅ Long Entry Position Guard（2026-05-26 完成）

**问题**：NEAR-USDT 2026-05-26 14:47:47 CST 通过 `long_bullish_low_rr` 在 range_pos=0.838、prev_daily=+15.66%、pre_12h=+0.33%、`effective_rr=1.36`、`rr_floor_used=1.30` 的山顶位置直接 `open_long`（`request_id=20260526-NEAR-5ead4ff9`，成交 2.778）。事后复算确认这不是 RSI 极端意义上的追高（RSI≈54），但价格已处在短期高位的趋势追多。当前系统对这种"位置过高但 RSI 中性"的多头入场缺少独立风控：`pending_pullback` 要求 RSI≥70，`deferred_15m_confirmation` 仅在 15m 过滤失败触发，`long_bullish_low_rr` 没有检查标的位置/前置涨幅。同时 EV bucket 在主路径中发生于 `plan.entry_type` 写入之前，bucket key 退化为 `unknown`，稀疏样本可能把负 EV 抬成正 EV。

**解决方案**：
1. **TechAnalyst 输入**（`agents/trading/tech_analyst.py`）：新增 `entry_context.{position_in_24h_range, pre_12h_return_pct, prev_daily_return_pct}`，保留 `short_context` 兼容；`prev_daily_return_pct` 取 `klines_1d[-2]` 的 `(close-open)/open`。
2. **统一函数**（`agents/trading/judge.py: _check_entry_position_policy(symbol, action, plan, tech, score, context)`）：long overheat 与 short side guard 的唯一入口，主开仓路径与 `deferred_15m_confirmation` / `deferred_pullback` / `deferred_chase` 三条 deferred 路径共用。修改任何 entry position 阈值 **必须改这一处**。
3. **触发阈值**：`range_pos>=0.82` → `long_overheat_range_pos`；`pre_12h>=0.05 ∧ range_pos>=0.75` → `long_overheat_pre_move`；`prev_daily>=0.10 ∧ range_pos>=0.75` → `long_overheat_daily_gain`。NEAR 案例命中 daily_gain（也命中 range_pos，因为 0.838≥0.82），优先返回 range_pos 标签。
4. **处理策略**：有效 target（`stop_loss < target < signal_price`，target = `max(stop_loss*1.005, signal*(1-max(LONG_LIVE_PULLBACK_MIN_PCT, atr_pct)))`）→ 创建 `deferred_pullback_overheat`（`chase_eligible=false`，timeout `LONG_LIVE_PULLBACK_TIMEOUT_HOURS=4`），等待回调后必须重新执行 HTF/15m/RR/EV/Entry Position Guard/slot gate 全套二次确认；target 无效 → 直拒 `long_overheat_no_valid_pullback_target`。
5. **Short side guard 主路径生效**：`open_short` 的 `range_position_too_low` / `pre_move_too_deep` / `rsi_too_low_for_short` 也由该函数返回，避免只在 `_apply_regime_policy` 中生效。
6. **EV bucket 修正**：`plan.entry_type` 前移到 `_check_expected_value` 之前，消除 `unknown` bucket key；新增 `EV_BUCKET_MIN_TRADES=10` / `EV_BUCKET_SPARSE_ALLOW_UPLIFT=false`，sparse bucket 禁止抬高 `p_win`，可降仓 / 缩仓（保留 phase2 EPIC D 强信号缩仓 60% 行为）。
7. **配置化**（`utils/config_loader.py`）：新增 9 项 `LONG_LIVE_*` 与 2 项 `EV_BUCKET_*` env，全部进 HARD_LIMITS + env_map + banner。
8. **Attribution 全链路**：`trade_decision.attribution` 新增 12 个 optional 字段：`entry_position_status` / `entry_position_block_reason` / `entry_range_pos_24h` / `entry_pre_12h_return_pct` / `entry_prev_daily_return_pct` / `entry_position_policy=long_overheat_v1` / `deferred_target_price` / `deferred_reason` / `ev_bucket_key` / `ev_bucket_trade_count` / `ev_bucket_min_trades` / `ev_bucket_sparse`；被拒决策同样带，落 `data/journal/events_*.jsonl`。
9. **回测同构**（`event_backtest.py`）：新增 `long_live_*` 构造参数与 `_check_entry_with_regime` overheat 检查；`prev_daily_return_pct` 列由 `close.pct_change(24)` 预计算。
10. **测试**：新增 `test_long_entry_position_guard.py` 23 case，覆盖 AC-LONGPOS-01..17（NEAR 复现、三组阈值触发、target 无效拒绝、chase 禁用、四路径一致性、short side guard 主路径生效、bucket key 真实、稀疏 bucket 不 uplift、trade_decision.v2 兼容、回测同构、配置 + banner、审计字段）。

**验证**：575 passed / 4 deselected / 0 failed（551 → 575，新增 23 个 Long Entry Position Guard 单测；phase2 sparse 缩仓回归用例同步保留）。

详见 `docs/long_entry_position_guard_prd.md` / `docs/long_entry_position_guard_acceptance.md`。

## 技术债务

1. **R:R计算已修复**（2026-05-13）
   - ✅ R:R硬性门槛1.5（不因confidence放松）
   - ✅ SL距离ATR封顶（2.5×ATR，max 5%）
   - ✅ TP下限=SL×1.5（plan构建阶段保证R:R≥1.5）
   - 剩余：4h swing支撑/阻力锚点缺失（部分标的SL用ATR fallback而非结构性价位）

2. **套利代码可以清理**
   - 套利相关代码已验证不可行
   - 可以保留作为参考，或移到archive目录

3. **低优先级问题**
   - 异常处理粒度可以更细（ISSUES.md #12）
   - 当前不影响核心功能

## 关键决策记录

### 方向转变（2026-05-06）

| 决策 | 原因 | 影响 |
|------|------|------|
| 放弃套利策略 | 所有测试0次机会，成本>收益 | 重新设计系统架构 |
| 转向趋势交易 | 更适合技术栈和资金规模 | 采用MVP方式，1-2周完成 |
| 使用合约交易 | 可以做多做空，机会更多 | 需要学习合约API |

### 技术选型

| 决策 | 选择 | 原因 |
|------|------|------|
| 交易所API库 | ccxt | 统一接口，支持200+交易所 |
| 数据库 | SQLite | 本地运行，无需额外安装 |
| 异步框架 | asyncio | Python内置，适合IO密集 |
| K线数据源 | Binance WebSocket | 实时、免费、稳定 |

## 已知问题

1. **价差不足**
   - ETH/USDT在主流交易所间价差极小
   - 解决方案：开发币种研判Agent寻找更好的标的

2. **无API密钥时的限制**
   - 只能获取公开行情
   - 无法执行交易
   - 解决方案：用户配置API密钥

## 环境配置

### 必需
- Python 3.9+
- pip3

### 依赖包
```
ccxt>=4.3.0
pandas>=2.0.0
python-dotenv>=1.0.0
pyyaml>=6.0.0
openai>=1.0.0
anthropic>=0.25.0
```

### 可选配置
- API密钥（执行交易时必需）

## 运行指南

```bash
# 安装依赖
pip3 install -r requirements.txt

# 配置环境变量（复制.env.example并修改）
cp .env.example .env
# 编辑.env文件，填入API密钥

# 系统验证
python3 verify_system.py          # 基础验证
python3 verify_trading_flow.py    # 交易Flow验证
python3 verify_okx_real.py        # OKX真实账户验证

# 启动实盘交易（生产入口）
python3 run_agents.py
# 或：./start.sh
# 注意：live_trading.py / main.py 已 deprecated，仅保留为单策略调试参考
```

## 文档位置

- **项目配置**：`CLAUDE.md`
- **架构设计**：`docs/architecture.md`
- **运维手册**：`docs/runbook.md`
- **集成指南**：`docs/integration-guide.md`
- **本文档**：`docs/handoff.md`

## 联系方式

如有问题，请查阅文档或检查日志文件。
