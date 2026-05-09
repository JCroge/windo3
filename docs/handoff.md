# 项目交接文档

## 项目状态

**开始日期**：2026-05-06
**当前阶段**：Phase 6g 完成 + 2026-05-09 Bug修复（做空信号、ticker格式、日线阈值）
**下一阶段**：Phase 7（资金费率API修复、Predictor、Paper Trading、更多数据源）

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
   - 开仓/平仓（reduceOnly参数）
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
   - 研判层每12小时触发，交易层持续运行
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

## 技术债务

1. **套利代码可以清理**
   - 套利相关代码已验证不可行
   - 可以保留作为参考，或移到archive目录

2. **低优先级问题**
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

# 启动实盘交易
python3 live_trading.py
```

## 文档位置

- **项目配置**：`CLAUDE.md`
- **架构设计**：`docs/architecture.md`
- **运维手册**：`docs/runbook.md`
- **集成指南**：`docs/integration-guide.md`
- **本文档**：`docs/handoff.md`

## 联系方式

如有问题，请查阅文档或检查日志文件。
