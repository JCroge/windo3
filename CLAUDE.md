# Crypto Trading System - AI 协作指南

## 当前事实

- 当前系统是多 Agent 加密货币趋势交易系统，不是跨交易所套利系统。
- 生产、paper、testnet、实盘验收主入口统一为 `python3 run_agents.py`。
- `main.py` 和 `live_trading.py` 是归档/调试路径，不能作为生产入口。
- 当前基线：`1135 passed / 4 deselected / 1 warning`（2026-06-12，第五次审计 + ccxt keysort 崩溃修复 + Agent 故障可见性 + 持仓同步瞬时重试 + Agent Health Supervisor，全部合并入 main 后全量实测）。
- 当前 Go/No-Go：小额 live 灰度 GO（维持现有 cap）；live 扩容 CONDITIONAL GO，扩容前置 = 运维 SOP 把 `BOT_INSTANCE_ID` 写入 systemd / pm2 启动配置 + 真实 TG 命令链与 drift gate 运维验收。
- OKX 验收状态：mock 执行语义 10 case PASS；真实 testnet long_short_mode 13 PASS（T0/T1/T4/T5/T6/T8–T15，T2/T3/T7 SKIP）+ net_mode 子账户 T0/T2/T3 3 PASS。
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
- 修改 R:R floor 必须改 `Judge._select_rr_floor` 单一函数，主路径与 `_apply_regime_policy` 共用；不能在调用点重新写 if/else 分支。`probe` / `long_bullish_low_rr` / `long_aligned_low_rr` / `short_bullish_strong` / `default` 五种 policy 标签由该函数返回。
- 修改 Long Entry Position Guard 必须改 `Judge._check_entry_position_policy` 单一函数，主开仓路径与三条 deferred 路径（15m / pullback / chase）必须都调用它；不能在 deferred helper 中再写一遍 overheat 判定。新增字段必须同步到 `_build_attribution` 与 `_rejection_attribution`，并在 `event_backtest.py` 中同步。详见 `docs/long_entry_position_guard_prd.md`。
- 保护单 owner 单一入口（2026-05-28 P0 FR-001/FR-002）：策略层（EarlyReview、partial TP 锁利）必须走 `ContractExecutor.move_protective_sl(symbol, new_sl, reason=...)`，不得在 agent 层直接写 `pos['stop_loss']` 或调 `_save_positions()` 与 `_replace_protective_sl`。`_replace_protective_sl` 撤旧失败不得挂新 SL，live OKX 必须 halt symbol；返回结构遵循 `ProtectiveSLResult` 契约（见 `docs/audit_remediation_20260528_acceptance.md` §8.1）。
- close path 不直接撤保护单（2026-05-28 P0 FR-003）：`agents/trading/executor.py` 的 trade_decision close、risk_alert（emergency/flash/position_danger/high_leverage_danger/trailing_stop）、`_close_all_positions`、local_stop（stop_loss/take_profit/price_fetch_failed）全部只能调 `executor.close_position(symbol)`；保护单 cancel + orphan algo sweep 由 root `_cleanup_protective_orders_on_close()` 完成，状态写到 `result.protective_cleanup_state ∈ {cleaned/none/failed/unknown}`。新增 close 路径同样禁止直接 `cancel_order(sl_order_id)`。
- execution_result.v2 close cause（2026-05-28 P0 FR-004）：close 类 payload（action='close' 或 status ∈ {force_closed, closed_externally}）必须含 `exit_reason / close_cause / is_strategy_stop / is_risk_forced`，由 `_classify_close_cause(source, reason)` 单一函数生成；Judge 的 `force_closed` / `closed_externally` 分支必须用 `payload['is_strategy_stop']` 门控 `_record_sl_hit()` 与 `_probe_short_sl_count`，禁止再用 `status == 'force_closed'` 当作 SL hit 信号。下游对历史无新字段 payload 必须 fail-safe（默认不计 SL）。
- 真实已实现 PnL 账本 dual-payload（2026-05-28 PRD §6.2 Phase 1+2）：外部平仓必须走 `closed_externally` 先 publish `pnl_is_final=false`、再 `pnl_resolved/pnl_mismatch` 升级 final，禁止再在同步路径里 best-effort 估算成 final。`utils/realized_pnl_resolver.py` 是唯一 OKX fills-history+bills 解析入口，状态集合 `final/pending/estimated/mismatch/pending_fx`；`utils/live_ledger.record_pending_external_close()` 写 pending（`realized_pnl_net_usdt=None`）+ `apply_pnl_resolution()` 写 correction（`supersedes_event_id`+`correction_seq`，幂等 upsert）；`Reconciler.auto_resolve_pending()` 每 tick 扫 pending 升级 final，由 Executor `_run_reconciliation()` 发布 `pnl_resolved` / `pnl_mismatch` 总线事件。Reviewer/Judge 必须按 `pnl_is_final=True` 守门，pending 不进 `trade_history.json`、不进 `_archetype_cooldown.record_result()`、不计 probe_short SL。`fee` 非 USDT 时落 `pending_fx` 不强行换算；fills/bills 净值偏差超过 `max(0.10, |bills_net|*0.05)` 落 `mismatch` 不写 final。详见 `docs/exchange_realized_pnl_ledger_prd.md` / `docs/exchange_realized_pnl_ledger_acceptance.md`。
- Entry drift 必须走单一函数 `executor._classify_entry_drift`，主路径（Gate 1 限价前）与 fallback 路径（Gate 2 市价前）共用，不在调用点重写 if/else；Gate 2 基准始终原 `plan.entry_ref`，严禁用 Gate 1 重算后的 plan 当输入。重算必须通过 `_recompute_plan_for_drift` 按 `sl_pct/tp_pct` 同比例平移；medium band（2–5% drift）floor 加成 `+0.20`。Plan 缺 `entry_ref/sl_pct/tp_pct` 任一字段走 fail-safe accept（`drift_pct=0.0`）+ 发 `risk_alert.plan_missing_entry_ref`，禁止默默走老路径。详见 `docs/superpowers/specs/2026-06-01-entry-drift-hybrid-policy-design.md`。
- Position TP 字段写入必须经 `_set_position_tp(position, tp_first, tp_levels)` 单一收口，保证 `position.take_profit == position.take_profit_levels[0]`；`_update_trailing` 顶部 invariant 检测违反 → `_halt_symbol(reason='tp_invariant_breach')` + `risk_alert.tp_invariant_breach`，禁止任何代码点旁路写 take_profit/take_profit_levels。SL 方向亦改 invariant：`open_position_with_plan` 检测到 SL 落错一侧直接 halt symbol + `risk_alert.sl_invariant_breach`，不再静默"修正"。
- Paper limit 撮合单一入口（2026-06-03）：paper 收到 `plan.order_type=='limit'` + 有效 `entry_zone` 必须走 `_pending_limits[symbol]` 队列 + `_wait_paper_limit_fill` (tick 驱动) + `_scan_pending_limits` (30s cleanup)，禁止再用 `latest_price` 立成交。`_open_paper_at_price` 是 paper 创建仓位的唯一函数，必须携带 `entry_method ∈ {market, limit_filled, limit_unfilled}`；超时分流由 `_resolve_pending_timeout` 单一函数决定，不可在调用点重写 if/else。`_pending_limits` 仅 in-memory；`_persist_state` / `_load_state` 不得序列化 pending。`paper_unfilled` 必须带 `source='paper_executor'`，`pullback_unfilled` 默认 `source='executor'`，TG 按 `source` 加 `[模拟]`/`[实盘]` 前缀；缺 source → fail-safe live 默认 + warning。详见 `docs/superpowers/specs/2026-06-03-pullback-entry-paper-parity-design.md`。
- 修改短单结构性风险 gate 必须改 `Judge._classify_short_entry_risk` 单一函数（2026-06-05），main path 与 deferred 三路径（15m / pullback / chase）必须都调用它；不能在 deferred helper 或 `_apply_regime_policy` 调用点重写 daily_bias / range_pos / pre_move / RSI 判定。`RSI <= 30` 的硬性 no-short 阈值在 `agents/trading/judge.py:853, 978, 1404` 三处独立保留，不能与软性结构 gate 合并；`short_live_min_rsi` 默认 40 是结构性 gate，与硬阈值语义不重叠。LLM hold/"禁止做空"/"超卖"/"看涨背离"/"支撑"/"追空风险" 关键词只能写入 `llm_short_reversal_risk=true` 归因 + 收紧信号，不允许单独 veto；最终拒单驱动必须是结构性原因（`daily_bearish_required` / `range_position_too_low` / `pre_move_too_deep` / `rsi_too_low_for_short` / `short_score_too_low` / `htf_votes_insufficient`）。新增 short attribution 字段必须同步到 `_apply_short_gate_attribution`，并保证 `short_gate_version` / `short_gate_decision` / `short_gate_reason` / `llm_short_reversal_risk` 四字段在 accept / reject path 都写入。详见 `docs/superpowers/specs/2026-06-05-short-main-path-risk-guard-parity-design.md`。
- 研究层流动性硬过滤必须改 `MarketScanner._apply_liquidity_hard_filter` + `_liquidity_rejection_reason` 单一函数（2026-06-07），在 enrichment 之后、发布 `research_market_data` 之前生效，禁止在调用点重写门槛或把流动性判定下沉给 Censor / LLM prompt。门槛是 `volume_24h` 与 `open_interest_usd` 双 gate（默认 50M / 10M），缺 OI 必须 fail-closed 剔除，不允许放行未证明深度的标的。粗筛 `min_volume_24h`（默认 5M）是交易所扫描广度的便宜首过，不能与该 live 安全 gate 合并。`liquidity_filter` summary 必须随 payload 发布，degraded `last_good` 兜底必须复用已过滤候选并带上一次 summary，禁止在降级路径重新引入被剔除的低流动性标的。门槛只能经 `RESEARCH_MIN_VOLUME_24H_USDT` / `RESEARCH_MIN_OPEN_INTEREST_USD` 调参，不能放宽 Judge / RiskGuard / Executor。详见 `docs/superpowers/specs/2026-06-07-research-liquidity-hard-filter-design.md`。
- Paper 双轨账本必须经 `book ∈ {realistic, idealized}` 维度单点收口（2026-06-10）：所有持仓/equity 访问走 `self._books[book]`，`_positions`/`_equity` 只是 realistic 的代理 property，禁止在 book 参数化的 helper 内用代理 property 写 equity（会把 idealized 记到 realistic）。realistic 行为必须零回归：`paper_dual_track_enabled` disabled 时 outcome 与现状等价且不产生任何 idealized 文件。idealized 只在 `_tick_fresh` 时市价开仓，缺/陈旧 tick 跳过不伪造价；idealized 退出 = 镜像策略 close/reduce/add（仅当持有）+ 自走 SL/TP，使 `limit_discipline_value` 只隔离入场效应。`_pending_limits` 仅 realistic 且永不序列化。对比层 `paper_dual_track_report.py` 是 paper-only 纯函数，**严禁**被 live Reviewer 消费（`tests/test_paper_dual_track.py::test_reviewer_does_not_consume_idealized_or_paper` 守卫）。状态用分离文件：realistic 维持原 flat `paper_positions.json` 格式不变（telegram reader 依赖）。详见 `docs/superpowers/specs/2026-06-10-paper-dual-track-sim-design.md`。
- 数据源 provenance 必须经 `utils/data_provenance.py::derive_confidence` 单函数派生 confidence（2026-06-10），禁止在调用点写 bespoke 置信度评分；新增维度走 `provenance_entry`。provenance 是 **observability-only metadata**：`MultiJudge._summarize_provenance` 写入 `attribution.provenance` 与 `ReviewerAgent._provenance_bucket` 必须是 write-only，**严禁**任何 gate/rank/veto/halt/daily-stop 读取 provenance / weakest_confidence / has_cross_exchange / provenance_bucket（"Judge 对弱信号降权"是独立后续 change，须回测）。collector 的 `market_data["provenance"]` 是非破坏并行 block，flat 字段值不得改动；provenance 必须穿透 `collector → tech_analysis → Judge attribution → trade record → Reviewer`，任一层 legacy 缺 provenance 必须 fail-safe 当 `unknown`。freshness 必须取 API item timestamp（非 fetch time）以反映真实数据年龄。详见 `docs/superpowers/specs/2026-06-10-data-source-provenance-design.md`。
- Agent 健康聚合必须经 `utils/health_snapshot.py::build_health_snapshot` 单一纯函数派生四维度（loop-alive / queue backlog / LLM degraded / data degraded），2026-06-12。健康快照是 **observability-only write-only**：写入 `agent_health.json` 与驱动 Orchestrator `_maybe_alert_health_transitions` 边沿告警 + `/status`/`/health` 展示，**严禁**任何 gate/rank/veto/halt/daily-stop 读取健康状态做交易决策（与 provenance 同性质）。loop-alive 告警只看 `BaseAgent._last_alive_ts`（message loop 0.5s 心跳，与业务节奏解耦，零误报）；`_last_work_ts` 仅展示绝不告警。告警边沿触发 + 恢复通知、四维度独立、持续不健康静默；DLQ/`agent_task_failed`/Judge `risk_alert{llm_degraded}`（决策路径）各自独立，不并入此机。详见 `docs/superpowers/specs/2026-06-12-agent-health-supervisor-design.md`。

## Exchange 规则

- 当前实盘和 testnet 验收以 OKX USDT 永续为主。
- 所有新建 exchange client 优先走 `utils.exchange_factory.create_exchange()`。
- `USE_TESTNET=true` 时必须在任何 API 调用前启用 sandbox/testnet。
- `executor.py` 底层仍直接创建 ccxt，但必须保持构造期设置 sandbox；后续应收敛到 factory。
- Binance path 视为 legacy，不能假设具备与 OKX `attachAlgoOrds` 相同语义。

## 状态文件命名空间（FR-008）

- 状态路径由 `utils/state_paths.py` 单一真相源派生，禁止再硬编码 `data/positions.json` 等。
- 命名空间优先级：显式 `STATE_NAMESPACE=live|testnet|paper` > `USE_TESTNET=true` 推断 testnet > 默认 live。
- live 默认完全兼容历史路径（`data/positions.json` / `data/risk_state.json` / `data/halt_state.json` / `data/riskguard_state.json` / `data/live_order_events.jsonl` / `data/live_position_lifecycle.json`）；testnet/paper 加 `testnet_` / `paper_` 前缀。
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
| `docs/generated_reports/系统性审计报告_20260528_第四次.md` | 最新系统性审计报告 |
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
