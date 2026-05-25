# Crypto Trading System - AI 协作指南

## 当前事实

- 当前系统是多 Agent 加密货币趋势交易系统，不是跨交易所套利系统。
- 生产、paper、testnet、实盘验收主入口统一为 `python3 run_agents.py`。
- `main.py` 和 `live_trading.py` 是归档/调试路径，不能作为生产入口。
- 2026-05-25 自动化基线：`531 passed / 4 deselected / 1 warning`（含 `test_okx_posmode_executor.py` 38）。
- OKX mock 执行语义验收 10 case PASS（含 posMode close 矩阵 + 拒单状态复核）；OKX 真实 testnet 语义验收未执行，阻断 live 扩容。
- 当前待办统一看 `docs/to-do-list.md`，审计报告看 `docs/generated_reports/系统性审计报告_20260524.md`。

## 快速命令

```bash
python3 run_agents.py
./start.sh
env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .
python3 -m pytest -q
python3 verify_okx_testnet_semantics.py
```

默认 pytest 通过 `pytest.ini` 排除 `network` 标记测试；真实 OKX/Telegram 冒烟依赖本机网络和凭证。

## 目录职责

| 路径 | 职责 |
|---|---|
| `run_agents.py` | 多 Agent 系统启动入口，支持远程重启标记 |
| `agents/orchestrator.py` | Agent 注册、生命周期、research loop、优雅停机 |
| `agents/message_bus.py` | 进程内消息总线，优先级、背压、DLQ、关键 topic journal |
| `agents/research/` | 研判层：扫描、情绪、新闻、综合、言官、标的路由 |
| `agents/trading/multi_data_collector.py` | 9 维度行情采集，多频率数据发布 |
| `agents/trading/tech_analyst.py` | 技术分析与规则信号，不直接下单 |
| `agents/trading/judge.py` | open 决策 owner，R:R、EV、ranking、slot gate、request_id |
| `agents/trading/executor.py` | Agent 执行层，消费 `trade_decision`，发布 `execution_result.v2` |
| `executor.py` | 底层 CCXT 合约执行器，OKX 订单/仓位/SL/TP 语义集中点 |
| `agents/trading/paper_executor.py` | 影子账户，不下真单，发布 `paper_execution_result` |
| `agents/trading/reviewer.py` | live 交易复盘、segmented metrics、Daily Hard Stop |
| `agents/trading/portfolio_risk_guard.py` | 组合风控、持仓追踪、risk alert |
| `agents/trading/position_analyst.py` | 持仓复评、close/reduce/add 裁决 |
| `agents/trading/behavioral_critic.py` | 行为偏差检测，当前字段契约待统一 |
| `utils/` | 配置、symbol、exchange factory、halt state、对账、事件日志等基础设施 |
| `docs/` | 架构、runbook、验收、审计报告和待办 |

## 核心 Flow

Research:

```text
research_trigger
  -> MarketScanner / SentimentResearcher / NewsResearcher
  -> ResearchSynthesizer preliminary
  -> Censor
  -> ResearchSynthesizer final
  -> SymbolRouter
  -> symbol_update
```

Trading:

```text
symbol_update
  -> MultiDataCollector
  -> market_data / price_tick / news_snapshot
  -> TechAnalyst
  -> tech_analysis
  -> Judge
  -> trade_decision.v2
  -> MultiExecutor + PaperExecutor
  -> execution_result.v2 / paper_execution_result
  -> Reviewer / RiskGuard / PositionAnalyst / TelegramNotifier
```

Holding:

```text
execution_result + tech_analysis + price_tick
  -> PositionAnalyst
  -> position_review
  -> BehavioralCritic
  -> position_verdict
  -> PositionAnalyst arbitration
  -> trade_decision close/reduce/add
```

Risk:

```text
Reviewer / RiskGuard
  -> daily_hard_stop_triggered / risk_alert
  -> Executor halt / close / reduce
  -> HaltState + reconciliation
```

## 消息契约红线

- 跨 Agent symbol 使用内部格式 `BASE-USDT`；交易所 API 调用现场转换。
- open 主链路必须走 `trade_decision.v2`，字段包括 `schema_version`、`request_id`、`action`、`confidence`、`plan`、`dispatch_path`、`attribution`。
- Executor 所有终态必须发布 `execution_result.v2`，字段包括 `schema_version`、`status`、`action`、`symbol`、`source`、`request_id`、`correlation_id`、`reason`、`result`、`timestamp`。
- `paper_execution_result` 与 live `execution_result` 隔离，不能污染 live Reviewer 指标。
- `trade_decision.plan.size_usdt` 是保证金，不是名义价值；名义价值为 `size_usdt * leverage`。
- LLM 只做辅助信号，不能绕过规则、R:R、EV、余额、熔断、订单预检和执行终态。

## 风控红线

- 扩大 live 前必须完成 OKX 真实 testnet 语义验收。
- 熔断恢复的最终 owner 是 Executor；Telegram 只发请求和展示结果。
- `HaltState` 加载损坏必须 fail-closed，不允许默认恢复交易。
- `RiskGuard`、Executor、交易所、Paper 状态对账中，live 阻断问题必须阻止 `/resume`；paper/live mismatch 默认 advisory。
- close/reduce 不应被开仓风控阻断；open/add 必须经过余额、回撤、slot、订单能力预检。
- 修改 Judge / 策略公式必须同步事件回测或补同构测试，不能只看 mock 单测。

## Exchange 规则

- 当前实盘和 testnet 验收以 OKX USDT 永续为主。
- 所有新建 exchange client 优先走 `utils.exchange_factory.create_exchange()`。
- `USE_TESTNET=true` 时必须在任何 API 调用前启用 sandbox/testnet。
- `executor.py` 底层仍直接创建 ccxt，但必须保持构造期设置 sandbox；后续应收敛到 factory。
- Binance path 视为 legacy，不能假设具备与 OKX `attachAlgoOrds` 相同语义。

## LLM 规则

- 所有 LLM JSON 调用应传 schema，并记录 validation errors。
- `BehavioralCritic` 当前待统一 `counter_action/confidence` 与 `counter_recommendation/confidence_in_challenge` 字段。
- LLM audit 会记录截断后的 user message 和 raw response；涉及账户、订单或策略敏感信息时需先做脱敏设计。
- LLM 不可用时必须规则降级，不能中断交易关键链路。

## 文档入口

| 文档 | 用途 |
|---|---|
| `README.md` | 项目入口和当前状态 |
| `docs/to-do-list.md` | 当前阻断项、后续优化、已关闭事项 |
| `docs/generated_reports/系统性审计报告_20260524.md` | 最新系统性审计报告 |
| `docs/runbook.md` | 运维命令、环境变量、故障处理 |
| `docs/development.md` | 开发边界、flow、验证矩阵 |
| `docs/integration-guide.md` | 下游集成和消息契约 |
| `docs/architecture.md` | 架构与历史演进 |
| `docs/handoff.md` | 长历史交接记录 |

## 禁止事项

- 不要把 `main.py` / `live_trading.py` 写回生产入口。
- 不要删除或覆盖用户已有 `data/`、`logs/`、`.env`。
- 不要把 paper/mock 通过写成 OKX testnet/live 语义通过。
- 不要在未执行 OKX testnet 验收时升级 ccxt 后直接 live。
- 不要在没有 `request_id` / `execution_result.v2` 的路径里新增 open 执行动作。
- 不要把历史流水继续追加到本文件；历史写入 `docs/handoff.md` 或审计报告。
