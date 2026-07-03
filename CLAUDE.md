# Crypto Trading System - AI 协作指南

## 当前事实

- 当前系统是多 Agent 加密货币趋势交易系统，不是跨交易所套利系统。
- 生产、paper、testnet、实盘验收主入口统一为 `python3 run_agents.py`。
- `main.py` 和 `live_trading.py` 是归档/调试路径，不能作为生产入口。
- **⚠️ 2026-07-03 关键运营状态**：(a) LLM 中转端点当前 `.env` 为 `BOT_LLM_BASE_URL=https://api.chivess.com` / `BOT_LLM_MODEL=gpt-5.5`，15:05 后出现多次 504，影响新决策样本产出。(b) **体制分类 weighted_total follow-up 已部署**：97825a1 后续发现 `anchor_neutral_weight` 未进 `weighted_total`，已在 commit `08a7552` 修复为 `weighted_total = weighted_bullish + weighted_bearish + weighted_neutral` 并补回归测试；live 已清理为单进程，当前由 `screen` 会话 `crypto_live` 承载，Python PID 24714，2026-07-03 15:05:11 OS 层重启加载。(c) 体制空仓硬门仍生效（env `REGIME_FLAT_GATE_ENABLED=false` 可回滚）。
- 当前基线：`1474 passed / 0 failed / 4 deselected`（2026-06-26）。历史基线见 `docs/handoff.md`。
- 当前 Go/No-Go：小额 live 灰度 GO（维持现有 cap）；live 扩容 CONDITIONAL GO，扩容前置 = 运维 SOP 把 `BOT_INSTANCE_ID` 写入 systemd / pm2 启动配置 + 真实 TG 命令链与 drift gate 运维验收。
- OKX 验收状态：mock 执行语义 10 case PASS；真实 testnet long_short_mode 13 PASS + net_mode 子账户 3 PASS。
- TG 命令清单：`/status /positions /halt /resume /force_resume /reconcile /halts /resume_symbol /pnl /pnl_id /stop /restart /log /paper_gap /health`。
- 各特性的单点收口函数与硬约束见下方「风控红线」；当前待办看 `docs/to-do-list.md`，最新审计报告看 `docs/generated_reports/系统性审计报告_20260610_第五次.md`，完整历史演进与逐基线里程碑看 `docs/handoff.md`。

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

- 跨 Agent symbol 使用内部格式 `BASE-USDT`；交易所 API 调用现场转换。消费侧落记前必须经 `utils/symbol.py::to_internal` 归一。
- open 主链路必须走 `trade_decision.v2`，字段包括 `schema_version`、`request_id`、`action`、`confidence`、`plan`、`dispatch_path`、`attribution`。
- Executor 所有终态必须发布 `execution_result.v2`，字段包括 `schema_version`、`status`、`action`、`symbol`、`source`、`request_id`、`correlation_id`、`reason`、`result`、`timestamp`。
- `paper_execution_result` 与 live `execution_result` 隔离，不能污染 live Reviewer 指标。
- `trade_decision.plan.size_usdt` 是保证金，不是名义价值；名义价值为 `size_usdt * leverage`。
- LLM 只做辅助信号，不能绕过规则、R:R、EV、余额、熔断、订单预检和执行终态。

## 风控红线

### 基础安全
- 扩大 live 前必须完成 OKX 真实 testnet 语义验收。
- 熔断恢复的最终 owner 是 Executor；Telegram 只发请求和展示结果。
- `HaltState` 加载损坏必须 fail-closed，不允许默认恢复交易。
- `RiskGuard`、Executor、交易所、Paper 状态对账中，live 阻断问题必须阻止 `/resume`；paper/live mismatch 默认 advisory。
- close/reduce 不应被开仓风控阻断；open/add 必须经过余额、回撤、slot、订单能力预检。

### 单点收口函数（MUST 修改的唯一入口）
- **仓位同步补录**：`executor.sync_positions` 双确认机制（`position_resync_confirm_ticks`=2，防幽灵持仓）。详见 `docs/superpowers/specs/2026-06-20-fix-phantom-position-resync-design.md`。
- **R:R floor**：`Judge._select_rr_floor` 单一函数，返回 policy 标签（probe/long_bullish_low_rr/long_aligned_low_rr/long_aligned_path_evidence/short_bullish_strong/default）。
- **lever2 阶梯 vs 低 R:R 缩仓解耦**：`Judge._compute_ladder_rr` 计算阶梯加权（只用于地板 gate）；`Judge._apply_low_rr_sizing` 单一收口缩仓判定（必须用 TP1 口径 `effective_rr_tp1`）。详见 `docs/superpowers/specs/2026-06-17-trend-entry-levers-default-on-design.md`。
- **Long Entry Position Guard**：`Judge._check_entry_position_policy` 单一函数，主路径与三条 deferred 路径共用。体制感知阈值经 `Judge._resolve_long_range_thresholds(eff_regime)` 取值（choppy/mixed/bearish 收紧至 0.70，config `long_live_regime_aware_range_enabled`）。详见 `docs/superpowers/specs/2026-06-21-regime-aware-long-entry-guard-design.md`。
- **体制空仓硬门**：`Judge._classify_regime_flat_gate(action, plan, tech, score) -> (allow, reason)` 单一收口（long-only，choppy/mixed + 无方向论据 → 拒 open_long）。方向论据 = `_has_directional_thesis` = `aligned OR path_evidence_raw`（ungated）。4 处调用点（主 + 三 deferred），config `regime_flat_gate_enabled` 默认 True。详见 `docs/superpowers/specs/2026-06-25-fix-open-direction-regression-choppy-flat-gate-design.md`。
- **保护单 owner**：`ContractExecutor.move_protective_sl(symbol, new_sl, reason=...)` 唯一入口。详见 `docs/audit_remediation_20260528_acceptance.md` §8.1。
- **close path 保护单清理**：`executor.close_position(symbol)` → `_cleanup_protective_orders_on_close()` 自动完成，禁止直接 `cancel_order(sl_order_id)`。
- **execution_result.v2 close cause**：`_classify_close_cause(source, reason)` 单一函数生成 `exit_reason/close_cause/is_strategy_stop/is_risk_forced`。
- **已实现 PnL 账本**：`utils/realized_pnl_resolver.py` 唯一解析入口（状态集合 final/pending/estimated/mismatch/pending_fx）。`utils/live_ledger.record_pending_external_close()` + `apply_pnl_resolution()`，Reviewer/Judge 必须按 `pnl_is_final=True` 守门。详见 `docs/exchange_realized_pnl_ledger_prd.md`。
- **Entry drift**：`executor._classify_entry_drift` 单一函数，Gate 1 和 Gate 2 共用（Gate 2 基准始终原 `plan.entry_ref`）。详见 `docs/superpowers/specs/2026-06-01-entry-drift-hybrid-policy-design.md`。
- **Position TP 字段**：`_set_position_tp(position, tp_first, tp_levels)` 单一收口，保证 `position.take_profit == position.take_profit_levels[0]`。
- **Paper limit 撮合**：`_pending_limits` 队列 + `_wait_paper_limit_fill` + `_scan_pending_limits`。`_open_paper_at_price` 唯一创建函数（携带 `entry_method`）。详见 `docs/superpowers/specs/2026-06-03-pullback-entry-paper-parity-design.md`。
- **短单结构性风险 gate**：`Judge._classify_short_entry_risk` 单一函数（main path 与三 deferred 共用）。LLM 关键词只写 `llm_short_reversal_risk=true`，最终拒单驱动必须是结构性原因。详见 `docs/superpowers/specs/2026-06-05-short-main-path-risk-guard-parity-design.md`。
- **研究层流动性硬过滤**：`MarketScanner._apply_liquidity_hard_filter` + `_liquidity_rejection_reason` 单一函数（`volume_24h` 与 `open_interest_usd` 双 gate，缺 OI 必须 fail-closed）。详见 `docs/superpowers/specs/2026-06-07-research-liquidity-hard-filter-design.md`。
- **Paper 双轨账本**：`book ∈ {realistic, idealized}` 维度单点收口 `self._books[book]`。对比层 `paper_dual_track_report.py` 是 paper-only 纯函数，严禁被 live Reviewer 消费。详见 `docs/superpowers/specs/2026-06-10-paper-dual-track-sim-design.md`。
- **数据源 provenance**：`utils/data_provenance.py::derive_confidence` 单函数派生。provenance 是 **observability-only metadata**（write-only，严禁任何 gate/rank/veto/halt/daily-stop 读取）。详见 `docs/superpowers/specs/2026-06-10-data-source-provenance-design.md`。
- **Agent 健康聚合**：`utils/health_snapshot.py::build_health_snapshot` 单一纯函数派生四维度（loop-alive/queue backlog/LLM degraded/data degraded）。健康快照是 **observability-only write-only**，严禁任何 gate/rank/veto/halt/daily-stop 读取。详见 `docs/superpowers/specs/2026-06-12-agent-health-supervisor-design.md`。

### Observability-only 产物（write-only，严禁决策路径读取）
守卫测试：`tests/test_cf_red_line_guard.py`

- **反事实回放产物**（决策磁带 `decision_replay_tape.jsonl` / 反事实 PnL `utils/counterfactual_pnl.py` / 1s tick `klines_1s.db`）。Judge 写决策磁带允许，禁止决策/风控路径读 CF 产物。详见 `docs/superpowers/specs/2026-06-13-counterfactual-replay-foundation-design.md`。
- **决策磁带捕获**：Judge 经 `_symbol_llm_cache` + `_symbol_tech_tape_cache` 专属侧信道捕获，绝不写 live `_symbol_tech_cache`。详见 `docs/superpowers/specs/2026-06-15-decision-tape-capture-fix-design.md`。
- **确定性回放 harness**（`utils/decision_replay.py` + driver `cf_replay_driver.py`）。Judge 写状态快照允许，禁止读回放产物。详见 `docs/superpowers/specs/2026-06-13-deterministic-replay-golden-master-design.md`。
- **逐决策扰动引擎**（`utils/perturbation_replay.py`）。详见 `docs/superpowers/specs/2026-06-14-perturbation-replay-per-decision-design.md`。
- **序列组合态扰动重演**（`utils/cf_portfolio.py` + `utils/sequential_perturbation.py`）。CF 决策绝不 publish 真实 bus。详见 `docs/superpowers/specs/2026-06-14-sequential-portfolio-perturbation-design.md`。
- **旋钮扫描 + 方向推荐**（`utils/knob_sweep.py`）。绝不自动改线上 config。详见 `docs/superpowers/specs/2026-06-14-perturbation-knob-sweep-design.md`。
- **前向影子决策记录器**（`utils/shadow_decision_logger.py`）。影子决策绝不 publish 真实 bus/下单/mutate live Judge·portfolio·cooldown·daily-stop。详见 `docs/superpowers/specs/2026-06-17-trend-entry-shadow-decision-logger-design.md`。
- **胜率解耦复核驱动**（`cf_ev_decouple_ab.py`）。详见 `docs/superpowers/specs/2026-06-20-ev-decouple-forward-ab-design.md`。
- **形态研究链**（`utils/candlestick_patterns.py` / `cf_pattern_edge_discovery.py` / `pattern_forward_shadow.py` / `fetch_historical_klines.py`）。详见 `docs/superpowers/specs/2026-06-23-*-design.md`。
- **choppy+neutral TP1 地板反事实驱动**（`cf_choppy_neutral_tp1_floor_ab.py`）。详见 `docs/superpowers/specs/2026-06-24-cf-choppy-neutral-tp1-floor-ab-design.md`。
- **neutral 动量救援测量驱动**（`cf_neutral_momentum_rescue_ab.py`）。详见 `docs/superpowers/specs/2026-06-26-cf-neutral-momentum-rescue-ab-design.md`。

### 修改策略必须同步
- 修改 Judge / 策略公式必须同步事件回测或补同构测试，不能只看 mock 单测。
- 修改入场/出场逻辑的新增字段必须同步到 `_build_attribution` 与 `_rejection_attribution`，并在 `event_backtest.py` 中同步。

## Exchange 规则

- 当前实盘和 testnet 验收以 OKX USDT 永续为主。
- 所有新建 exchange client 优先走 `utils.exchange_factory.create_exchange()`。
- `USE_TESTNET=true` 时必须在任何 API 调用前启用 sandbox/testnet。
- `executor.py` 底层仍直接创建 ccxt，但必须保持构造期设置 sandbox；后续应收敛到 factory。
- Binance path 视为 legacy，不能假设具备与 OKX `attachAlgoOrds` 相同语义。

## 状态文件命名空间（FR-008）

- 状态路径由 `utils/state_paths.py` 单一真相源派生，禁止再硬编码 `data/positions.json` 等。
- 命名空间优先级：显式 `STATE_NAMESPACE=live|testnet|paper` > `USE_TESTNET=true` 推断 testnet > 默认 live。
- live 默认完全兼容历史路径；testnet/paper 加 `testnet_` / `paper_` 前缀。
- 新增状态文件必须通过 `get_state_paths()` 读取默认值；显式参数仍可覆盖（测试或运维场景）。
- 启动 banner 由 `format_banner()` 自动打印当前 namespace 与 6 个状态文件路径。

## LLM 规则

- 所有 LLM JSON 调用应传 schema，并记录 validation errors。
- `BehavioralCritic` 字段已统一为 canonical `counter_recommendation/confidence_in_challenge`（2026-05-28 FR-005）；schema 与 `_rule_fallback` 输出 canonical 字段，`_normalize_critic_payload` 把 legacy `counter_action/confidence` 别名补齐，`PositionAnalyst._arbitrate` 兼容两套字段。
- LLM audit 会记录截断后的 user message 和 raw response；涉及账户、订单或策略敏感信息时需先做脱敏设计。
- LLM 不可用时必须规则降级，不能中断交易关键链路。

## 文档入口

| 文档 | 用途 |
|---|---|
| `README.md` | 项目入口和当前状态 |
| `docs/to-do-list.md` | 当前阻断项、后续优化、已关闭事项 |
| `docs/generated_reports/系统性审计报告_20260610_第五次.md` | 最新系统性审计报告 |
| `docs/runbook.md` | 运维命令、环境变量、故障处理 |
| `docs/development.md` | 开发边界、flow、验证矩阵 |
| `docs/integration-guide.md` | 下游集成和消息契约 |
| `docs/architecture.md` | 当前系统架构（模块、数据流、配置）；演进里程碑见 handoff |
| `docs/handoff.md` | 完整历史演进、逐阶段里程碑与逐基线测试数 |

## 禁止事项

- 不要把 `main.py` / `live_trading.py` 写回生产入口。
- 不要删除或覆盖用户已有 `data/`、`logs/`、`.env`。
- 不要把 paper/mock 通过写成 OKX testnet/live 语义通过。
- 不要在未执行 OKX testnet 验收时升级 ccxt 后直接 live。
- 不要在没有 `request_id` / `execution_result.v2` 的路径里新增 open 执行动作。
- 不要把历史流水继续追加到本文件；历史写入 `docs/handoff.md` 或审计报告。

## Agent skills

### Issue tracker

GitHub Issues 是本仓库的事项跟踪器，`triage` 只处理 issues，不把外部 PR 当作 triage 输入。See `docs/agents/issue-tracker.md`.

### Triage labels

本仓库使用默认的五个 triage 标签：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

本仓库是 single-context 布局，根目录 `CONTEXT.md` + `docs/adr/` 作为域文档入口。See `docs/agents/domain.md`.
