# To-Do List

更新日期：2026-06-10
来源：2026-05-24 系统性审计、全量测试、OKX mock 验收、docs 清理；2026-05-25 OKX posMode 执行故障复核与代码落地；2026-05-26 R:R Floor Policy 修复 + Long Entry Position Guard 上线；2026-05-27 OKX 真实 testnet T0-T9 语义验收 PASS；2026-05-28 系统性审计复核 + P0/P1 历史整改 + 真实已实现 PnL 账本 Phase 1+2+3 落地；2026-05-28 第三次审计 P0/P1/P2 整改完成；2026-05-29 第四次审计 F4-001/002/003 整改完成（解除 live 扩容 NO-GO 前置）；2026-06-01 TG Graceful Ops 与 Entry Drift Hybrid Policy 完成；2026-06-03 Pullback Entry Paper Parity 完成；2026-06-05 Short Main Path Risk Guard Parity 完成；2026-06-07 研究层低流动性硬过滤器上线（2026-06-10 补 OpenSpec/verify 流程闭环）；2026-06-10 Paper Dual-Track Simulation 完成（idealized vs realistic 双轨 + /paper_gap，comet 全流程归档）；2026-06-10 Data Source Provenance 完成（跨源 source/freshness_sec/confidence 穿透至 tech_analysis + Judge attribution + Reviewer 分桶，observability-only，comet 全流程归档）。
当前基线：`1066 passed / 4 deselected / 1 warning`。OKX 真实 testnet T0/T1/T6 PASS（owner-tag clOrdId 验证）。live 扩容为 CONDITIONAL GO；扩容前需运维 SOP 把 `BOT_INSTANCE_ID` 写入启动配置，并完成真实 TG 命令链与 drift gate 运维验收。

最新整改文档：

- `docs/audit_remediation_20260528_prd.md`
- `docs/audit_remediation_20260528_acceptance.md`
- `docs/audit_remediation_third_pass_20260528_prd.md`
- `docs/audit_remediation_third_pass_20260528_acceptance.md`
- `docs/audit_remediation_fourth_pass_20260528_acceptance.md`
- `docs/audit_remediation_tg_graceful_ops_acceptance.md`
- `docs/audit_remediation_entry_drift_hybrid_policy_acceptance.md`
- `docs/superpowers/specs/2026-06-03-pullback-entry-paper-parity-design.md`
- `docs/superpowers/specs/2026-06-05-short-main-path-risk-guard-parity-design.md`
- `docs/superpowers/reports/2026-06-05-short-main-path-risk-guard-parity-verify.md`
- `docs/generated_reports/系统性审计报告_20260528_第四次.md`

## 当前 Go/No-Go

- 本地开发：GO。
- Paper/mock：GO。
- 小额 live 灰度：GO（保持现有 cap，运维可接管）。
- live 扩容：CONDITIONAL GO（解除 NO-GO 前置已完成；扩容前需运维 SOP 把 `BOT_INSTANCE_ID` 写入 systemd / pm2 启动配置，并完成真实 TG 命令链与 drift gate 运维验收）。

## 第五次审计阻断（处理中）

> 第五次审计报告：`docs/generated_reports/系统性审计报告_20260610_第五次.md`。多 change 并行处理（P1-01/P2-02 与 P1-02/P1-03 分属不同 worktree）；合并入 main 后总基线以实跑为准。

| 状态 | 优先级 | 事项 | 落地 | 验收证据 |
|---|---|---|---|---|
| DONE 2026-06-11 | P1 | P1-02 短单 gate `or`-falsy：`range_pos=0.0`（24h 锅底）被当 0.5，`range_position_too_low` gate 失效 | `MultiJudge` 新增 `@staticmethod _coalesce_float(*vals, default)`（仅 absent None 取默认，present 0.0 保留）；`_classify_short_entry_risk` + `_check_entry_position_policy` long overheat gate + attribution 写点三处统一改用 | `tests/test_short_main_path_risk_guard.py` `TestClassifyShortEntryRisk` 新增 3 case（含锅底拒单）；隔离基线 1073 passed |
| DONE 2026-06-11 | P1（红线） | P1-03 短单结构 gate 第二份内联实现（`_apply_regime_policy`，默认值 1.0 vs 0.5 发散），违反单点收口红线 | `_apply_regime_policy` 短单段 delegate 到 `_classify_short_entry_risk`，删第二份实现，保留 `daily_bearish_required` probe 路由外壳；attribution 四字段（caller-owned）不回归 | `TestApplyRegimePolicyDelegation` 4 case（parity / probe 外壳 / 锅底）；comet change `fix-short-gate-or-falsy-single-source`，design `docs/superpowers/specs/2026-06-11-fix-short-gate-or-falsy-single-source-design.md` |

## 第四次审计阻断（已闭环 2026-05-29）

| 状态 | 优先级 | 事项 | 落地 | 验收证据 |
|---|---|---|---|---|
| DONE 2026-05-29 | P0 | F4-001 reduce 失败回参 Agent 误广播为 `risk_reduced` | `agents/trading/executor.py` 新增 `_classify_reduce_outcome` 6 分支单点契约（None/pre-trade fail → rejected；exchange reject → reduce_failed；dust_closed → executed+close+reduce_origin；reduce_ok=True && ok=False → risk_reduced + protection_failed=True；干净 ok → risk_reduced），PositionAnalyst 部分平 / portfolio_exposure / partial_tp_1/2 三路径共用；PortfolioRiskGuard rejected/reduce_failed 不缩、protection_failed 缩 + 发独立 `risk_alert{type='protection_failed'}`；Telegram 文案按 protection_failed 分流 + critical_types 加 protection_failed | `test_reduce_failure_propagation.py` 25 case PASS（`TestClassifyReduceOutcome` 7 / `TestPositionAnalystPartialClose` 3 / `TestPortfolioExposureReduce` 3 / `TestPartialTpReduce` 3 / `TestPortfolioRiskGuardReduceHandling` 5 / `TestTelegramReduceMessages` 4） |
| DONE 2026-05-29 | P1 | F4-002 `pnl_resolved` final cause / 幂等键透传 | `utils/realized_pnl_resolver.py:make_resolution_id` 4 级幂等链（corr → sup → key → pos）；`Reconciler.auto_resolve_pending` summary + `_resolve_external_close_async` + `_run_reconciliation` 三发布点透传 `final_close_cause / close_evidence / resolution_id`；`correction is None` 且 status 非 final/mismatch 跳过发布 + warning；Judge / Reviewer dedup fall-back 优先按 resolution_id；Telegram 保留 60s window 不强制 resolution_id 去重 | `test_pnl_resolved_event_contract.py` 19 case PASS（`TestMakeResolutionId` 8 / `TestReconcilerSummaryFields` 1 / `TestResolveExternalCloseAsyncPublish` 3 / `TestRunReconciliationPublish` 1 / `TestSubscriberDeduplication` 6） |
| DONE 2026-05-29 | P1 | F4-003 OKX 真实新 SL owner-tag clOrdId | `_replace_protective_sl` / `open_position_with_plan` / legacy `_open_position` 三处真实新挂 SL 改用 `_make_owner_tag_clord_id`；legacy `_make_sl_clord_id` 保留并标 `[DEPRECATED]`（cleanup 仍按 exact 匹配兼容历史）；`utils/state_paths.py:as_banner_lines` 加 `BOT_INSTANCE_ID` 行，live 缺时打 WARNING（testnet/paper 不打） | `test_owner_tag_clord_id_callsites.py` 8 case PASS；OKX 真实 testnet T1 回包 `algoClOrdId="catestneaudit5BTCUSD..."` 含 owner-tag prefix（报告：`docs/generated_reports/OKX执行语义testnet验收报告_20260529_112117.md`） |

## 第三次审计阻断（已闭环）

| 状态 | 优先级 | 事项 | 落地 | 验收证据 |
|---|---|---|---|---|
| DONE 2026-05-28 | P0 | `reduce_position()` 缩仓保护单生命周期 | `executor.py:2427-2724` 撤旧 SL 失败立即返回 `sl_cancel_failed`、不清旧 ID、live OKX halt；reduce reject 后尝试 restore 原 SL；residual 必重挂 SL；结构化结果含 `protective_update_state/protection_state/halt_required/cancel_ok/reduce_ok/replace_ok` | `test_reduce_protective_sl_lifecycle.py` 14 case PASS；AC3-P0-001..008 全过 |
| DONE 2026-05-28 | P0 | `_cleanup_protective_orders_on_close()` owner-bound sweep | `executor.py:1169-1310` 三层 owner 判定（known_id / clord exact / `ca+namespace+bot` owner-prefix）；新增 `_make_owner_tag_clord_id()` / `_is_owner_clord_id()`；foreign/unknown 不撤、写 `state=foreign_algos_present` + `halt_required=True`；`close_position()` 透传 `protective_cleanup` 全字段 | `test_protective_cleanup_owner.py` 12 case PASS；AC3-P0-009..014 全过 |
| DONE 2026-05-28 | P1 | 外部平仓 final close cause 证据与幂等 | `utils/realized_pnl_resolver.py::_classify_close_evidence` 输出 `final_close_cause/match_rule/confidence/matched_*_id`，仅 `exchange_sl` + `confidence>=0.9` 才 `is_strategy_stop=True`；`agents/trading/judge.py` 与 `agents/trading/reviewer.py` 按 `correction_event_id\|position_id` 幂等去重；Judge probe_short SL 计数受 `is_strategy_stop` 门控（仅 exchange_sl 才递增） | `test_external_close_final_cause.py` 9 + 2 case PASS；AC3-P1-001..007 全过 |
| DONE 2026-05-28 | P2 | 新闻 ticker mention 边界匹配 | `utils/symbol_mentions.py` 提供 `match_symbol_in_text` / `extract_symbol_mentions` / `filter_relevant_headlines`；五条规则 cashtag/paren/pair/keyword/word + 正则边界 `(?<![A-Z0-9])SYM(?![A-Z0-9])`，TON/ARB/NEAR 等高歧义短 ticker 不放行 word 规则；`agents/research/news_researcher.py` 与 `agents/trading/multi_data_collector.py` 都走 helper；输出 `confidence/match_rule/source/freshness_sec` provenance | `test_symbol_mentions.py` 33 case PASS；AC3-P2-001..006 全过 |

## 已完成历史整改记录

| 状态 | 事项 | 下一步 | 验收标准 |
|---|---|---|---|
| DONE 2026-06-07 | 研究层低流动性硬过滤器（BABY-USDT 事件根因） | `agents/research/market_scanner.py` 新增 `_apply_liquidity_hard_filter` / `_liquidity_rejection_reason`，enrichment 后、发布 `research_market_data` 前按 `volume_24h >= research_min_volume_24h_usdt`(默认 50M) + `open_interest_usd >= research_min_open_interest_usd`(默认 10M) 双门槛硬过滤，缺 OI fail-closed 剔除；payload 带 `liquidity_filter` summary，degraded `last_good` 兜底带 summary；`utils/config_loader.py` 加 DEFAULTS/HARD_LIMITS + `RESEARCH_MIN_VOLUME_24H_USDT`/`RESEARCH_MIN_OPEN_INTEREST_USD` env 覆盖 | `test_research_market_scanner_failover.py` 8 case PASS（含 liquidity 专项 2 case）；OpenSpec change `2026-06-07-research-liquidity-hard-filter` + master spec `research-liquidity-filter` 已归档；verify 报告 `docs/superpowers/reports/2026-06-07-research-liquidity-hard-filter-verify.md`（2026-06-10 事后补流程闭环，代码 2026-06-07 commit 2047187 已上线） |
| DONE 2026-05-28 | 保护单 owner 收敛（P0 FR-001/FR-002） | EarlyReview 收敛到 `ContractExecutor.move_protective_sl`；`_replace_protective_sl` cancel/place fail-closed；live OKX 失败 halt | `test_protective_sl_owner.py` 11 case + `test_partial_tp_lifecycle.py::TestProtectiveSlSingleEntry` PASS；AC-P0-001 至 AC-P0-006 通过 |
| DONE 2026-05-28 | Agent close path 不直接撤保护单（P0 FR-003） | trade_decision close / risk_alert / close_all / local_stop 全部走 `close_position()`；新增 `_cleanup_protective_orders_on_close` sweep + `protective_cleanup_state` 字段 | `test_judge_close_cause.py::TestCloseDoesNotDirectlyCancel` 6 case + 静态扫描 `rg cancel_order\( agents/trading/executor.py` 仅剩 helper 与 sweep 引用；AC-P0-007 至 AC-P0-011 通过 |
| DONE 2026-05-28 | close cause / Judge cooldown 修复（P0 FR-004） | `_build_execution_result()` 自动注入 `exit_reason/close_cause/is_strategy_stop/is_risk_forced` + `result.protective_cleanup_state`；Judge `force_closed`/`closed_externally` 分支只在 `is_strategy_stop=True` 时调用 `_record_sl_hit()` | `test_judge_close_cause.py::TestExecutionResultCloseCause` 17 case + `TestJudgeRecordSlHit` 10 case PASS；AC-P0-012 至 AC-P0-015 通过 |
| DONE 2026-05-28 | OKX testnet T10-T15 保护单/close cause 补验（人工） | `verify_okx_testnet_real.py` 扩 T10-T15：EarlyReview move、cancel failure halt、risk_alert close、local stop close、close_all、external SL；`_wait_no_live_algos` 兜住 51400/51412 异步生效 | T10/T11/T12/T13/T14/T15 6/6 PASS；报告：`docs/generated_reports/OKX执行语义testnet验收报告_20260528_063307.md`；全量 T0-T15 13 PASS / 3 SKIP（T2/T3 long_short_mode、T7 mock_only） |
| OPEN | OKX net_mode 切换二次验收（可选） | 把 testnet 账户 posMode 切到 net_mode 后跑 T2/T3 | T2 reduce ratio in [0.4, 0.6]、T3 close 后无残余 algo；当前账户为 long_short_mode，已通过 mock 矩阵覆盖 net_mode 闭环 |
| DONE 2026-05-28 | testnet/live 状态命名空间（P1 FR-008） | `utils/state_paths.py` 解析 `STATE_NAMESPACE=live|testnet|paper`，未设时 `USE_TESTNET=true→testnet`，否则 live；`executor.py` / `risk_manager.py` / `portfolio_risk_guard.py` / `multi_data_collector.py` / `judge.py` / `position_analyst.py` / `telegram_notifier.py` / `utils/halt_state.py` / `utils/live_ledger.py` 默认路径全部按 namespace 派生；`format_banner` 打印 namespace 与 6 个状态文件路径；live 默认完全兼容历史路径 | `test_state_namespace.py` 16 case PASS；AC-P1-007/008/009/010 通过 |
| DONE 2026-05-28 | BehavioralCritic 字段契约统一（P1 FR-005） | `BEHAVIORAL_CRITIC_SCHEMA` 改为 canonical `counter_recommendation/confidence_in_challenge`；`_normalize_critic_payload` 把 legacy `counter_action/confidence` 别名补齐；`PositionAnalyst._arbitrate` 同时读两套字段 | `test_behavioral_critic_contract.py` 15 case PASS；AC-P1-001/002 通过 |
| DONE 2026-05-28 | network 测试限时 + 缺 DB 干净 skip（P1 FR-007） | `test_kline.py` 用 `asyncio.wait_for` 5s 时间窗 + 网络异常 skip；`conftest.py` 新增 `klines_db` fixture 缺 `data/klines.db` 时 pytest.skip 并给出准备说明；`test_indicators.py` / `test_backtest.py` / `test_strategy.py` 改用该 fixture | `pytest -q -m network` 4 case 12s PASS；缺 DB 时 3 skipped 1.9s；AC-P1-005/006 通过 |

## P2 后续优化

| 状态 | 事项 | 下一步 | 验收标准 |
|---|---|---|---|
| OPEN | 真实已实现 PnL 账本 Phase 4 testnet 矩阵 | OKX testnet 跑 T0..T6（fills 直达 / bills 兜底 / mismatch / pending_fx / ambiguous / external SL / 异步资源对账） | 6 case 真实 testnet 全过 + 报告 `docs/generated_reports/realized_pnl_ledger_testnet_*.md` |
| DONE 2026-06-01 | Telegram `/pnl` 手动 PnL correction 命令 | 新增 `/pnl <SYMBOL> <NET_PNL> [reason]` 与 `/pnl_id <event_id> <NET_PNL> [reason]`,共用 `_resolve_pending_for_pnl_correction(filter_fn)` helper,1 候选写 `manual_tg_review` correction、0/多候选 fail-fast | `test_tg_pnl_correction.py` 15 case PASS;TG `setup()` lazy-init `LiveLedger(exchange=None)`,reason 写入 manual_correction_reason,详见 `docs/audit_remediation_tg_graceful_ops_acceptance.md` |
| DONE 2026-06-10 | Paper 结果独立复盘 | `agents/trading/paper_dual_track_report.py` 纯函数 reader（`compute_gap`/`format_gap`）+ TG `/paper_gap [days]` + PaperExecutor `tick()` 周期 gap 日志；按 `book` 切片，paper/live 隔离守卫测试锁定 reviewer 不消费 paper | 可查看 paper realistic vs idealized 胜率/EV/总PnL/回撤 + `limit_discipline_value`，不污染 live Reviewer。详见 `docs/superpowers/specs/2026-06-10-paper-dual-track-sim-design.md` |
| DONE 2026-06-10 | Paper 双轨模拟（idealized + realistic） | PaperExecutor 加 `book∈{realistic,idealized}` 维度：realistic=现有限价撮合，idealized=决策瞬间市价立成交 baseline（仅 tick fresh 时开）；双账本独立持仓/SL-TP/equity，idealized 镜像策略 close/reduce/add + 自走 SL/TP；分离状态文件 + legacy 兼容；`paper_dual_track_enabled` 开关 paper 默认开 | comet 全流程归档 `openspec/changes/archive/2026-06-10-paper-dual-track-sim`，master spec `paper-dual-track` 新增 + `paper-executor` 8→11 增量；判断依据见 design doc Evidence（unfilled≈53%、limit_filled 唯一正收益桶、反事实本地不可重建 → 市价 baseline）；1010→1035 tests |
| OPEN | ma_aligned 触发面收窄（pullback policy issue #2） | 评估 `PULLBACK_ATR_ENTRY_TYPES` 是否应排除 `ma_aligned`，让该 entry_type 走 deferred_15m_confirmation；当前 ma_aligned 全覆盖 pullback 路径 | 数据回测后决策（依赖 paper realistic 数据） |
| OPEN | PULLBACK_LIMIT_TIMEOUT_SEC 数值调参（pullback policy issue #4） | 1800s 是否合理；是否应根据 atr/regime 动态化 | paper realistic 数据观察 unfilled 率后决策 |
| OPEN | paper_limit_tick_staleness_sec 阈值调参 | 60s 默认值是否合适，从 paper_unfilled / paper_unfilled_no_tick 比例评估 | 数据回测后决策 |
| OPEN | LLM audit 脱敏和保留策略 | 增加 `LLM_AUDIT_RETENTION_DAYS`、原始 prompt 记录开关、敏感字段脱敏 | 日志保留可配置，默认不长期保留敏感输入/响应 |
| OPEN | `ContractExecutor` exchange 创建统一 | 将根 `executor.py` 的 ccxt 创建收敛到 `utils/exchange_factory.py` 或共享 helper | 所有 exchange client 的 sandbox/live 语义由单一入口控制 |
| OPEN | Binance legacy path 标识 | 明确当前 live/testnet 只验收 OKX；Binance 分支标为 legacy 或补交易所能力适配 | 文档和代码注释不再暗示 Binance 已具备同等 TP/SL 语义 |
| DONE 2026-06-10 | 数据源 provenance（观测+穿透） | `utils/data_provenance.py` 单函数 `derive_confidence`；collector 为 OI/taker/long_short/big_trades/funding 捕获 `source`/`freshness_sec`（捡回被丢弃的 API item timestamp）/`confidence`（freshness+跨所+degraded 派生），非破坏并行 provenance block 入 market_data；tech_analyst 透传进 tech_analysis；Judge `_summarize_provenance` 写 attribution（metadata-only，决策不变）；Reviewer `_provenance_bucket` 按 native/cross × low/high 分桶 | comet 全流程归档 `openspec/changes/archive/2026-06-10-data-source-provenance`，master spec `data-source-provenance`（8 需求）；1035→1066 tests。**注**：news mention provenance 此前已由 `utils/symbol_mentions.py` 覆盖（FR-3D）；本项覆盖行情维度字段 |
| OPEN | Judge 对弱信号降权（provenance 后续） | 基于已铺出的 provenance（weakest_confidence / has_cross_exchange），让 Judge 对陈旧/跨所/低置信信号降权或门控 | 策略改动，须事件回测验证（CLAUDE.md 红线）；依赖 provenance 数据先累积。详见 `docs/superpowers/specs/2026-06-10-data-source-provenance-design.md` Out of Scope |
| OPEN | Agent health supervisor | Orchestrator 增加 setup failure、loop alive、queue backlog、DLQ、LLM degraded、data degraded 状态 | Telegram `/status` 或 health 输出能看见关键 agent 健康状态 |
| OPEN | 文档瘦身 | `CLAUDE.md`、`docs/architecture.md`、`docs/handoff.md` 历史流水迁出或压缩 | 规则文档只保留当前事实和硬约束，旧测试数仅在历史上下文出现 |
| OPEN | 策略层深度优化提案（先观察） | 详见 `docs/strategy_optimization_proposal_20260602.md` 5 项发现（Exit Strategy 系统止损 20% 胜率 / ma_aligned 直接开仓 -9.14U / R:R poor bucket 全亏 / Regime choppy 主导 / BTC 14 分钟连续开 16 单）；2026-06-03 决定先归档观察，等 paper realistic 数据累计后回看 | paper realistic 数据足够后再决策实施或归档 |

## 已关闭

| 事项 | 验收证据 |
|---|---|
| Short Main Path Risk Guard Parity | 2026-06-05 落地，2026-06-10 本地验证 `1010 passed`。新增 `Judge._classify_short_entry_risk` 单一函数，main path 与 deferred 三路径（15m / pullback / chase）共用；触发反转风险条件（`daily_bearish_required` / `range_position_too_low` / `pre_move_too_deep` / `rsi_too_low_for_short` / `short_score_too_low` / `htf_votes_insufficient`）在 main path 发布 `open_short` 之前评估，与 `_apply_regime_policy` 同语义。`RSI <= 30` 硬性 no-short 阈值在 `agents/trading/judge.py:853, 978, 1404` 三处保留，未与软性结构 gate 合并。LLM hold/"禁止做空"/"看涨背离"等文本只作为 `llm_short_reversal_risk=true` 收紧信号与归因，独立结构性风险才是拒单驱动；不能单独 veto。`_apply_short_gate_attribution` 在 accept/reject path 写入 `short_gate_version=short_main_path_parity_v1` / `short_gate_decision ∈ {pass,reject,probe}` / `short_gate_reason` / `llm_short_reversal_risk`，供 Reviewer 与 backtest 区分 pre/post 分布。新增 `tests/test_short_main_path_risk_guard.py` 14 case 全过；fixture 覆盖 NEAR 2026-06-05 09:01（LLM parse fail + bullish daily + low range + deep pre-move）/ 09:23（parsed 禁止做空 + bullish）。详见 `docs/superpowers/specs/2026-06-05-short-main-path-risk-guard-parity-design.md` 与 `docs/superpowers/reports/2026-06-05-short-main-path-risk-guard-parity-verify.md` |
| Bucketed EV short side | `_build_plan()` 写入 `side`；`test_phase2_bucketed_ev.py` 覆盖 short bucket |
| halt/resume owner | Telegram `/resume` 不直接 confirm；Executor `_handle_resume()` 负责 `HaltState.confirm_resume()` |
| `execution_result.v2` 全路径统一 | `_build_execution_result()` 覆盖 reject/error/open/close/risk/sync/external close；`test_execution_result_contract.py` 通过 |
| exchange sandbox 分散 | scanner/data/judge/telegram 使用 `utils.exchange_factory.create_exchange()`；root executor 构造期设置 sandbox |
| RiskGuard 纳入恢复对账 | Telegram reconciliation 读取 `data/riskguard_state.json` |
| paper/live mismatch 阻塞恢复 | `PositionReconciler` 区分 blocking/advisory，paper mismatch 不阻塞 |
| contractSize 关键换算 | `test_okx_contract_size.py` 通过 |
| `data_alert` 无消费者 | Telegram 已订阅并处理 `data_alert` |
| 旧入口误跑 | `start.sh` 启动 `run_agents.py`；`main.py` deprecated 后退出 |
| 依赖不可复现 | `requirements.lock` 和 `docs/dependency_upgrade_runbook.md` 已存在 |
| Phase 2 配置缺口 | `config_loader.py` 默认值/env map/banner/runbook 已补齐 |
| R:R Floor Policy 修复 | `Judge._select_rr_floor` 单一函数收敛主路径与 `_apply_regime_policy`；`long_aligned_low_rr` 策略允许 mixed/choppy 趋势强一致多头按 1.30 floor 入场；`test_rr_floor_policy.py` 20 case PASS（AC-RR-01..09 覆盖）；attribution 新增 `rr_floor_used`/`rr_floor_reason`/`symbol_trend`/`symbol_higher_tf_bias`/`symbol_daily_bias`；详见 `docs/rr_floor_policy_acceptance.md` |
| Long Entry Position Guard | `Judge._check_entry_position_policy` 单一函数收敛主路径与 `deferred_15m_confirmation` / `deferred_pullback` / `deferred_chase` 三条 deferred 路径；命中 `range_pos>=0.82` 或 `pre_12h>=0.05 ∧ range_pos>=0.75` 或 `prev_daily>=0.10 ∧ range_pos>=0.75` 标记 `entry_position_status=overheated`，有有效回调目标时进入 `deferred_pullback_overheat`（`chase_eligible=false`），否则直拒；`plan.entry_type` 在 EV gate 之前写入避免 `unknown` bucket key；EV bucket 增加 sparse-sample 保护（`EV_BUCKET_MIN_TRADES=10`，`EV_BUCKET_SPARSE_ALLOW_UPLIFT=false`）；`event_backtest.py` 与 live 同构；`test_long_entry_position_guard.py` 23 case PASS（覆盖 AC-LONGPOS-01..17）；详见 `docs/long_entry_position_guard_prd.md` 与 `docs/long_entry_position_guard_acceptance.md` |
| 分批止盈生命周期收敛 阶段 1+2+3 | `_build_okx_attach_algo` 不再带 TP；`reduce_position(tp_advance)` 真实成交后才推进 `tp_filled` 并锁利位；`_replace_protective_sl` 单一入口替代所有 SL cancel/place；`_make_sl_clord_id` + `_resolve_attached_sl_algo_id` 让 smart_open 通过 `attachAlgoClOrdId` 回查 algoId；`add_to_position` 在 `protection_state != protected` 时拒绝；`_migrate_okx_algos_for_symbol` 在重启/sync 后清理存量 algo（TP 一律撤、唯一 SL 归属本地、orphan 全撤、无 SL/多 SL/方向冲突 live halt）；`test_partial_tp_lifecycle.py` 32 case PASS；详见 `docs/partial_tp_lifecycle_prd.md` / `docs/partial_tp_lifecycle_acceptance.md` |
| OKX 真实 testnet 语义验收 | T0/T1/T4/T5/T6/T8/T9 PASS，T2/T3 SKIP（账户为 long_short_mode），T7 SKIP（mock_only 已 PASS）。报告：`docs/generated_reports/OKX执行语义testnet验收报告_20260527_150518.md`。bug 修复：`_cancel_protective_sl` / `_cancel_algo_by_id` 改走 `cancel_orders([id], symbol, params={'trigger': True})`（直接 `private_post_trade_cancel_algos` 传 dict/list 都被 OKX 拒成 50002）。工具：`verify_okx_testnet_real.py` + `.env.testnet` 隔离 testnet 凭证 |
| 2026-05-28 P0 整改代码与单测 | FR-001 EarlyReview → `ContractExecutor.move_protective_sl` 单一公开入口；FR-002 `_replace_protective_sl` 撤旧失败不挂新 SL，live OKX 失败 halt；FR-003 Agent close path 7 处直接 `cancel_order(sl_order_id)` 全部移除，`close_position()` 调用新增 `_cleanup_protective_orders_on_close` sweep 出 owner-tagged orphan algo，结果挂到 `result.protective_cleanup_state ∈ {cleaned/none/failed/unknown}`；FR-004 `_build_execution_result()` 在 close action 注入 `exit_reason/close_cause/is_strategy_stop/is_risk_forced`，Judge 仅在 `is_strategy_stop=True` 时记 SL hit；新增 `test_protective_sl_owner.py` 11 case + `test_judge_close_cause.py` 33 case；fixed legacy `test_executor_upgrade.py` / `test_riskguard_upgrade.py` / `test_full_pipeline.py` 三处 `cancel_order` 断言；全量回归 `668 passed / 4 deselected / 1 warning`。AC-P0-001..015 全过 |
| OKX testnet T10-T15 保护单/close cause 真实补验 | 2026-05-28 6/6 PASS。T10 EarlyReview `move_protective_sl` 单一入口契约（ProtectiveSLResult 全字段、唯一新 algo、protection_state=protected）；T11 `_cancel_protective_sl` 失败时不挂新 SL（cancel_ok=False、place_call_count=0、sl_sync_state=failed、protection_state=unknown、本地 SL 保留、halt_required=False testnet 不 halt）；T12 risk_alert close（exit_reason=risk_emergency / is_risk_forced=True / is_strategy_stop=False）；T13 local_stop（exit_reason=local_stop_loss / is_strategy_stop=True）；T14 close_all（exit_reason=system_close_all / is_risk_forced=True）；T15 external_close 三种 reason 映射。OKX testnet 51400/51412 异步生效用 `_wait_no_live_algos` 轮询补撤兜住，残留 live algo=0 时接受 cleanup_state=failed。报告：`docs/generated_reports/OKX执行语义testnet验收报告_20260528_063307.md`，原始 trace `data/testnet_verify_20260528_063307.jsonl`，备份 `data/backup_T10_T15_20260528_142129/` |
| 真实已实现 PnL 账本 Phase 1+2 | 2026-05-28 落地，`711 passed`。新增 `utils/realized_pnl_resolver.py`：唯一 OKX fills-history+bills 解析入口，pnl_status 集合 `final/pending/estimated/mismatch/pending_fx`，bills 阈值 `max(0.10, |bills_net|*0.05)`，funding subType 173/7 单独累加 `funding_usdt`，fee 非 USDT 落 `pending_fx`。`utils/live_ledger.py`：拆出 `record_pending_external_close()`（写 `realized_pnl_net_usdt=None` + `close_match_key`）+ `apply_pnl_resolution()`（写 correction 事件，含 `supersedes_event_id`/`correction_seq`，幂等 upsert）+ `find_pending_external_closes()` + `daily_realized_pnl(final_only=True)` 跳过 superseded。`utils/reconciliation.py` 加 `auto_resolve_pending(since_ts, max_attempts)` 把 pending 升级 final 返回摘要给 Executor 发布。`agents/trading/executor.py`：外部平仓走 dual-payload（先 `closed_externally pnl_is_final=False`，再 `asyncio.create_task` 调 resolver 升级，发 `pnl_resolved/pnl_mismatch`）；`_run_reconciliation` tick 自动消费 pending 队列。`agents/trading/reviewer.py`：订阅 `pnl_resolved`/`pnl_mismatch`，pending 不进 `trade_history.json`，final upsert by `entry_request_id`/`position_id`，新增 `_payload_pnl_is_final/_payload_pnl_value` helper。`agents/trading/judge.py`：`force_closed`/`closed_externally` 分支按 `pnl_is_final=True` 守门 archetype_cooldown / probe_short SL count。`execution_result.v2` 在 close 路径自动注入 `pnl_status / pnl_is_final / pnl_source / realized_pnl_net_usdt / estimated_pnl / position_id / entry_request_id`。新增 `test_exchange_realized_pnl_resolver.py` 12 case 覆盖 AC-A1/A2/A3 match+mismatch/A4/A5/A7/A8/A9/A12/D1/D2；`test_live_ledger.py` external_close 测试改 pending 契约（pnl_status=pending、realized_pnl_net_usdt=None、close_match_key 非空）；fixed legacy `test_executor_upgrade.py`/`test_riskguard_upgrade.py`/`test_full_pipeline.py` 三处 `cancel_order` 断言。Phase 3 backfill 与 Phase 4 testnet 矩阵 deferred。详见 `docs/exchange_realized_pnl_ledger_prd.md` / `docs/exchange_realized_pnl_ledger_acceptance.md` |
| 真实已实现 PnL 账本 Phase 3 backfill | 2026-05-28 落地，`727 passed`。新增 `scripts/backfill_realized_pnl.py`：扫 `events.jsonl` 中 `pnl_status=pending` 或 legacy（`pnl_status` 缺失 + `source=='estimated'`） 的 `external_close` 事件（自动排除已被 `supersedes_event_id` 引用的）；调 `RealizedPnlResolver.resolve_external_close()` 拉 OKX fills-history+bills 升级 final；dry-run（默认）输出 old_pnl/new_pnl/delta/source 表格不写文件；`--apply` 走 `LiveLedger.apply_pnl_resolution()` 写 `external_close_correction` 事件（`supersedes_event_id` 指向原 pending、`correction_seq` 单调 +1），仅 status ∈ `{final/mismatch/pending_fx}` 才写；支持 `--since/--until/--symbol/--testnet/--events-path/--lifecycle-path/--json-out`，`--dry-run` 与 `--apply` 互斥（默认 dry-run 安全）；`run()` 接受注入 ledger/resolver/exchange 便于单测。新增 `test_realized_pnl_backfill.py` 16 case 覆盖 AC-A10（dry-run 不动 events.jsonl byte 级一致 + delta 输出）+ AC-A11（apply 写 correction、不删旧 JSONL、幂等：第二次 apply 候选清零、lifecycle 累计不变、summary resolved/pending/mismatch/pending_fx/skipped/needs_exchange_data 计数齐全）+ legacy estimated 检测 + superseded 排除 + symbol/since/until 过滤 + parser dry/apply 互斥 + `--json-out` audit。Phase 4 testnet 矩阵 deferred |


## 常用验证命令

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .
python3 -m pytest -q
python3 verify_okx_testnet_semantics.py
python3 verify_okx_testnet_real.py
```
