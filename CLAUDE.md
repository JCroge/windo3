# Crypto Trading System - AI 协作指南

## 当前事实

- 当前系统是多 Agent 加密货币趋势交易系统，不是跨交易所套利系统。
- 生产、paper、testnet、实盘验收主入口统一为 `python3 run_agents.py`。
- `main.py` 和 `live_trading.py` 是归档/调试路径，不能作为生产入口。
- 2026-05-25 自动化基线：`531 passed / 4 deselected / 1 warning`（含 `test_okx_posmode_executor.py` 38）。
- 2026-05-26 自动化基线：`551 passed / 4 deselected / 1 warning`（含 R:R Floor Policy 新增 20 个 case）。
- 2026-05-26 Long Entry Position Guard 上线后基线：`575 passed / 4 deselected / 1 warning`（含 `test_long_entry_position_guard.py` 新增 23 case）。
- 2026-05-28 P0 整改后基线：`668 passed / 4 deselected / 1 warning`（含 `test_protective_sl_owner.py` 11 case + `test_judge_close_cause.py` 33 case + 既有用例）。
- 2026-05-28 P1 整改后基线：`699 passed / 4 deselected / 1 warning`（新增 `test_behavioral_critic_contract.py` 15 case + `test_state_namespace.py` 16 case）。
- 2026-05-28 真实已实现 PnL 账本 Phase 1+2 后基线：`711 passed / 4 deselected / 1 warning`（新增 `test_exchange_realized_pnl_resolver.py` 12 case，覆盖 AC-A1..A9 + AC-A12 + AC-D1/D2）。
- 2026-05-28 真实已实现 PnL 账本 Phase 3 backfill 后基线：`727 passed / 4 deselected / 1 warning`（新增 `test_realized_pnl_backfill.py` 16 case，覆盖 AC-A10 dry-run 不动 events.jsonl + delta 输出 / AC-A11 apply 写 correction 不删旧 JSONL + 幂等 + summary resolved/pending/mismatch/skipped；脚本 `scripts/backfill_realized_pnl.py` 默认 dry-run 安全）。
- 2026-05-28 真实已实现 PnL 账本 P0+P1 整改后基线：`739 passed / 4 deselected / 1 warning`（test_exchange_realized_pnl_resolver.py 12 → 17 case，新增 AC-A5b retry schedule [10/30/120/600/1800] / AC-A14 needs_manual_reconcile + next_retry_at 门控 / AC-A13 sl_algo_id+tp_algo_id+entry_attribution 全链路透传到 resolution + correction event）。修复 P0-1..P0-4：(P0-1) closed_externally pending 不计 SL hit，pnl_resolved final 才追溯；(P0-2) apply_pnl_resolution 严格按 status 分流 — final 写 correction、pending/pending_fx 调 update_pending_resolution_attempt 只更新 retry metadata、mismatch 写独立 pnl_mismatch_alert，retry chain 不被 supersede 误断；(P0-3) Reconciler/Executor 后台 resolve 仅 final 调 apply、pending 不广播总线事件；(P0-4) Telegram 加 pnl_resolved/pnl_mismatch 订阅、null pnl 走 estimated_pnl 文案、_handle_pnl_resolved/_handle_pnl_mismatch 升级与对账偏差告警分离、daily summary 仅 final。P1：retry schedule + 24h needs_manual 落到 update_pending_resolution_attempt；resolver 透传 sl/tp algo IDs + entry_attribution 到 resolution，correction event 与 pnl_resolved/pnl_mismatch 总线事件携带同一字段集。
- 2026-05-28 第三次审计整改后基线：`807 passed / 4 deselected / 1 warning`（新增 `test_reduce_protective_sl_lifecycle.py` 14 + `test_protective_cleanup_owner.py` 12 + `test_external_close_final_cause.py` 9 + probe_short 门控 2 + `test_symbol_mentions.py` 33 case + utils/symbol_mentions.py helper）。FR-3A reduce_position 结构化结果 + cancel/restore/replace fail-closed + residual 必重挂 + live OKX halt；FR-3B `_cleanup_protective_orders_on_close()` owner-tag clOrdId（`ca+namespace+bot+base+random`）+ 三层 owner 判定 + foreign 不撤 + halt 阻断新开仓；FR-3C resolver `_classify_close_evidence` 输出 `final_close_cause/match_rule/confidence`，Judge/Reviewer 按 correction_event_id 幂等去重，probe_short SL 计数受 `is_strategy_stop` 门控；FR-3D 新闻 ticker mention 走严格边界正则（cashtag/paren/pair/keyword/word），TON/ARB/NEAR 等高歧义短 ticker 不放行 word 规则。详见 `docs/audit_remediation_third_pass_20260528_prd.md` / `docs/audit_remediation_third_pass_20260528_acceptance.md`。
- 2026-05-29 第四次审计整改后基线：`860 passed / 4 deselected / 1 warning`（净增 53 case：`test_owner_tag_clord_id_callsites.py` 8 + `test_pnl_resolved_event_contract.py` 19 + `test_reduce_failure_propagation.py` 25 + `test_partial_tp_lifecycle.py` clord_id 断言修正 + `test_execution_result_contract.py` reduce_pct 语义修正）。F4-001：`agents/trading/executor.py` 新增 `_classify_reduce_outcome` 6 分支单点契约（None / pre-trade fail → rejected；exchange reject → reduce_failed；dust_closed → executed+close+reduce_origin；reduce_ok=True && ok=False → risk_reduced + protection_failed=True；ok=True → 干净 risk_reduced），PositionAnalyst 部分平 / portfolio_exposure / partial_tp_1/2 三路径共用，PortfolioRiskGuard rejected/reduce_failed 不缩 + protection_failed 缩 + 发独立 risk_alert，Telegram 文案按 protection_failed 分流 + risk_alert critical_types 加 protection_failed。F4-002：`utils/realized_pnl_resolver.py:make_resolution_id` 4 级幂等链（corr → sup → key → pos）；`Reconciler.auto_resolve_pending` summary + `_resolve_external_close_async` + `_run_reconciliation` 三发布点透传 `final_close_cause / close_evidence / resolution_id`；correction=None && pending 跳过发布 + warning；Judge/Reviewer dedup fall-back 优先按 resolution_id；Telegram 保留 60s window 不强制 resolution_id 去重。F4-003：`_replace_protective_sl` / `open_position_with_plan` / legacy `_open_position` 三处真实新挂 SL 改用 `_make_owner_tag_clord_id`，legacy `_make_sl_clord_id` 保留并标 [DEPRECATED]（cleanup 仍按 exact 匹配兼容历史）；live 缺 `BOT_INSTANCE_ID` 时 banner 打 WARNING（testnet/paper 不打）。OKX 真实 testnet 验证（T0/T1/T6 PASS）：T1 OKX 回包 `algoClOrdId="catestneaudit5BTCUSD..."` 含 owner-tag prefix，证明 F4-003 在真实链路生效。详见 `docs/audit_remediation_fourth_pass_20260528_acceptance.md`。
- 2026-06-01 Entry Drift Hybrid Policy 上线后基线：`954 passed / 4 deselected / 1 warning`（净增 33 case：`test_entry_drift_hybrid_policy.py` 28 + `test_judge_plan_anchor_fields.py` 4 + `test_event_backtest_drift_compat.py` 1）。Judge `_build_plan` 新增 `entry_ref/sl_pct/tp_pct` 锚点字段；executor 单一函数 `_classify_entry_drift` + `_recompute_plan_for_drift` 实现 4 档 Hybrid drift gate（accept ≤ 0.5% / small recalc 0.5–2% / medium recalc + floor +0.20 2–5% / abandon > 5%），双 Gate（限价前 + fallback 前）基准始终原 `plan.entry_ref` 防分段累加；删除 `executor.py:1991-1997` 机械 TP 修正、`2203-2205` limit 校准、`2259-2262` fallback 0.5% 检查；SL 方向修正改 invariant fail-closed；`_set_position_tp` 单一收口杜绝 partial_tp_1 双源真相，违反 → halt symbol + `risk_alert.tp_invariant_breach`；新 reject reason `drift_too_large/drift_rr_floor_fail` + 5 个 critical_types `entry_drift_abandoned/entry_drift_rr_fail/plan_missing_entry_ref/tp_invariant_breach/sl_invariant_breach`；`agents/trading/executor.py:_drain_drift_alerts` 把 root executor `_pending_drift_alerts` 转发为 `risk_alert` 总线事件，`execution_result.v2.attribution.entry_drift` 嵌套 dict 暴露 band/decision/drift_pct。详见 `docs/superpowers/specs/2026-06-01-entry-drift-hybrid-policy-design.md` 与 `docs/audit_remediation_entry_drift_hybrid_policy_acceptance.md`。
- 2026-06-01 TG Graceful Ops 整改后基线：`921 passed / 4 deselected / 1 warning`（净增 61 case：`test_tg_symbol_halt_control.py` 30 + `test_tg_pnl_correction.py` 15 + `test_tg_status_enhancement.py` 16）。F-TG-001：`executor.py` 新增 `clear_symbol_halt(symbol=None, *, source="unknown") -> int` + `get_halted_symbols() -> dict`；`agents/trading/executor.py:_handle_resume` 三个成功分支调 `_safe_clear_symbol_halt(None, ...)` 清 in-memory `_halted_symbols`，解决 5/30 XLM 8 小时静默拒单 bug；`force_resume` 同步清 + `risk_alert{type=force_resume_cleared_symbol_halts}` 让 TG 回显被清的 symbol。F-TG-002：新增 `/halts` (file 直读) + `/resume_symbol <SYMBOL>` (走 bus system_command 路由到 MultiExecutor agent) + `/status` 增加 Per-symbol halt 行；TG agent 不持有 root executor 引用（agent 隔离），通过 `_handle_command` 的 `handlers_with_args` 集合支持带参命令；新 risk_alert types `symbol_halt_cleared / symbol_halt_not_found` 加入 critical_types。F-TG-003：新增 `/pnl <SYMBOL> <NET_PNL> [reason]` + `/pnl_id <event_id> <NET_PNL> [reason]`，共用 `_resolve_pending_for_pnl_correction(filter_fn)` helper，1 候选写 `manual_tg_review` correction、0/多候选 fail-fast；`/pnl_id` 是多候选的精确匹配回退；TG `setup()` lazy-init `LiveLedger(exchange=None)`。F-TG-004：`utils/state_paths.py` 加 `agent_health: str` namespace 派生路径；MultiExecutor 周期 publish `halts_snapshot` 事件；Orchestrator 订阅 + 缓存 + `_health_loop` 每 30s 写 `data/<ns_>agent_health.json`（schema 含 ts/agents_registered/tasks_alive/tasks_failed/halted_symbols/bus_dlq_size）；TG `/status` 读 health.json 增加 Agents/Bus DLQ/Per-symbol halt 三行，缺失时降级文案。详见 `docs/audit_remediation_tg_graceful_ops_acceptance.md`。
- OKX mock 执行语义验收 10 case PASS；OKX 真实 testnet 语义验收 2026-05-28 完成：long_short_mode 跑 T0/T1/T4/T5/T6/T8/T9/T10/T11/T12/T13/T14/T15 13 PASS（T2/T3 SKIP、T7 SKIP mock_only），net_mode 子账户单独跑 T0/T2/T3 3 PASS；T2/T3 net_mode caveat 已解除。第四次审计 F4-001/002/003 代码与单测已闭环（2026-05-29），live 扩容 NO-GO 已解除前置；扩容前需运维 SOP 把 `BOT_INSTANCE_ID` 写入 systemd / pm2 启动配置以保持单 bot 重启时 owner-tag 区分能力。TG Graceful Ops 命令清单：`/status /positions /halt /resume /force_resume /reconcile /halts /resume_symbol /pnl /pnl_id /stop /restart /log`。待办看 `docs/to-do-list.md`，第四次审计报告看 `docs/generated_reports/系统性审计报告_20260528_第四次.md`，整改验收报告看 `docs/audit_remediation_fourth_pass_20260528_acceptance.md` 与 `docs/audit_remediation_tg_graceful_ops_acceptance.md`。
- R:R Floor Policy 修复已上线（2026-05-26）：单一 `Judge._select_rr_floor` 函数，主路径与 deferred 路径共用，新增 `long_aligned_low_rr` 分支允许 mixed/choppy 下趋势强一致多头按 1.30 floor 进入 low_rr_extra slot。详见 `docs/rr_floor_policy_prd.md` / `docs/rr_floor_policy_acceptance.md`。
- 2026-06-03 Pullback Entry Paper Parity 上线后基线：`993 passed / 4 deselected / 1 warning`（净增 23 case：`test_paper_limit_fill.py` 17 + `test_telegram_pullback_alerts.py` 6）。Paper Executor 限价撮合契约对齐 live：`_open_paper` 在 `plan.order_type == 'limit'` 且 `entry_zone` 有效时改写入 `_pending_limits` 不立成交；`_wait_paper_limit_fill` 单一函数 tick 驱动判定 `min(low) <= tick_price <= max(high)`，命中在中点开仓写 `entry_method='limit_filled'`；`_scan_pending_limits` 嵌入 30s `tick()` cleanup loop 处理超时；`_resolve_pending_timeout` 三档分流（`limit_no_fallback=True` → `paper_unfilled` 拒单 + `risk_alert`；no_fallback=False + tick fresh → market 成交 + `paper_limit_fallback_used` log；no_fallback=False + tick stale/None → `paper_unfilled_no_tick` 拒单）。`paper_limit_tick_staleness_sec` 默认 60s，可经 `.env` `PAPER_LIMIT_TICK_STALENESS_SEC` 覆盖（`HARD_LIMITS=(1.0, 600.0)`）。`entry_method ∈ {market, limit_filled, limit_unfilled}` 写入 paper position + close trade record，旧记录无字段时下游 fail-safe 默认 market。TG `critical_types` 新增 `pullback_unfilled`（live）+ `paper_unfilled`（paper），按 `payload.source` 加 `[实盘]`/`[模拟]` 前缀；live alert 显示 `limit_price`，paper alert 显示 `entry_zone`；paper unfilled 携带 `subtype='no_tick'` 时显示"行情失联"变体。Root `_enqueue_drift_alert` 注入 `source='executor'` 默认；agent 层 `_drain_drift_alerts` 把 `pullback_unfilled` 写到 `agent_executor_*.log`。`_pending_limits` 仅 in-memory，不参与 `_save_state` / `_load_state`，重启即丢失。详见 `docs/superpowers/specs/2026-06-03-pullback-entry-paper-parity-design.md`。
- 2026-06-10 当前本地默认回归：`1010 passed / 4 deselected / 1 warning`。Short Main Path Risk Guard Parity 已在 2026-06-05 上线（当时新增 `tests/test_short_main_path_risk_guard.py` 14 case）：Judge 新增 `_classify_short_entry_risk` 单一函数，main path（`agents/trading/judge.py:1529`）与 deferred 三路径（15m / pullback / chase，`agents/trading/judge.py:792, 911, 1032`）共用同一短单结构性风险 gate，主路径在 `open_short` 发布之前评估 `daily_bearish_required` / `range_position_too_low` / `pre_move_too_deep` / `rsi_too_low_for_short` / `short_score_too_low` / `htf_votes_insufficient`，与 `_apply_regime_policy` 完全同语义。`RSI <= 30` 硬性 no-short 阈值在 `agents/trading/judge.py:853, 978, 1404` 三处保留不动，未与结构性 gate 合并；`short_live_min_rsi`（默认 40）作为软性结构 gate 独立存在，RSI 31.5 / 34 等可作为反转风险被拒。LLM hold / "禁止做空" / "超卖" / "看涨背离" / "支撑" / "追空风险" 关键词只作为 `llm_short_reversal_risk=true` 归因 + 收紧信号，不能单独 veto；最终拒单驱动必须是结构性原因。`_apply_short_gate_attribution`（`agents/trading/judge.py:3057`）在 accept / reject path 写入 `short_gate_version="short_main_path_parity_v1"` / `short_gate_decision ∈ {pass, reject, probe}` / `short_gate_reason=<machine-readable>` / `llm_short_reversal_risk`，让 Reviewer / backtest 把 pre / post 分布切片分开。NEAR 2026-06-05 09:01（LLM parse fail + bullish daily + low range + deep pre-move）/ 09:23（parsed 禁止做空 + bullish）两个 fixture 锁定回归。详见 `docs/superpowers/specs/2026-06-05-short-main-path-risk-guard-parity-design.md` 与 `docs/superpowers/reports/2026-06-05-short-main-path-risk-guard-parity-verify.md`。
- 2026-06-07 研究层低流动性硬过滤器上线（2026-06-10 补 OpenSpec/verify 流程闭环，代码 commit 2047187 已在线）：`agents/research/market_scanner.py` 新增 `_apply_liquidity_hard_filter` / `_liquidity_rejection_reason`，在 enrichment 之后、发布 `research_market_data` 之前对候选做确定性流动性硬过滤——`volume_24h >= research_min_volume_24h_usdt`（默认 50M）且 `open_interest_usd >= research_min_open_interest_usd`（默认 10M），二者都过才保留；缺 OI 或取不到 fail-closed 剔除（reason `open_interest_missing`），volume 先于 OI 判定（reason 顺序 `volume_below_min` → `open_interest_missing` → `open_interest_below_min`）。payload 带 `liquidity_filter` summary（thresholds/removed/kept/≤5 examples），degraded `last_good` 兜底复用已过滤候选并带上一次 summary。`utils/config_loader.py` 加 DEFAULTS/HARD_LIMITS + `RESEARCH_MIN_VOLUME_24H_USDT`/`RESEARCH_MIN_OPEN_INTEREST_USD` env 覆盖（与粗筛 `min_volume_24h` 相互独立）。背景：BABY-USDT 低流动性标的进入候选池→Judge 开多→风控强平，把流动性从 LLM/Censor 主观研判下沉为入场前确定性 gate。`test_research_market_scanner_failover.py` 8 case PASS；OpenSpec change `2026-06-07-research-liquidity-hard-filter` + master spec `research-liquidity-filter` 已归档，verify 报告 `docs/superpowers/reports/2026-06-07-research-liquidity-hard-filter-verify.md`。
- 2026-06-10 Paper Dual-Track Simulation 上线后基线：`1035 passed / 4 deselected / 1 warning`（净增 25 case：`test_paper_dual_track.py` 19 + `test_paper_dual_track_report.py` 6）。PaperExecutor 新增 `book ∈ {realistic, idealized}` 维度：realistic = 现有限价撮合（limit_filled/limit_unfilled，约 53% 漏单），idealized = 决策瞬间按 `_latest_price` 市价立成交的 baseline（仅 `_tick_fresh` 时开，缺/陈旧 tick 跳过不伪造价）。`self._books={'realistic':{positions,equity},'idealized':...}`，`_positions`/`_equity` 是代理 realistic 的 property（旧代码零改），核心 helper（`_open_paper_at_price`/`_close_paper`/`_check_sl_tp`/`_add_paper`/`_reduce_paper`/`_unrealized_pnl`/`_locked_margin`）带 `book='realistic'` 参数。idealized 镜像策略 `close/reduce/add`（仅当 idealized 持有该 symbol）+ 自走独立 SL/TP；realistic unfilled（无仓）时 idealized 自走 SL/TP = 量化漏单。状态用**分离文件**：realistic 仍写原 flat `paper_positions.json`/`paper_equity.json`（telegram reader 零改、legacy 天然兼容），idealized 写 `paper_positions_idealized.json`/`paper_equity_idealized.json`；`_pending_limits` 仅 realistic、不序列化。对比层是纯函数 reader `agents/trading/paper_dual_track_report.py`（`compute_gap`/`format_gap`，按 `book` 切片算 win%/EV/总PnL/回撤 + `limit_discipline_value=realistic_total-idealized_total`，`low_sample` 显式标注），经 TG `/paper_gap [days]` + `tick()` 周期日志暴露，**不进 live Reviewer**（隔离守卫测试锁定）。`paper_dual_track_enabled` 开关 paper namespace 默认开、disabled 时 outcome 与现状等价且不写 idealized 文件。判断依据（历史数据：unfilled≈53%、limit_filled 唯一正收益桶、反事实本地不可重建）见 design doc Evidence。详见 `docs/superpowers/specs/2026-06-10-paper-dual-track-sim-design.md` 与 `docs/superpowers/reports/2026-06-10-paper-dual-track-sim-verify.md`。
- 2026-06-10 Data Source Provenance 上线后基线：`1066 passed / 4 deselected / 1 warning`（净增 31 case：`test_data_provenance.py` + `test_data_provenance_collector.py` + `test_data_provenance_propagation.py`）。给跨源行情维度补 provenance（`source`/`freshness_sec`/`confidence`）：`utils/data_provenance.py` 的 `derive_confidence`（单函数，freshness 线性衰减到 2× 采样周期归零 × 跨所 0.7 penalty，degraded/missing→0）+ `provenance_entry`。`multi_data_collector` 的 `_fetch_oi_delta`/`_fetch_taker_ratio`/`_fetch_long_short_ratio`（`binance_fapi`）/`_fetch_big_trades`（`okx`）改返回 `(value, meta)`，从**被丢弃的 API item timestamp**（Binance `timestamp`、OKX `ts`）捡回 freshness（taker/long_short 是 `period=1h` 最旧 1h、oi `period=5m`）；`_full_collect` 组装非破坏并行 `market_data["provenance"]={dim:{source,freshness_sec,confidence}}`，flat 字段值不变。`tech_analyst` 把 provenance 透传进 `tech_analysis`（Judge/Reviewer 读 tech_analysis 不读 raw market_data，否则收敛即丢）。`MultiJudge._summarize_provenance` 写 `attribution.provenance={quality,weakest_confidence,has_cross_exchange}` —— **metadata-only，决策行为零变更**（grep 证实 write-only，决策套件全绿）。`ReviewerAgent._provenance_bucket` 按 `{native|cross_exchange}/{low|high}` 分桶（缺失→unknown）。本 change 只观测+穿透；"Judge 对弱信号降权"是独立后续项（须回测）。背景：OI/taker/long_short 全来自 Binance 喂 OKX 系统、1h 旧数据当现值用且时间戳被丢。详见 `docs/superpowers/specs/2026-06-10-data-source-provenance-design.md` 与 `docs/superpowers/reports/2026-06-10-data-source-provenance-verify.md`。
- 2026-06-11 第五次审计 P1-02/P1-03 短单 gate 修复（comet change `fix-short-gate-or-falsy-single-source`，隔离 worktree）：本 change 隔离基线 `1066 + 7 = 1073 passed`（合并入 main 后总基线随其它在途 change 叠加，以合并时实跑为准）。**P1-02**：`MultiJudge._classify_short_entry_risk` 原 `float(a or b or default)` 提取 `position_in_24h_range`/`pre_12h_return_pct`/`rsi`，present 的 `0.0`（24h 锅底、做空最危险的追底场景）被当 falsy → 退化成默认 0.5 → `range_position_too_low` gate 失效。改用新增 `@staticmethod _coalesce_float(*vals, default)` 哨兵合并（仅 absent None 才取默认，present 0.0 原样保留）；同模式三处统一（短单 gate / `_check_entry_position_policy` long overheat gate / attribution 写点）。**P1-03**：`_apply_regime_policy` 短单结构段原是第二份内联实现（默认值 1.0 与 canonical 0.5 发散），违反"短单 gate 必须只在 `_classify_short_entry_risk` 单点收口"红线；改为 delegate 到 `_classify_short_entry_risk` 并保留 probe 路由外壳（reason==`daily_bearish_required` 时由外壳决定 probe-or-reject，其它结构 reason 直接透传拒单），红线名实相符。`event_backtest` 短单 gate 用 `.get(...,0.5)`（row 永不 None）一直正确处理 0.0 且单份实现 → 本 change 是 live 向回测对齐 + live 两份合一，回测决策路径无需改动。`tests/test_short_main_path_risk_guard.py` 14→21 case。残留：`_check_entry_position_policy` 仍有第三份内联短单 gate（已享 `_coalesce_float` 修复、与 canonical 阈值一致，单点收口完全合并为后续项）。详见 `docs/superpowers/specs/2026-06-11-fix-short-gate-or-falsy-single-source-design.md`。
- 当前待办统一看 `docs/to-do-list.md`，最新审计报告看 `docs/generated_reports/系统性审计报告_20260528.md`。

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
| `docs/architecture.md` | 架构与历史演进 |
| `docs/handoff.md` | 长历史交接记录 |

## 禁止事项

- 不要把 `main.py` / `live_trading.py` 写回生产入口。
- 不要删除或覆盖用户已有 `data/`、`logs/`、`.env`。
- 不要把 paper/mock 通过写成 OKX testnet/live 语义通过。
- 不要在未执行 OKX testnet 验收时升级 ccxt 后直接 live。
- 不要在没有 `request_id` / `execution_result.v2` 的路径里新增 open 执行动作。
- 不要把历史流水继续追加到本文件；历史写入 `docs/handoff.md` 或审计报告。
