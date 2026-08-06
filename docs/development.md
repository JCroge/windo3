# 系统开发文档

本文档用于后续修改、审查和交接。目标是让任何一次改动都能快速定位链路、明确边界、知道该跑哪些验证，并避免把工程可运行误判为策略已盈利。

## 当前状态

截至 2026-08-06，系统主入口仍是多 Agent 交易系统：

```bash
python3 run_agents.py
```

`live_trading.py` 已标记为 deprecated，只保留给单策略调试参考。生产、paper、testnet、实盘验收都应走 `run_agents.py`。

当前工程链路已具备 paper/mock、Tactical V2 固定 `100U x 3` live 观察和 Sidecar resident monitor；OKX posMode 执行兼容、R:R Floor Policy、Long Entry Position Guard、分批止盈生命周期、审计整改、TG Graceful Ops、Entry Drift Hybrid Policy、Pullback Entry Paper Parity、Short Main Path Risk Guard Parity、Tactical Exit Track、保护单 halt recovery、V2 精确入口回查/自愈和 durable PnL replay 均已落地。最新全量回归为 `1878 passed / 4 deselected`。OKX 真实 testnet 语义验收：long_short_mode 子账户跑 T0-T15 13 PASS / 3 SKIP，net_mode 切换后单独跑 T0/T2/T3 3 PASS；第四次审计 owner-tag 补验 T0/T1/T6 PASS。当前不扩大 V2 容量、不恢复 Sidecar admission；收益目标仍未证明，真实事件回测需要持续验证，任何策略或风控改动都不能只用 mock 单测证明有效。当前功能域总览见 `docs/project-stage-summary.md`。

**热更新语义**：Telegram `/restart` 现在会让 `run_agents.py` 在优雅停机后执行 `os.execv(...)`，重新拉起 Python 解释器并重新 import 已修改的模块。`execv` 后 PID 可能不变，这是正常现象；判断是否换上新代码，应看启动日志和新行为。若变更的是 Python/venv/系统级依赖，仍建议 `kill -TERM $(pgrep -f run_agents.py)` 后 `nohup python3 run_agents.py &`。

## 目录职责

| 路径 | 职责 | 修改注意 |
|------|------|----------|
| `agents/orchestrator.py` | 两层 Agent 生命周期和研判触发 | 不要绕过 `MessageBus.reset()` 和优雅停机 |
| `agents/message_bus.py` | 进程内消息总线、优先级、背压、DLQ | 新消息类型要补优先级和测试 |
| `agents/research/` | 研判层：扫描、新闻、情绪、综合、言官、路由 | 输出统一走 `research_result` / `symbol_update` |
| `agents/trading/multi_data_collector.py` | 9维度行情采集 | 核心数据缺失必须阻止发布或标记 degraded |
| `agents/trading/tech_analyst.py` | 技术分析与规则信号 | 使用闭合 K 线，避免未闭合数据 |
| `agents/trading/judge.py` | 交易决策、风险预算、EV 门 | 策略公式改动必须同步回测 |
| `agents/trading/executor.py` | Agent 执行层，消费 `trade_decision` | 不能绕过风险和余额检查 |
| `executor.py` | CCXT 合约执行器 | 所有下单路径必须 precheck、幂等、可同步 |
| `agents/trading/portfolio_risk_guard.py` | 组合风控、强平告警 | 强风控消息要可追踪、可恢复 |
| `agents/trading/reviewer.py` | 交易复盘、Daily Hard Stop | 熔断逻辑必须保守，状态必须持久化 |
| `agents/trading/position_analyst.py` | 持仓复评、加减仓裁决 | 加仓/减仓要同步 Executor、RiskGuard、PA 状态 |
| `agents/trading/paper_executor.py` | 影子账户 | 不得调用真实交易所；状态用原子写 |
| `event_backtest.py` | 事件驱动回测 | 必须向线上 Judge 同构演进 |
| `utils/` | 配置、symbol、余额、成本、原子写等基础工具 | 优先复用，避免在业务模块重复造解析逻辑 |

## 核心链路

### 研判层

```text
research_trigger
  -> MarketScanner / SentimentResearcher / NewsResearcher
  -> ResearchSynthesizer 初选
  -> Censor 逆向审查
  -> ResearchSynthesizer 终选
  -> SymbolRouter
  -> symbol_update
```

要求：
- `selected` 为空时必须显式发布 no-op `symbol_update`，不能静默失败。
- 标的格式进入交易层前统一为内部格式 `BASE-USDT`。
- 轮换移除标的会触发平仓，后续如改为软退出，需要同步 PositionAnalyst / RiskGuard。

### 交易层

```text
symbol_update
  -> MultiDataCollector
  -> market_data:{symbol}
  -> MultiTechAnalyst
  -> tech_analysis:{symbol}
  -> MultiJudge
  -> trade_decision:{symbol}
  -> MultiExecutor + PaperExecutor
  -> execution_result:{symbol} / paper_execution_result:{symbol}
  -> Reviewer / PortfolioRiskGuard / PositionAnalyst / TelegramNotifier
```

要求：
- 跨 Agent 消息里的 `symbol` 使用 `BASE-USDT`。
- 交易所 API 调用现场转换：CCXT 用 `BASE/USDT:USDT`，OKX REST 用 `BASE-USDT-SWAP`。
- `trade_decision` 里的 `size_usdt` 语义是保证金，不是名义价值。
- 名义价值统一公式：`notional = size_usdt * leverage`。

### 风控闭环

```text
execution_result
  -> Reviewer 记录已完成交易
  -> Daily Hard Stop / consecutive losses
  -> daily_hard_stop_triggered
  -> Executor 停止新交易并全平
  -> RiskGuard 持久化熔断状态
```

```text
price_tick / market_data
  -> PortfolioRiskGuard
  -> risk_alert
  -> Executor 强制平仓或减仓
```

要求：
- Daily Hard Stop 和 `risk_alert` 属于高优先级/关键消息。
- 熔断后不能自动恢复，必须手动 `/resume` 或明确代码路径。
- 强平结果必须发布 `execution_result`，否则 Judge / Reviewer / PA 会保留幽灵状态。

## 开发原则

1. 先确定链路，再改代码  
任何改动先回答：输入消息是什么、输出消息是什么、状态写到哪里、失败时如何降级。

2. 单一语义，不重复解释  
余额、symbol、成本、订单能力、原子写已有工具层：
- `utils.symbol`
- `utils.balance_adapter`
- `utils.cost_model`
- `utils.order_capabilities`
- `utils.atomic_io`

业务模块应优先复用这些工具。

3. LLM 只能辅助，不能绕过硬闸  
LLM 输出必须经过 schema 校验、规则校验、风控校验。任何 prompt 优化都不能让 LLM 直接决定下单数量、跳过止损、绕过熔断。

4. 回测要尽量同构  
Judge 的评分、EV、SL/TP、冷却、延迟入场、成本模型一旦改变，必须同步 `event_backtest.py` 或抽成共享策略函数。不能只改线上逻辑。

5. 真实 API 参数必须 testnet 验证  
CCXT 的 `params` 是交易所相关扩展。`reduceOnly`、`clOrdId`、OKX attached TP/SL、trigger、cancel、fallback 都必须有 testnet 证据。

6. 状态文件必须可恢复  
持仓、风控、交易历史、paper 账户等 JSON 状态使用 `atomic_write_json()`。append-only 日志可用 jsonl，但要保证每行独立可解析。

## 修改规范

### 改 Agent

必须检查：
- `name` 是否唯一。
- `subscriptions` 是否匹配发布方 topic。
- 是否处理 `symbol` scoped 消息。
- `setup()` 是否只做初始化，不做长阻塞。
- `tick()` 是否有 sleep，不能忙等。
- 异常是否会被 `BaseAgent.run()` 捕获并可恢复。

新增消息类型时：
- 更新 `agents/message_bus.py` 的 `_PRIORITY_MAP`。
- 更新 `docs/development.md` 的消息契约。
- 添加路由测试或集成测试。

### 改 Judge / 策略公式

必须记录：
- 改了哪些输入因子。
- score 权重如何变化。
- 入场门槛是否变化。
- SL/TP/R:R/EV 是否变化。
- 对 long 和 short 是否对称。
- 对极端 RSI、HTF 反向、数据 degraded 的处理是否仍保守。

**R:R floor 改动专项约束**：
- 修改任何 R:R floor 必须改 `Judge._select_rr_floor(action, plan, tech, score)` 单一函数，**禁止**在调用点重新写 if/else 分支。
- `_select_rr_floor` 是主路径与 `_apply_regime_policy`（deferred 路径）的唯一入口，按顺序匹配 `probe` / `long_bullish_low_rr` / `long_aligned_low_rr` / `short_bullish_strong` / `default` 五个分支并返回 `(min_rr, rr_policy, rr_floor_reason)`。
- 新增分支必须同步：`utils/config_loader.py` 的 DEFAULTS / HARD_LIMITS / env_map / banner、`event_backtest.py` 的同构实现、`test_rr_floor_policy.py` 的 AC 覆盖、`docs/rr_floor_policy_prd.md` / `docs/rr_floor_policy_acceptance.md` 的 PRD 与验收。
- 不要放宽空头：`mixed/choppy` 空头默认仍 `RR_FLOOR_DEFAULT`；`bullish` 空头仍 `RR_FLOOR_SHORT_BULLISH`。
- attribution 必须带 `rr_floor_used` / `rr_floor_reason` / `rr_policy` / `symbol_trend` / `symbol_higher_tf_bias` / `symbol_daily_bias`，被拒决策也必须带，否则事后无法复盘。

**Long Entry Position Guard 改动专项约束**：
- 修改 long overheat / short side guard 阈值或处理策略必须改 `Judge._check_entry_position_policy(symbol, action, plan, tech, score, context)` 单一函数，**禁止**在 deferred helper（`_handle_pending` / `_apply_regime_policy`）里再写一份 overheat 判定。
- 该函数是主开仓路径与三条 deferred 路径（`deferred_15m_confirmation` / `deferred_pullback` / `deferred_chase`）的唯一入口；触发后若有有效回调目标进入 `deferred_pullback_overheat`（`chase_eligible=false`），否则直拒 `long_overheat_no_valid_pullback_target`。
- 新增阈值必须同步：`utils/config_loader.py` 的 DEFAULTS / HARD_LIMITS / env_map / banner、`event_backtest.py` 的同构实现、`test_long_entry_position_guard.py` 的 AC 覆盖、`docs/long_entry_position_guard_prd.md` / `docs/long_entry_position_guard_acceptance.md` 的 PRD 与验收。
- `plan.entry_type` 必须在 `_check_expected_value` 之前写入，避免 EV bucket key 退化为 `unknown`。
- 稀疏 bucket（`trade_count < EV_BUCKET_MIN_TRADES`）默认禁止抬高 `p_win`（`EV_BUCKET_SPARSE_ALLOW_UPLIFT=false`），降仓 / 缩仓仍允许。
- attribution 必须带 `entry_position_status` / `entry_position_block_reason` / `entry_range_pos_24h` / `entry_pre_12h_return_pct` / `entry_prev_daily_return_pct` / `entry_position_policy` / `deferred_target_price` / `deferred_reason` / `ev_bucket_key` / `ev_bucket_trade_count` / `ev_bucket_min_trades` / `ev_bucket_sparse`，被拒决策也必须带。

必须验证：
```bash
python3 test_risk_budget.py
python3 test_ev_gate.py
python3 -m pytest test_rr_floor_policy.py -q
python3 -m pytest test_long_entry_position_guard.py -q
python3 test_event_backtest.py
python3 test_event_backtest_real_data.py
```

如果真实事件回测变差，需要在提交说明里解释原因，不能只看 mock 单测。

### 改 Executor / API 下单

所有 `create_order()` 前必须确认：
- symbol 已转换为交易所可接受格式。
- amount 已按 `amount_to_precision()` 处理。
- 已通过 `OrderCapabilities.precheck_order()` 或有等价本地限制。
- OKX 下单带 `clOrdId`，避免重复提交。
- 平仓/减仓必须通过 OKX posMode 参数构造器生成 `posSide` / `reduceOnly`，禁止业务路径手写固定 `reduceOnly`。
- 下单失败不会重复开仓。
- 成功/失败都能形成下游可理解的 `execution_result`。

建议验证：
```bash
python3 test_executor_upgrade.py
python3 test_p1m_order_caps.py
python3 test_paper_executor.py
python3 test_full_pipeline.py
```

testnet 验收另行执行，不用 mock 代替。

### 改 DataCollector / 数据源

必须检查：
- 核心数据 K 线和最新价是否完整。
- 缺维度是否标记 `data_quality.degraded`。
- 跨交易所数据是否标记来源。
- 网络失败是否不会让下游高置信度开仓。

数据类真实网络测试必须标记：
```python
@pytest.mark.network
```

### 改 Paper / Backtest

PaperExecutor：
- 不得调用真实交易所。
- 不得消费真实 Executor 内部对象。
- 状态必须原子写。
- 如果要镜像实盘风控，需要显式配置，不要默认耦合。

EventBacktest：
- 不允许未来函数。
- 信号 K 线和入场 K 线必须分清。
- equity curve 不得包含尚未发生的未来入场。
- 费用、滑点、资金费率应使用 `CostModel`。

## 验证矩阵

### 默认回归

```bash
python3 -m pytest -q
```

当前默认 pytest 排除 `network` 标记的外部依赖测试。

### 语法检查

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .
```

### 核心链路

```bash
python3 test_full_pipeline.py
python3 test_executor_upgrade.py
python3 test_p1m_order_caps.py
python3 test_llm_schema.py
python3 test_paper_executor.py
python3 test_risk_budget.py
```

### 系统验证

```bash
python3 test_full_verification.py
```

说明：Layer 7 依赖真实 OKX/Telegram 网络环境，失败时需要区分代码问题和环境问题。

### 收益验证

```bash
python3 test_event_backtest.py
python3 test_event_backtest_real_data.py
python3 test_p2p3_grid_search.py
```

收益验证结论优先级：
1. 真实多标的 walk-forward
2. 真实单标的多窗口
3. 当前真实 BTC 1h
4. 合成行情网格搜索
5. mock 单测

不能用低优先级结果覆盖高优先级反证。

## 状态文件

文件路径由 `utils/state_paths.py` 单一真相源派生（FR-008，2026-05-28）。命名空间优先级：`STATE_NAMESPACE=live|testnet|paper` > `USE_TESTNET=true` 推断 testnet > 默认 live。live 默认完全兼容历史路径；testnet/paper 自动加 `testnet_` / `paper_` 前缀。新增状态文件必须通过 `get_state_paths()` 读取默认值；显式参数仍可覆盖（测试或运维场景）。

| 文件 | 写入者 | 说明 |
|------|--------|------|
| `data/positions.json` | `ContractExecutor` | 实盘持仓快照 |
| `data/risk_state.json` | `RiskManager` | daily pnl / peak balance |
| `data/halt_state.json` | `HaltState` | 全局熔断状态（加载损坏 fail-closed） |
| `data/trade_history.json` | `ReviewerAgent` | 已完成交易历史 |
| `data/riskguard_state.json` | `PortfolioRiskGuard` | 风控追踪状态和熔断状态 |
| `data/live_order_events.jsonl` | `LiveLedger` | 订单事件流 append-only |
| `data/live_position_lifecycle.json` | `LiveLedger` | 持仓生命周期聚合 |
| `data/paper_positions.json` | `PaperExecutor` | 影子账户持仓 |
| `data/paper_equity.json` | `PaperExecutor` | 影子账户权益 |
| `data/paper_trades.jsonl` | `PaperExecutor` | 影子账户已完成交易 |
| `logs/llm_audit_YYYYMMDD.jsonl` | `LLMClient` | LLM 输入输出审计 |

开发测试应尽量通过 `conftest.py` 隔离到临时目录，不要污染真实 `data/`。

## 环境变量

以 `.env.example` 为准。关键项：
- `EXCHANGE=okx`
- `USE_TESTNET=true|false`
- `STATE_NAMESPACE=live|testnet|paper`（覆盖 USE_TESTNET 推断；默认按 USE_TESTNET 推断；未设且 USE_TESTNET=false 时为 live）
- `MAX_TRADE_AMOUNT`
- `MAX_DRAWDOWN_PCT`
- `MAX_DAILY_LOSS` 或 `DAILY_PNL_HARD_STOP`
- `CONSECUTIVE_LOSS_LIMIT`
- `EFFECTIVE_BALANCE_CAP`
- `ANTHROPIC_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Live 模式缺少交易所凭证应拒绝启动。测试场景可使用 `load_config(strict_live_check=False)`。

## 已知边界

1. 收益目标尚未被真实回测证明  
工程链路可跑通不代表日化 1%~5% 已达成。策略放大前必须有真实数据和 paper/testnet 证据。

2. OKX attached TP/SL testnet 矩阵已 PASS（2026-05-27）  
真实 testnet 7 PASS / 3 SKIP，covered SL 替换 / algo 迁移 / attachAlgoClOrdId 回查 / 51169 拒单 / reduceOnly close。后续如改动 attached TP/SL / algo 迁移 / cancel_orders 路径仍需重跑 `verify_okx_testnet_real.py`。mock 仍是必要前置（`verify_okx_testnet_semantics.py`），但**不能**单独证明交易所接受。

3. 进程内 MessageBus 不做持久化  
关键事件依赖日志和状态文件恢复。后续如进入长期实盘，应考虑事件 ledger。

4. 回测同构仍是长期主线  
线上 Judge 逻辑复杂，`event_backtest.py` 必须持续向线上逻辑收敛，或抽出共享 StrategyPolicy。

## 提交前清单

改代码前：
- 明确改动链路和状态边界。
- 查找是否已有工具函数可复用。
- 标记是否影响实盘下单、风控、收益验证。

改代码后：
- 跑与改动相关的最小测试。
- 若影响交易决策，跑事件回测。
- 若影响下单 API，准备 OKX testnet 验收。
- 更新 README 或 docs。
- 不删除用户已有数据和日志，不重置工作区。
