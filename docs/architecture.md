# 系统架构文档

## 概述

加密货币趋势交易系统，基于技术分析和合约交易，支持多AI Agent协作决策。

**当前状态（2026-06-20）**：主入口为 `run_agents.py`，全量回归 `1338 passed / 8 failed / 4 deselected`（8 failed=round2 全量 asyncio 污染，隔离全 PASS，非 change 引入）。**2026-06-20 连归 3 个 comet change（基线 1314→1338，余额 1732 USDT 用户出金后确认/cap 仍 300）**：前 2 个 observability-only（重启 live PID 98028 ~10:47）——`fix-shadow-logger-replay-baseline-parity`（影子记录器 lever1 增量口径改两臂同复盘 `replay(lever2-only) vs replay(both)` + baseline 复现自检闸，坐实 lever1 真实增量=0）；`ev-decouple-forward-ab`（新驱动 `cf_ev_decouple_ab.py` 镜像 `cf_lever2_rejected_ab`，gate-toggle 两臂复盘复核胜率解耦放行单前向期望，真跑诚实门拒答、suggestive 不支持"解耦更差"假设）。第 3 个改 live executor.py（需手动重启 live）——`fix-phantom-position-resync`（MODIFIED `position-sync-resilience`：`sync_positions` 补录双确认 persist-2-ticks 防交易所平仓上报延迟产生幽灵持仓 + protection-unknown 告警去重 + migrate_missing_sl halt 自愈；20x 杠杆查明=`_calc_risk_budget` 恒定风险公式按设计非 bug）。**2026-06-18 连归 6 change（1302→1314）见 `docs/handoff.md`**。**2026-06-17 当天在 1285 之上连归 4 个 comet change 并重启 live（PID 46766，资金 cap 仍 300）**：`cf-lab-driver-portfolio-param-parity`（CF 驱动组合参数对齐 live，+0）→ **`trend-entry-levers-default-on`（+3，lever2 阶梯 effective_rr 口径默认开、改 live 决策**，config `ladder_rr_enabled` 默认 True / env `LADDER_RR_ENABLED=false` 回滚；lever1 `path_evidence_aligned_enabled` 仍默认关）→ **`trend-entry-shadow-decision-logger`（+10，前向影子决策记录器** `utils/shadow_decision_logger.py`，observability-only 复用 `replay_decision` 旁路记 both-levers 影子决策=lever1 增量到 `shadow_decision_log.jsonl`）→ **`fix-lever2-low-rr-sizing-tp1`（+4，hotfix**，低 R:R 缩仓判定用 TP1 口径单一收口 `_apply_low_rr_sizing`，地板 gate 仍用阶梯）。lever2 定价=是 bug 非赌（P(达TP2)68%/rejected A/B +0.181R/簇）。**以下为 1285 历史**：1285 = 1270 之上叠加 **`trend-entry-rr-fidelity` +15**：诊断"干净趋势零开仓"→ 实现两入场杠杆 ① `_select_rr_floor` path-evidence 客观路径证据地板（policy `long_aligned_path_evidence`）/ ② `_compute_ladder_rr` 阶梯离场比例口径 effective_rr（彼时两开关均默认关，lever2 现已默认开见上），comet 归档 2 新 capability `trend-aligned-rr-floor`+`ladder-weighted-rr`；1270 = 1255 之上叠加**多旋钮联合扫描** `joint-knob-sweep` +15；1255 = 1238 之上叠加**反事实实验室三连修** `fix-cf-lab-ev-coldstart-deadlock` +9 / `fix-cf-lab-replay-config-parity` +5 / `fix-cf-lab-symbol-state-injection` +3，均 observability-only comet 归档；1238 = 1223 + `decision-tape-capture-fix` +11 + `tick-capture-retention-prune` +4）。1223 = 1149 之上叠加**反事实策略实验室 L1-L4**：决策磁带埋点 + 确定性回放/golden master + 逐决策扰动 + 序列组合态重演 + 旋钮扫描方向推荐，全 observability-only write-only，模块 `utils/{decision_tape,decision_replay,counterfactual_pnl,cf_honesty_gate,perturbation_replay,cf_portfolio,sequential_perturbation,knob_sweep}.py` + `cf_replay_driver.py` + `cf_direction_recommendation.py`（L2 终验 + L4 方向推荐可复用驱动），红线守卫 `tests/test_cf_red_line_guard.py`；详见各层 `docs/superpowers/specs/2026-06-1[3456]-*-design.md`。**2026-06-16 实验室三连修后端到端首次可信**：续 2026-06-15 `decision-tape-capture-fix`（磁带 tech/llm 不再写死为空，经专属侧信道 `_symbol_llm_cache`+`_symbol_tech_tape_cache` 捕获，schema v2/v3）之后，又依次修 CF EV-gate 冷启动死锁（CF rolling 胜率窗口镜像 Reviewer + 暖启动播种 + gate-level fidelity）/ replay config parity（replay 用生产 config 基线 `production_base_config`，磁带录 `config_snapshot`）/ `_inject_cf_state` 还原录制 `_symbol_state`——驱动 `cf_direction_recommendation.py` baseline_fidelity 1.0(虚假)→0.34→0.798→**0.944（untrustworthy=False）**，首个可信结论：放宽 choppy R:R 地板/`min_confidence` 的 PnL delta≈0 → 非高价值杠杆，佐证地板 1.50 维持。各策略 gate 均单点收口：R:R Floor → `Judge._select_rr_floor`、Long Entry Position Guard → `Judge._check_entry_position_policy`、Entry Drift Hybrid Policy → `executor._classify_entry_drift` / `_recompute_plan_for_drift`、短单结构性风险 gate → `Judge._classify_short_entry_risk`（main path 与 deferred 三路径共用同一份语义）、Position TP 写入 → `_set_position_tp`。OKX 真实 testnet 语义验收：long_short_mode T0-T15 13 PASS / 3 SKIP + net_mode 子账户 T0/T2/T3 3 PASS（2026-05-28）+ owner-tag 补验 T0/T1/T6 PASS（2026-05-29）。当前事实与硬约束以 `CLAUDE.md` 为准，逐基线里程碑见 `docs/handoff.md`，当前待办见 `docs/to-do-list.md`。下方"重要变更"是历史时间线，不代表当前待办状态。

**重要变更**：
- 2026-05-06：原套利策略经全面验证不可行（0次机会），转向趋势交易+合约策略
- 2026-05-07：多Agent系统完成，两层架构（研判层6 Agent + 交易层7 Agent），含言官逆向审查机制
- 2026-05-07：P0风控增强完成（ReviewerAgent + Daily Hard Stop + Graceful Shutdown + 状态持久化）
- 2026-05-07：P1-A Telegram通知完成（TelegramNotifier，交易层7个Agent）
- 2026-05-08：contractSize修复（DOGE/ETH等非1合约单位正确计算），Judge杠杆上限20x
- 2026-05-08：方向决策修复（_compute_score重写：RSI极端值保护+趋势强度衰减+条件化散户反指）
- 2026-05-09：post-mortem修复（correlation_risk用保证金计算、Judge force_close冷却300s）
- 2026-05-09：入场质量优化（R:R门槛≥1.5、负面催化剂否决、30min新闻轮询+price-in检测）
- 2026-05-09：日线多周期升级（1d K线采集、日线趋势/价位/反欺骗、多周期共振1h+4h+1d、标的限制放开）
- 2026-05-09：Judge主驱动修复（rule_signal±35基础分、LLM降为修正因子不再一票否决）
- 2026-05-09：做空信号修复（RobustStrategy新增entry_short：MA死叉+RSI不超卖+放量+价格下跌）
- 2026-05-09：日线阻力区阈值收紧（3%→1.5%，减少横盘误触发）
- 2026-05-09：PROS-USDT ticker格式修复（_fetch_price_tick统一用/USDT:USDT格式）
- 2026-05-11：MA alignment信号（tech_analyst.py+judge.py）：ma_aligned_long/short给±20分，解决crossover后系统永远hold
- 2026-05-11：Symbol sync修复（executor.py）：OKX格式BASE/USDT:USDT自动转换为BASE-USDT-SWAP
- 2026-05-13：持仓管理防遗憾优化（position_analyst.py）：7因子评分+entry_thesis_intact+2h周期+阈值放宽
- 2026-05-13：R:R硬性门槛修复（judge.py）：min_rr=1.5不可绕过 + SL距离ATR封顶(2.5×ATR) + TP下限=SL×1.5
- 2026-05-14：Judge LLM-Rule方向冲突修复（judge.py）：confidence提升需方向一致 + LLM反向衰减50% + rule_signal+LLM反向衰减60% + RSI禁区inclusive(>=70/<=30)
- 2026-05-14：PositionAnalyst规则3b（position_analyst.py）：浮亏>10%+趋势非顺向→强制平仓
- 2026-05-14：llm_client.py chat_json支持temperature参数传递
- 2026-05-14：统一风险预算框架（judge.py）：杠杆由风险约束推导 `leverage = 0.5/sl_dist`，删除旧`_calc_leverage`+`_calc_size`，新增`_calc_risk_budget`统一函数，effective_rr含资金费率+手续费
- 2026-05-14：回调入场机制（judge.py）：R:R<1.5分级响应（追价/等回调/放弃），deferred_entry状态机
- 2026-05-14：Censor分批审查（censor.py）：BATCH_SIZE=4避免Cloudflare超时，LLM timeout=90s+max_retries=2
- 2026-05-14：Executor required_margin修复（executor.py）：size_usdt即margin，不再除以leverage
- 2026-05-15：HYPE重复做空5层防护（judge.py）：RSI背离HTF降权+入场门槛40+LLM conf cap 55+开仓冷却300s+失败冷却120s
- 2026-05-15：SL/TP方向校验（executor.py）：下单前验证方向合法性，价格变动导致方向错误时自动修正
- 2026-05-15：PositionAnalyst评估周期2h→1h（position_analyst.py）
- 2026-05-15：加仓/减仓功能修复（executor.py+agents/trading/executor.py）：加仓(add_to_position加权均价+SL/TP重算+上限2x)+减仓(reduce_position精度+取消旧SL)+全系统execution_result同步(is_add/risk_reduced)
- 2026-05-15：PA Rule 1/3b动态阈值（position_analyst.py）：Rule 1=SL含杠杆距离（第三道防线），Rule 3b=SL距离×50%（替代固定15%/10%）
- 2026-05-15：Executor close冷却60s（executor.py）：平仓后60s内sync_positions不重新发现该标的（防API延迟导致幽灵持仓）
- 2026-05-15：Telegram去重（telegram_notifier.py）：sync发现的持仓不推送开仓通知 + 同symbol平仓通知60s去重
- 2026-05-15：Symbol格式统一修复（judge.py+position_analyst.py+portfolio_risk_guard.py）：execution_result handler strip `-SWAP`后缀 + deferred_entry触发即时冷却，解决ZEC重复开仓+SL覆盖+PA幽灵持仓三个级联故障
- 2026-05-17：closed_externally PnL追踪（executor.py）：sync_positions保存被移除持仓数据，_estimate_close_pnl优先用unrealized_pnl（~30s误差），降级用SL价格计算。Daily Hard Stop现在能检测交易所SL触发的真实亏损
- 2026-05-17：递增冷却StoplossGuard（judge.py）：4h窗口内连续SL次数递增冷却（300→600→1200→3600s），窗口过期自动重置。参考Freqtrade StoplossGuard protection
- 2026-05-17：研判层上线时间过滤（market_scanner.py）：OKX月K线<12根的标的不进入初选（上线不足1年），enrich前并行检查节省API调用
- 2026-05-17：初选固定12标的（synthesizer.py）：LLM固定选12个标的（原5-12个浮动）
- 2026-05-17：Telegram启动flush旧消息（telegram_notifier.py）：_flush_old_updates()跳过所有pending消息，防止历史/stop命令杀掉新启动的进程
- 2026-05-17：终选prompt优化（synthesizer.py）：明确区分reject（移除）和warning（保留降置信度），代码保底防LLM过度收窄
- 2026-05-17：Logger防重复（utils/logger.py）：propagate=False + handler去重，解决每条日志打印7次的问题
- 2026-05-19：Phase 7 Trailing Stop + 分批止盈（executor.py）：Break-Even→TP1(50%)→TP2(25%)→Trailing Stop，棘轮机制
- 2026-05-19：4h RSI二级保护（judge.py）：1h RSI未触发硬cap但4h RSI≥70/≤30时score×0.5
- 2026-05-19：逻辑账户拆分（config_loader.py）：EFFECTIVE_BALANCE_CAP限制风控计算余额
- 2026-05-19：Paper Trading全并行（paper_executor.py）：影子账户Agent，独立余额+持仓+topic
- 2026-05-20：15m入场确认层（tech_analyst.py+judge.py）：MA7/25+RSI14→bias/confirm/block，block时defer等待转向
- 2026-05-21：Phase 8 Regime优化（market_regime.py+judge.py）：RegimeManager+Short Guard+Probe Short+Dynamic R:R+Low R:R Slot+Counterfactual Ledger
- 2026-05-21：Side-Aware Short Entry Gates（tech_analyst.py+judge.py+event_backtest.py）：daily_bias=bearish必须+position_in_24h_range≥0.45+pre_12h_return>-1%+RSI≥40，防止"追空"入场。BTCUSDT回测short从0%WR/-9.18 PnL改善为全部过滤（避免亏损）
- 2026-05-21：Unified Open Dispatch（judge.py）：_gate_and_publish_open统一入口+dispatch_path归因(main_direct/main_ranking/deferred_15m/deferred_pullback/deferred_chase)+_can_route_probe_short返回(bool,reason)元组
- 2026-05-22：Phase 1.5 观测与回测同构补齐（reviewer分层、position_analyst regime grace、event_backtest 同构）+ 14h shadow observation，验证 373 passed / 4 deselected / 1 warning（历史基线）
- 2026-05-24：审计整改自动化验收通过，验证 493 passed / 4 deselected / 1 warning；OKX 真实 testnet 仍待执行。
- 2026-05-25：OKX posMode 执行兼容代码完成（executor.py）：启动期 `private_get_account_config` 探测 posMode，live fail-closed；新增 `_build_okx_open_params` / `_build_okx_close_params` / `_build_okx_algo_params` 三入口构造器，业务路径全部接入；close/reduce 前 `_fetch_okx_position_state` 拉真实仓位并按 `availPos` 钳制；51169/51205/51112/51333 拒单触发 `_handle_okx_close_reject` 状态复核（already_flat/external_closed/still_open/direction_conflict），不再无脑重试或错删本地仓位。新增 `test_okx_posmode_executor.py` 38 PASS，`verify_okx_testnet_semantics.py` 扩展为 10 case（posMode close 矩阵 + 拒单复核）；基线 493 → 531。OKX 真实 testnet T0-T9 仍待执行。
- 2026-05-25：发现 `/restart`（Telegram 远程重启）走的是 `run_agents.py` 的同进程 `while True: Orchestrator()` 循环，不 fork 不 exec，Python `sys.modules` 缓存旧 `executor.py`，**新代码不会被加载**。要让代码热更新生效必须 OS 层 `kill -TERM` 后 `nohup python3 run_agents.py` 重启进程。
- 2026-05-26：修复 Telegram `/restart` 热更新语义（`run_agents.py`）：launcher 退出后检测 `data/.restart_flag` 不再 `continue` 复用旧解释器，而是执行 `os.execv(sys.executable, [sys.executable] + sys.argv)` 置换进程镜像并重新 import 模块。代码更新后可直接用 `/restart` 生效；PID 可能保持不变，属 `execv` 正常行为。Python/venv/系统级依赖升级仍建议外部 supervisor/OS 层重启。
- 2026-05-26：R:R Floor Policy 修复（judge.py + config_loader.py）：抽出 `_select_rr_floor(action, plan, tech, score)` 单一函数，主路径与 `_apply_regime_policy` 共用，返回 `(min_rr, rr_policy, rr_floor_reason)`；新增 `long_aligned_low_rr` 策略允许 mixed/choppy 下 trend bullish AND (htf bullish OR daily bullish) 多头按 1.30 floor 进 low_rr_extra slot；新增 `RR_FLOOR_LONG_ALIGNED_CHOPPY=1.30` / `PROBE_RR_FLOOR=1.30` / `LOW_RR_LONG_ALIGNED_ENABLED=true` 配置；attribution 新增 `rr_floor_used` / `rr_floor_reason` / `symbol_trend` / `symbol_higher_tf_bias` / `symbol_daily_bias` 五字段；新增 `test_rr_floor_policy.py` 20 case，基线 531→551 passed。详见 `docs/rr_floor_policy_prd.md` / `docs/rr_floor_policy_acceptance.md`。
- 2026-05-26：Long Entry Position Guard 上线（tech_analyst.py + judge.py + config_loader.py + event_backtest.py）：tech_analyst 新增 `entry_context`（`position_in_24h_range` / `pre_12h_return_pct` / `prev_daily_return_pct`），保留 `short_context` 兼容；judge 抽出 `_check_entry_position_policy(symbol, action, plan, tech, score, context)` 单一函数，主开仓路径与 `deferred_15m_confirmation` / `deferred_pullback` / `deferred_chase` 三条 deferred 路径共用；触发阈值 `range_pos>=0.82` / `pre_12h>=0.05 ∧ range_pos>=0.75` / `prev_daily>=0.10 ∧ range_pos>=0.75`，命中后创建 `deferred_pullback_overheat`（`chase_eligible=false`，4h 超时）或直拒 `long_overheat_no_valid_pullback_target`；short side guard 也走该函数（`range_position_too_low` / `pre_move_too_deep` / `rsi_too_low_for_short`）。`plan.entry_type` 前移到 EV gate 之前，消除 `unknown` bucket key；新增 `EV_BUCKET_MIN_TRADES=10` / `EV_BUCKET_SPARSE_ALLOW_UPLIFT=false`，sparse bucket 禁止抬高 `p_win`，可降仓 / 缩仓。attribution 新增 `entry_position_status` / `entry_position_block_reason` / `entry_range_pos_24h` / `entry_pre_12h_return_pct` / `entry_prev_daily_return_pct` / `entry_position_policy=long_overheat_v1` / `deferred_target_price` / `deferred_reason` / `ev_bucket_key` / `ev_bucket_trade_count` / `ev_bucket_min_trades` / `ev_bucket_sparse` 共 12 个 optional 字段。event_backtest 同步 `long_live_*` 参数与 overheat 检查。新增 `test_long_entry_position_guard.py` 23 case，基线 551→575 passed。详见 `docs/long_entry_position_guard_prd.md` / `docs/long_entry_position_guard_acceptance.md`。
- 2026-05-27：分批止盈生命周期收敛 阶段 1+2+3（executor.py + agents/trading/executor.py）：`_build_okx_attach_algo` 不再带 TP，避免 OKX 把 TP 也算成保护单触发"加仓时无保护单"误判；`reduce_position(tp_advance)` 真实成交后才推进 `tp_filled` 并 `_move_sl` 锁利位；新增 `_replace_protective_sl` 单一入口替代所有 SL cancel/place 路径；`_make_sl_clord_id` + `_resolve_attached_sl_algo_id` 让 smart_open 通过 `attachAlgoClOrdId` 回查 algoId；`add_to_position` 在 `protection_state != protected` 时拒绝；`_migrate_okx_algos_for_symbol` 在重启 / sync 后清理存量 algo（TP 一律撤、唯一 SL 归属本地、orphan 全撤、无 SL / 多 SL / 方向冲突 live halt）。新增 `test_partial_tp_lifecycle.py` 32 case，基线 575→618 passed。详见 `docs/partial_tp_lifecycle_prd.md` / `docs/partial_tp_lifecycle_acceptance.md`。
- 2026-05-27：OKX 真实 testnet 语义验收（verify_okx_testnet_real.py + .env.testnet + executor.py）：T0/T1/T4/T5/T6/T8/T9 PASS，T2/T3 SKIP（账户为 long_short_mode，需切到 net_mode 后单独跑），T7 SKIP（mock_only，已在 mock 矩阵 PASS）。关键 bug 修复：`_cancel_protective_sl` / `_cancel_algo_by_id` 改走 `cancel_orders([id], symbol, params={'trigger': True})`。2026-05-28 审计已覆盖本条当时的扩容判断，当前扩容结论见 `docs/to-do-list.md`。
- 2026-05-28：审计 P0 整改代码与单测落地（executor.py + agents/trading/executor.py + agents/trading/judge.py + 新增 test_protective_sl_owner.py / test_judge_close_cause.py）。FR-001 EarlyReview 经 `ContractExecutor.move_protective_sl(symbol, new_sl, reason=...)` 单一公开入口，结果遵循 `ProtectiveSLResult` 契约（`docs/audit_remediation_20260528_acceptance.md` §8.1）；FR-002 `_replace_protective_sl` cancel/place fail-closed，live OKX 失败 `_halt_symbol(reason='sl_cancel_failed')`；FR-003 `agents/trading/executor.py` 移除 7 处直接 `cancel_order(sl_order_id)`（trade_decision close / 4 路 risk_alert / close_all / 2 路 local_stop），全部改成只调 `executor.close_position(symbol)`，root 新增 `_cleanup_protective_orders_on_close()` 完成保护单 cancel + OKX trigger algo sweep，结果挂在 `result.protective_cleanup_state ∈ {cleaned/none/failed/unknown}`；FR-004 `_build_execution_result()` 在 close action 自动注入 `exit_reason / close_cause / is_strategy_stop / is_risk_forced`，由 `_classify_close_cause(source, reason)` 单一函数生成；Judge `force_closed`/`closed_externally` 分支只在 `payload['is_strategy_stop']=True` 时调 `_record_sl_hit()` 与 `_probe_short_sl_count`，老 payload 无字段时 fail-safe 不计 SL。新增 `test_protective_sl_owner.py` 11 case + `test_judge_close_cause.py` 33 case；fix legacy `test_executor_upgrade.py` / `test_riskguard_upgrade.py` / `test_full_pipeline.py` 三处 `cancel_order` 断言为 `assert_not_called()`。基线 619→668 passed。AC-P0-001..015 全过。OKX 真实 testnet T10-T15 保护单/close cause 补验 2026-05-28 6/6 PASS（全量 T0-T15 13 PASS / 3 SKIP，报告 `docs/generated_reports/OKX执行语义testnet验收报告_20260528_063307.md`）。该轮扩容判断已被第三次审计 supersede，当前 Go/No-Go 见 `docs/to-do-list.md`。
- 2026-05-28：审计 P1 整改代码与单测落地（agents/trading/behavioral_critic.py + agents/trading/position_analyst.py + test_kline.py + conftest.py + utils/state_paths.py + executor.py + risk_manager.py + agents/trading/portfolio_risk_guard.py + agents/trading/multi_data_collector.py + agents/trading/judge.py + agents/trading/position_analyst.py + agents/trading/telegram_notifier.py + utils/halt_state.py + utils/live_ledger.py + utils/config_loader.py + 新增 test_behavioral_critic_contract.py / test_state_namespace.py）。FR-005 `BEHAVIORAL_CRITIC_SCHEMA` 改为 canonical `counter_recommendation` / `confidence_in_challenge`，`_normalize_critic_payload` 把 legacy `counter_action` / `confidence` 别名补齐到 canonical 字段；`_rule_fallback` 输出 canonical 字段；`PositionAnalyst._arbitrate` 兼容两套字段；`test_behavioral_critic_contract.py` 15 case 覆盖 schema、normalize、arbitrate。FR-007 `test_kline.py` 用 `asyncio.wait_for(timeout=5.0)` 包裹 WebSocket 流，到点干净退出；网络异常 `pytest.skip`。`conftest.py` 新增 `klines_db` fixture：缺 `data/klines.db` 时 `pytest.skip` 并给出准备说明，否则 `shutil.copy2` 到 `tmp_path`；`test_indicators.py` / `test_backtest.py` / `test_strategy.py` 通过 fixture 接入。`pytest -q -m network` 4 PASS 12s；缺 DB 时 3 skipped 1.9s。FR-008 新增 `utils/state_paths.py` 单一真相源：`STATE_NAMESPACE=live|testnet|paper` 解析（白名单 + USE_TESTNET 推断 + 大小写不敏感 + 非白名单 fallback live），`@dataclass(frozen=True) StatePaths` 提供 `positions / risk_state / riskguard_state / halt_state / live_order_events / live_position_lifecycle` 6 个状态文件路径，live 默认完全兼容历史路径，testnet/paper 加 `testnet_` / `paper_` 前缀。9 处消费方（`executor.py` / `risk_manager.py` / `portfolio_risk_guard.py` / `multi_data_collector.py` / `judge.py` / `position_analyst.py` 2 处 / `telegram_notifier.py` 5 处 / `utils/halt_state.py` / `utils/live_ledger.py`）默认路径全部按 namespace 派生；显式参数仍可覆盖。`format_banner()` 自动追加 namespace 与 6 个路径。`test_state_namespace.py` 16 case 覆盖 AC-P1-007/008/009/010；基线 668→699 passed。AC-P1-001/002/005/006/007/008/009/010 全过。详见 `docs/audit_remediation_20260528_prd.md` / `docs/audit_remediation_20260528_acceptance.md`。
- 2026-05-28：T2/T3 net_mode caveat 解除（verify_okx_testnet_real.py 改动 + testnet 子账户切 net_mode 后补跑）。原因：T2/T3 case 设计依赖前置 T1 仓位，但 main loop 每个 case 之前会 `_safe_close_remaining` 把账户拉回 flat，T2/T3 永远拿不到仓位；之前账户为 long_short_mode 时直接走 SKIP 分支，缺陷未暴露。修复：T2/T3 改为 self-contained——case 内自己先 `open_position_with_plan('long', plan)` 建仓再做 partial reduce / full close。补验流程：`private_post_account_set_position_mode({'posMode': 'net_mode'})` → 单独跑 `--case T0,T2,T3`，net_mode 报告 3/3 PASS（`docs/generated_reports/OKX执行语义testnet验收报告_20260528_080723.md`）；切回 `long_short_mode` 后跑 `--case all`，long_short_mode 报告 13 PASS / 3 SKIP（`docs/generated_reports/OKX执行语义testnet验收报告_20260528_080900.md`）。两份报告合并视角下 T0-T15 net_mode + long_short_mode 完整覆盖；T7 mock_only 仍 by design（复现 51169/51205 需手工 OKX UI 干预，已在 `verify_okx_testnet_semantics.py` mock 矩阵 PASS）。第三次审计后 live 扩容重新回到 NO-GO，见 `docs/audit_remediation_third_pass_20260528_prd.md`。

## 架构图

### 单策略模式（live_trading.py）

```
┌─────────────────────────────────────────┐
│      K线数据采集 (WebSocket/REST)        │
│      kline_collector.py                 │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│      技术指标计算                         │
│      indicators.py                      │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│      策略系统                             │
│      optimize_1h.py (RobustStrategy)    │
│      - 4重入场确认、信号生成              │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│      实时交易系统                         │
│      live_trading.py                    │
│      ├─ 策略分析                         │
│      ├─ 风控检查 (risk_manager.py)       │
│      └─ 交易执行 (executor.py)           │
└─────────────────────────────────────────┘
```

### 多Agent模式（run_agents.py）

```
┌──────────────────────────────────────────────────────────────┐
│                  Orchestrator（编排器）                         │
│         两层架构：研判层(4h) + 交易层(持续)                      │
└──────────┬───────────────────────────────────────────────────┘
           │ asyncio Queue 消息总线（支持 topic:symbol 路由）
           ▼
┌──────────────────────────────────────────────────────────────┐
│              研判层 Tier 1（每4小时运行，6个Agent）               │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │MarketScanner │  │ Sentiment    │  │    News      │       │
│  │OKX合约扫描   │  │恐贪+热度+Taker│  │ RSS新闻采集  │       │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘       │
│         └──────────────────┼─────────────────┘               │
│                            ▼                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ Synthesizer  │←→│   Censor     │  │ SymbolRouter │       │
│  │Claude综合研判 │  │言官逆向审查   │  │标的路由+轮换  │       │
│  └──────────────┘  └──────────────┘  └──────┬───────┘       │
└─────────────────────────────────────────────┼───────────────┘
                                              │ symbol_update
┌─────────────────────────────────────────────┼───────────────┐
│              交易层 Tier 2（持续运行，9个Agent）               │
│                                              ▼               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │DataCollector  │  │ TechAnalyst  │  │    Judge     │       │
│  │多标的数据采集 │  │多标的技术分析 │  │多标的裁判决策 │       │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘       │
│         │                  │                  │               │
│  ┌──────┴───────┐  ┌──────┴───────┐                         │
│  │  Executor    │  │PortfolioRisk │                         │
│  │  多标的执行   │  │ 组合风控盯盘  │                         │
│  └──────────────┘  └──────────────┘                         │
│                                                              │
│  ┌──────────────┐                                           │
│  │  Reviewer    │                                           │
│  │ 交易复盘+熔断 │                                           │
│  └──────────────┘                                           │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐                         │
│  │PositionAnalyst│  │BehavioralCritic│                       │
│  │持仓分析+裁决  │  │行为偏差检测    │                         │
│  └──────────────┘  └──────────────┘                         │
└──────────────────────────────────────────────────────────────┘
```

### 研判层决策流水线（两阶段）

```
MarketScanner ─────┐
SentimentResearcher─┼─→ Synthesizer(初选) → Censor(谏言) → Synthesizer(终选)
NewsResearcher ────┘                                            │
                                                                ▼
                                                         SymbolRouter
                                                                │
                                                         symbol_update
                                                                │
                                    ┌───────────────────────────┼──────┐
                                    ▼               ▼           ▼      ▼
                              DataCollector   TechAnalyst    Judge  RiskGuard
```

### 交易层决策流水线（per-symbol）

```
DataCollector
    │ [market_data:SOL-USDT]
    ▼
TechAnalyst（规则引擎 + Claude分析）
    │ [tech_analysis:SOL-USDT]
    ▼
Judge（Claude裁判 / 规则降级）
    │ [trade_decision:SOL-USDT]
    ▼
Executor（风控审核 → 执行）
    │ [execution_result:SOL-USDT]
    ▼
PortfolioRiskGuard（组合级实时监控）
```

### 持仓管理决策流水线（每1小时）

```
PositionAnalyst（7因子规则评分）
    │ [position_review:SOL-USDT]
    ▼
BehavioralCritic（LLM偏差检测 / 规则降级）
    │ [position_verdict:SOL-USDT]
    ▼
PositionAnalyst 裁决引擎（纯规则矩阵）
    │ 硬性覆盖 > 裁决矩阵 > 分析官建议
    ▼
[trade_decision:SOL-USDT] → Executor执行
```

**7因子评分**：趋势对齐(±20) + 动量变化(±20) + 时间衰减(-15~0) + 浮盈状态(±20) + 成交量确认(±10) + 剩余R:R(±15) + 入场逻辑验证(-10~+25)

**硬性覆盖规则**（无视分析官和批判官）：
- 规则1：浮亏超过SL含杠杆距离 → close（第三道防线：交易所SL→Executor 5s轮询→PA 1h周期）
- 规则2：持仓>72h+浮亏>3% → close
- 规则3：HTF趋势反转+浮亏>5% → close
- 规则3b：浮亏超过SL距离×50%+趋势非顺向 → close（入场逻辑失效早期信号）
- 规则4：浮盈>15%+动量反转 → reduce 50%
- 规则5：剩余R:R<0.3 → close

**三层止损防线**：交易所SL条件单(实时) → Executor本地5s轮询 → PA规则1(1h周期，兜底)

**防遗憾机制**：高时间框架（4h/日线）仍确认入场方向时，裁决引擎保护持仓（批判官close→reduce，reduce→hold）

**加仓/减仓执行**（2026-05-15修复）：
- 加仓：score≥50 + conviction≥70 + 保证金<上限(max_trade_amount×2) → Executor.add_to_position（加权平均入场价 + SL/TP按原距离比例重算）
- 减仓：score∈[-60,-30) 或 硬性规则4 → Executor.reduce_position（取消旧SL条件单 + 精度格式化 + 浮点兜底）
- execution_result区分：新开仓(executed) / 加仓(executed+is_add) / 减仓(risk_reduced+reduce_pct) / 全平(executed+close)
- 下游同步：RiskGuard/PositionAnalyst/TelegramNotifier均正确处理增量更新

**执行优先级**：RiskGuard强制平仓 > 硬性覆盖 > 裁决矩阵 > 分析官建议

## 核心模块

### 1. K线数据采集器 (kline_collector.py) ✅

**职责**：实时采集K线数据并存储

**实现**：
- Binance WebSocket订阅
- 支持多币种、多周期
- SQLite存储

**数据流**：
```python
WebSocket stream
  → kline{open, high, low, close, volume}
  → database.insert_kline()
  → klines表
```

**表结构**：
```sql
CREATE TABLE klines (
    symbol TEXT,
    interval TEXT,
    open_time INTEGER,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    close_time INTEGER,
    UNIQUE(symbol, interval, open_time)
);
```

### 2. 技术指标计算 (indicators.py) ✅

**职责**：基于K线数据计算技术指标

**已实现**：
- MA（移动平均线）- 支持任意周期
- EMA（指数移动平均线）
- MACD（指数平滑异同移动平均线）- 返回MACD线、信号线、柱状图
- RSI（相对强弱指标）- 14周期默认
- 布林带 - 上轨、中轨、下轨

**实现方式**：
- 使用pandas向量化操作，高效计算
- 静态方法设计，易于复用
- 支持自定义参数

### 3. 策略系统 (strategy_base.py + optimize_1h.py) ✅

**职责**：基于技术指标生成交易信号

**已实现**：
- **StrategyBase基类**：参考Freqtrade架构，三步式策略设计
  - populate_indicators()：计算指标
  - populate_entry_signals()：入场信号
  - populate_exit_signals()：出场信号
  
- **RobustStrategy稳健策略**：带反欺骗机制的趋势跟踪策略，支持多空双向
  - 做多4重确认：MA金叉 + RSI不超买(<75) + 成交量确认 + 价格上涨
  - 做空4重确认：MA死叉 + RSI不超卖(>25) + 成交量确认 + 价格下跌
  - 做多出场：MA死叉 或 RSI超买(>80)
  - 做空出场：MA金叉 或 RSI超卖(<20)
  - 最佳参数：MA 7/25，RSI阈值75，成交量因子1.0
  - 验证结果：83.3%胜率，7.68盈亏比

### 4. 回测引擎 (backtest.py) ✅

**职责**：历史数据回测验证策略

**已实现**：
- 防前视偏差：信号在第i根K线产生，在第i+1根K线开盘价执行
- 手续费计算：每笔交易扣除0.1%手续费
- 完整绩效指标：
  - 总交易次数、盈利/亏损交易数
  - 胜率、盈亏比
  - 总收益、平均收益
  - 最大回撤
- 交易详情记录：每笔交易的入场/出场价格和收益

**验证结果**：
- 多时间周期测试：1小时周期最优（46.67%胜率）
- 参数优化：找到最佳参数组合（MA 7/25，RSI 75）
- 样本外验证：测试集100%胜率，策略稳健

### 5. 合约执行器 (executor.py) ✅

**职责**：执行合约交易

**已实现**：
- 基于CCXT的统一交易接口（Binance/OKX）
- 杠杆设置、开仓/平仓、止损止盈检查
- 持仓持久化（`data/positions.json`）
- reduceOnly参数、盈亏计算含杠杆

### 6. 风控管理器 (risk_manager.py) ✅

**职责**：风险控制

**已实现**：
- 余额/回撤/每日亏损限制
- 止损止盈计算（多空双向）
- 峰值余额持久化（`data/risk_state.json`）

**硬限制**：
- 单次最大交易额：10 USDT
- 最大回撤：20%
- 每日最大亏损：50 USDT

### 7. 多Agent系统 (agents/) ✅

**职责**：两层Agent协作决策——研判层选标的，交易层执行

**核心组件**：

| 文件 | 层级 | 职责 | LLM使用 |
|------|------|------|---------|
| `base.py` | 基础 | Agent基类（生命周期、消息收发） | 提供ask_claude接口 |
| `message_bus.py` | 基础 | asyncio Queue消息总线（支持topic:symbol路由） | 无 |
| `llm_client.py` | 基础 | Claude API客户端（OpenAI兼容格式） | 核心 |
| `orchestrator.py` | 基础 | 两层编排器（研判4h周期+交易持续） | 无 |
| `research/market_scanner.py` | 研判 | OKX永续合约扫描（量/波动/费率/多空比/OI） | 无 |
| `research/sentiment_researcher.py` | 研判 | 恐贪指数+CoinGecko热度+Binance Taker比 | 无 |
| `research/news_researcher.py` | 研判 | 6家加密媒体RSS新闻采集+币种提及统计 | 无 |
| `research/synthesizer.py` | 研判 | Claude综合研判（两阶段：初选→终选） | Claude选币 |
| `research/censor.py` | 研判 | 言官逆向审查（Devil's Advocate） | Claude质疑 |
| `research/symbol_router.py` | 研判 | 标的路由+轮换协议（平仓旧标的） | 无 |
| `trading/multi_data_collector.py` | 交易 | 9维度数据采集（K线/orderbook/OI/爆仓/费率/Taker/大单/多空比） | 无 |
| `trading/tech_analyst.py` | 交易 | 9维度信号解读（趋势/价位/动量/资金流/微观结构/散户/风险） | Claude综合研判 |
| `trading/judge.py` | 交易 | 精确交易计划（统一风险预算/入场区间/止盈止损/动态杠杆1-20x/仓位/RSI极端值保护/回调入场） | Claude最终裁决 |
| `trading/executor.py` | 交易 | 多标的交易执行 | 无 |
| `trading/paper_executor.py` | 交易 | 影子账户（与实盘并行，订阅同样 trade_decision/price_tick，独立余额持久化到 data/paper_*） | 无 |
| `trading/portfolio_risk_guard.py` | 交易 | 组合级风控盯盘 | 无 |
| `trading/reviewer.py` | 交易 | 交易复盘+策略衰减+Daily Hard Stop触发 | 无 |
| `trading/telegram_notifier.py` | 交易 | Telegram实时告警+每日摘要 | 无 |
| `trading/position_analyst.py` | 交易 | 持仓7因子评分+裁决引擎（每1h） | 无 |
| `trading/behavioral_critic.py` | 交易 | 行为金融学偏差检测（7种认知偏差） | Claude检测偏差 |

**LLM降级机制**：Claude不可用时自动回退到规则引擎，系统不中断。

**基础设施组件（2026-05-24审计整改新增）**：

| 文件 | 职责 |
|------|------|
| `utils/exchange_factory.py` | 统一交易所创建工厂（sandbox/live边界隔离，所有Agent共用） |
| `utils/event_journal.py` | 关键事件JSONL落盘（trade_decision/execution_result/system_command/risk_alert），MessageBus自动触发 |
| `utils/position_reconciler.py` | 4路对账（exchange/executor/riskguard/paper），blocking vs advisory issue分离 |
| `utils/market_regime.py` | 市场Regime检测（BTC/ETH bias + 全标的趋势共识 → bullish/bearish/mixed/choppy） |
| `utils/counterfactual_ledger.py` | 被拒信号影子追踪，24h TP/SL解析验证策略有效性 |
| `requirements.lock` | 精确版本锁定，ccxt升级需走`docs/dependency_upgrade_runbook.md`门控流程 |

**研判层消息类型**：
- `research_trigger`：编排器触发研判（Orchestrator → 研判层）
- `research_market_data`：市场扫描结果（MarketScanner → Synthesizer）
- `research_sentiment_data`：情绪数据（SentimentResearcher → Synthesizer）
- `research_news_data`：新闻数据（NewsResearcher → Synthesizer）
- `research_preliminary`：初选结果（Synthesizer → Censor）
- `research_challenge`：言官谏言（Censor → Synthesizer）
- `research_result`：终选结果（Synthesizer → SymbolRouter）
- `symbol_update`：活跃标的更新（SymbolRouter → 交易层全体）

**交易层消息类型**：
- `market_data:{symbol}`：9维度数据（K线1h/4h/1d/15m+orderbook+OI+爆仓+费率历史+Taker比+大单+多空比）（DataCollector → TechAnalyst, RiskGuard）
- `price_tick:{symbol}`：10秒价格流（DataCollector → RiskGuard）
- `tech_analysis:{symbol}`：9维度信号解读（趋势/价位/动量/资金流/微观结构/散户/风险）+ 15m入场时机（TechAnalyst → Judge）
- `trade_decision:{symbol}`：精确交易计划（入场区间/止盈止损/杠杆/仓位）（Judge → Executor）
- `execution_result:{symbol}`：执行结果（Executor → RiskGuard, Reviewer, TelegramNotifier）
- `paper_execution_result:{symbol}`：影子账户执行结果（PaperExecutor → 仅记账，不触发风控）
- `risk_alert`：风控警报（RiskGuard → broadcast，Executor + TelegramNotifier响应）
- `daily_hard_stop_triggered`：熔断信号（Reviewer → broadcast，Executor + RiskGuard + TelegramNotifier响应）
- `strategy_review`：策略复盘报告（Reviewer → TelegramNotifier）
- `news_snapshot`：30min新闻快照（DataCollector → Judge，用于price-in检测）
- `position_review:{symbol}`：持仓评估结果（PositionAnalyst → BehavioralCritic）
- `position_verdict:{symbol}`：偏差检测结果（BehavioralCritic → PositionAnalyst裁决引擎）

**Symbol格式约定**：
- 消息总线（DataCollector/TechAnalyst/Judge/PA/RiskGuard）：`ZEC-USDT`（不带-SWAP）
- ContractExecutor positions dict key：`ZEC-USDT-SWAP`（`_normalize_symbol`自动添加）
- OKX API返回：`ZEC/USDT:USDT`（sync_positions自动转换为`-SWAP`格式）
- 规则：Agent层收到execution_result时strip `-SWAP`后缀，确保与tech_analysis key一致

## 数据流

### K线采集流程（已实现）

```
1. kline_collector.py 启动WebSocket连接
2. 订阅币种K线流（如BTCUSDT@kline_1m）
3. 接收K线数据
4. K线闭合时存储到数据库
5. 持续监听
```

### 交易执行流程

**单策略模式（live_trading.py）**：
```
1. 从交易所获取最新K线（降级：数据库）
2. 技术指标计算 + 信号生成
3. 风控检查（余额、回撤、每日亏损）
4. 执行合约开仓/平仓
5. 止损止盈监控
6. 60秒后重复
```

**多Agent模式（run_agents.py）**：
```
1. DataCollector 9维度采集（10s价格/30s深度+爆仓/60s全量/5min 4h K线/60s 15m K线）
2. TechAnalyst 收到数据后：规则引擎解读9维度 + 15m入场时机分析(MA7/25+RSI14) + Claude综合研判
3. Judge 收到分析后：信号聚合评分 + Claude裁决 → 精确交易计划（入场/止盈止损/杠杆/仓位）
   - 15m 入场确认：block→deferred等待转向 / confirm→通过 / neutral+强信号+HTF同向→通过
   - R:R≥1.5 → 正常入场
   - 1.2≤R:R<1.5 + 强信号(|score|≥50) → 追价入场（缩仓）
   - 1.2≤R:R<1.5 + 弱信号 → deferred_entry等回调（3h有效）
   - R:R<1.2 → 放弃
4. Executor 收到决策后：风控审核 → 执行交易
5. RiskGuard 持续监控：闪崩检测、敞口超限
```

## 配置系统

**config.yaml**：
- 交易所配置
- 交易对列表
- 技术指标参数
- 风控参数

**.env**：
- API密钥
- 敏感配置

## 日志系统

**位置**：`logs/`
**格式**：`{module}_{YYYYMMDD}.log`
**级别**：INFO

**关键日志**：
- K线数据采集
- 技术指标计算
- 交易信号生成
- 交易执行结果
- 错误和异常

## MVP开发路线

> 已完成阶段的逐项实现细节迁至 `docs/handoff.md`（完整历史演进），各特性设计见 `docs/superpowers/specs/` 与 `docs/audit_remediation_*`。下表仅留里程碑与**彼时**测试基线（历史快照，非当前基线；当前基线见 `CLAUDE.md` 与 `docs/to-do-list.md`）。

| 阶段 | 完成 | 里程碑要点 | 彼时基线 |
|---|---|---|---|
| Phase 1–4 | 2026-05-06 | 数据采集/SQLite、技术指标+策略、回测引擎（1h 周期最优、反欺骗胜率 83%）、实盘执行器+风控+OKX 验证 | — |
| Phase 5 | 2026-05-07 | 多 Agent 系统：消息总线、两层研判（6 研判 Agent）、交易层 9 维度采集/解读/Judge 计划、P0 风控（Reviewer、Daily Hard Stop、优雅停机、状态持久化） | — |
| Phase 6 | 2026-05-07~17 | 智能增强：Telegram、方向决策修复、入场质量（R:R≥1.5、负面催化否决、price-in）、日线多周期共振、Judge 主驱动、MA alignment、持仓三角决策（PositionAnalyst+BehavioralCritic）、PnL 追踪 | — |
| Phase 7 | 2026-05-19 | 4h RSI 二级衰减、逻辑账户拆分（`EFFECTIVE_BALANCE_CAP`）、Paper Trading 全并行 | — |
| Phase 8 | 2026-05-21 | 市场 Regime 优化：RegimeManager、CounterfactualLedger、Short Regime Guard + Probe Short、Low R:R Extra Slot | 329 |
| R:R Floor Policy | 2026-05-26 | 单一 `Judge._select_rr_floor`，五分支 floor 策略 + `long_aligned_low_rr` | 551 |
| Long Entry Position Guard | 2026-05-26 | 单一 `Judge._check_entry_position_policy`，long overheat 四路径共用 + EV bucket 修正 | 575 |
| 分批止盈生命周期收敛（1+2+3） | 2026-05-27 | TP/SL owner 收敛、`_replace_protective_sl` 单一入口、重启 algo 迁移 | 618 |
| OKX 真实 testnet 语义验收 | 2026-05-27~28 | T0–T15 真实链路；`cancel_algos` 序列化 bug（mock 不可覆盖，印证「mock pass ≠ live ready」红线） | — |
| 第三~五次审计整改 | 2026-05-28~06-11 | 保护单/close cause/真实 PnL 账本/owner-tag SL/reduce 失败传播/Entry Drift Hybrid/Pullback Paper Parity/Short Main Path Guard/研究层流动性硬过滤/Paper Dual-Track/Data Source Provenance + 6 项 fail-closed 加固；逐项见 `docs/handoff.md` 与 `docs/audit_remediation_*` | 807→1088 |
| Agent Health + bot LLM 隔离 + 反事实实验室 L1-L4 + 三连修 | 2026-06-12~16 | Agent Health Supervisor + tick-stall / bot LLM env 隔离 / 反事实策略实验室 L1-L4（决策磁带→回放→扰动→旋钮扫描，全 observability-only）+ 磁带捕获修 + 三连修使端到端首次可信（fidelity 0.944）+ joint-knob-sweep；逐项见 `docs/handoff.md` | 1088→1270 |
| 入场门精修 trend-entry-rr-fidelity | 2026-06-17 | 干净趋势零开仓诊断 → 两入场杠杆 ① `_select_rr_floor` path-evidence 地板（policy `long_aligned_path_evidence`）/ ② `_compute_ladder_rr` 阶梯口径 effective_rr，**两 config 开关默认关、实盘零影响**；comet 归档 2 新 capability | 1270→1285 |

各特性的硬约束（单点收口函数、字段契约）见 `CLAUDE.md ## 风控红线`。

### Phase 9: 待开发
- Predictor（趋势预测Agent）
- 更多数据源接入（链上大额转账、清算数据）
- 参数 grid search（基于 event_backtest）
- P3-R 验收测试体系

## 性能考虑

- **实时数据**：WebSocket推送，延迟<100ms
- **数据库**：SQLite适合单机MVP，后期可升级PostgreSQL
- **计算效率**：技术指标计算使用pandas向量化操作

## 安全考虑

- API密钥存储在.env，不提交代码库
- 风控硬限制：单次10 USDT，回撤20%
- 只读模式：无API密钥时仅采集数据

## 套利系统归档说明

原套利系统代码保留在以下文件中作为参考：
- `core/aggregator.py` - 行情聚合器
- `core/detector.py` - 套利检测引擎
- `depth_validator.py` - 深度验证器
- `market_scanner.py` - 市场扫描器
- `websocket_monitor.py` - WebSocket监控
- `triangular_arbitrage.py` - 三角套利

**放弃原因**：2026-05-06全面验证，所有测试0次机会，市场效率极高，成本>收益。
