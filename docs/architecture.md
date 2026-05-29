# 系统架构文档

## 概述

加密货币趋势交易系统，基于技术分析和合约交易，支持多AI Agent协作决策。

**当前状态（2026-05-28）**：主入口为 `run_agents.py`，全量回归 `699 passed / 4 deselected / 1 warning`。R:R Floor 选择已统一收敛到 `Judge._select_rr_floor`，Long Entry Position Guard 收敛到 `Judge._check_entry_position_policy`。OKX 真实 testnet 语义验收 2026-05-28 完成：long_short_mode 子账户跑 T0-T15 13 PASS / 3 SKIP（T0/T1/T4-T6/T8-T15 PASS），net_mode 切换后单独跑 T0/T2/T3 3 PASS（verify_okx_testnet_real.py 的 T2/T3 已 self-contained，main loop pre-cleanup 不再抹掉前置仓位）；2026-05-28 审计 P0（保护单 owner、SL cancel failure、Agent close path、close cause）+ P1（FR-005 BehavioralCritic 字段统一、FR-007 network 测试限时、FR-008 STATE_NAMESPACE 状态命名空间）代码 + 单测 + testnet T10-T15 真实补验已闭环，T2/T3 net_mode caveat 解除，T7 mock_only by design（已 mock 矩阵 PASS）。下方"重要变更"是历史时间线，不代表当前待办状态。

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

### Phase 1: 数据基础 ✅ (2026-05-06完成)
- K线数据采集器
- SQLite存储

### Phase 2: 技术分析 ✅ (2026-05-06完成)
- 技术指标计算（indicators.py）
- 策略基类设计（strategy_base.py）
- 稳健策略实现（optimize_1h.py）

### Phase 3: 回测验证 ✅ (2026-05-06完成)
- 回测引擎（backtest.py）
- 多时间周期测试（compare_timeframes.py）
- 参数优化（optimize_1h.py）
- 样本外验证（validate_out_of_sample.py）

**关键发现**：
- 1小时周期最优，1分钟/15分钟不盈利
- 反欺骗机制使胜率从46.67%提升至83.3%
- 最佳参数：MA 7/25，RSI阈值75，成交量因子1.0
- 样本外验证通过，策略稳健

### Phase 4: 实盘交易系统 ✅ (2026-05-06完成)
- 合约执行器（executor.py）
- 风控管理器（risk_manager.py）
- 实时交易系统（live_trading.py）
- OKX真实账户验证通过

### Phase 5: 多Agent系统 ✅ (2026-05-07完成)

**Phase 5a - 基础框架**：
- 消息总线（asyncio Queue，支持topic:symbol路由）
- Agent基类 + 编排器
- Claude API客户端（OpenAI兼容格式，中转站支持）
- 5个核心交易Agent：DataCollector、TechAnalyst、Judge、Executor、RiskGuard
- LLM降级机制

**Phase 5b - 两层研判架构**：
- 研判层6个Agent：MarketScanner、SentimentResearcher、NewsResearcher、Synthesizer、Censor、SymbolRouter
- 交易层5个Agent升级为多标的并行处理
- 两阶段决策：Synthesizer初选 → Censor谏言 → Synthesizer终选
- 标的轮换协议（旧标的平仓、新标的接入）
- 数据源：OKX 324合约扫描 + 恐贪指数 + CoinGecko热度 + Binance Taker比 + 6家RSS新闻

**Phase 5c - 交易层深度升级（2026-05-07）**：
- DataCollector 9维度采集：K线(多周期) + Orderbook 20档 + 资金费率历史 + OI delta + 爆仓订单 + Taker买卖比 + 大单检测 + 多空账户比 + 实时价格流
- TechAnalyst 9维度信号解读：趋势结构 + 关键价位(含orderbook墙) + 动量(RSI背离) + 资金流向(OI背离/费率极值) + 微观结构(鲸鱼/深度偏向) + 散户反指 + 风险评估
- Judge 精确交易计划：7维度加权评分 + 基于支撑阻力的止盈止损 + 动态杠杆1-20x + RSI极端值保护 + 反欺骗/反人性决策
- 反欺骗验证通过：诱多陷阱识别、恐慌底部反人性做多、假突破拒绝、杠杆过热拒绝、主力洗盘识别

**Phase 5d - P0风控增强（2026-05-07）**：
- ReviewerAgent：交易历史追踪 + 滚动窗口指标（胜率/盈亏比） + 策略衰减检测
- Daily Hard Stop：双重熔断（单日亏损≤-50 USDT 或 连续3次亏损）
- Graceful Shutdown：SIGINT/SIGTERM信号处理 + 状态保存 + 优雅停机
- RiskGuard状态持久化：持仓追踪/价格缓存/熔断状态重启恢复
- Executor/RiskGuard升级：动态杠杆+限价单+条件单 + risk_alert接入强制平仓

**Phase 6 - 智能增强（2026-05-07~08）**：
- P1-A Telegram通知：TelegramNotifier实时推送+每日摘要+零配置降级
- contractSize修复：`amount = (size_usdt * leverage) / (price * contract_size)` + `amount_to_precision()`
- 方向决策修复（2026-05-08）：_compute_score重写，RSI极端值硬性保护+趋势强度衰减+条件化散户反指+RSI背离权重提升
- 系统逻辑校验：止损最小距离1.5%、组合回撤用保证金计算、止盈orderbook墙逻辑修复

**Phase 6e - 入场质量优化（2026-05-09）**：
- Post-mortem修复：correlation_risk改用保证金计算（非名义价值），Judge force_close冷却300s
- R:R门槛：`risk_reward_ratio < 1.5` → hold（参考Freqtrade minimal_roi原则）
- 负面催化剂否决：synthesizer近4h新闻关键词检测 → confidence=0 → censor reject（veto层设计）
- 30min新闻轮询：DataCollector新增`_tick_news()`，发布`news_snapshot`消息到交易层
- price-in检测：Judge订阅`news_snapshot`，近4h有新闻+价格移动>3% → score×0.5

**Phase 6f - 日线多周期升级（2026-05-09）**：
- DataCollector：`_collect_1d()` 每慢周期采集30根日线K线，payload新增`klines_1d`
- TechAnalyst：`_analyze_trend()` 新增日线偏向+`daily_near_resistance/support`检测（距20日高低点**1.5%**以内）
- TechAnalyst：多周期共振投票（1h+4h+1d三周期一致+20强度，矛盾-20）；4h RSI计算
- TechAnalyst：`_analyze_levels()` 新增日线swing支撑阻力（更可靠的止损止盈锚点）
- Judge：接近日线阻力区（1.5%以内）做多信号衰减70%（防假突破）；接近日线支撑区（1.5%以内）做空信号衰减70%（防反弹陷阱）
- Judge：止损优先用日线价位锚点（daily_support/daily_resistance）
- Synthesizer：放开标的限制（含XAU/CL等非加密标的），波动率范围扩至50%，成交量门槛降至$30M
- MarketScanner：并发enrichment（asyncio.gather替代串行循环）

**Phase 6g - Judge主驱动修复（2026-05-09）**：
- 根因：rule_signal（回测83%胜率的MA交叉信号）未参与评分，系统永远hold
- 修复：rule_signal触发时给±35基础分，确保过30分入场门槛
- LLM从一票否决改为仓位修正：rule_signal触发时LLM最多降30%仓位，不能阻止入场
- 无rule_signal时保持原有保守逻辑（LLM可否决弱信号）

**Phase 6h - MA alignment信号 + Symbol sync修复（2026-05-11）**：
- 根因：MA crossover是点事件，crossover后下一根K线entry_short=0，score≈0，系统永远hold
- 修复：tech_analyst.py新增`ma_aligned_long/short`（MA fast/slow已对齐≥3根K线），judge.py给±20基础分作为次驱动
- Symbol sync修复：executor.py sync_positions将OKX格式`BASE/USDT:USDT`自动转换为内部格式`BASE-USDT-SWAP`，防止每次sync循环删除并重建持仓
- 首次成功开仓：LAYER-USDT short @ 0.12171，3x杠杆
- 止损止盈计算修复（2026-05-13）：SL距离ATR封顶（2.5倍ATR，max 5%，Turtle Traders方法论）；TP下限=SL×1.5（保证R:R≥1.5）；R:R硬性门槛1.5（不因confidence高而放松）。根因：旧公式用confidence动态计算min_rr，LLM提升confidence到65时min_rr降至0.538，R:R=0.6即可通过

**Phase 6i - 持仓管理三角决策 + flash_move修复（2026-05-12）**：
- PositionAnalyst：6因子规则评分（趋势对齐/动量变化/时间衰减/浮盈状态/成交量确认/剩余R:R），每30min评估所有持仓
- BehavioralCritic：LLM检测7种认知偏差（loss_aversion/sunk_cost/anchoring/fomo/disposition/overconfidence/panic），LLM不可用时规则降级
- 裁决引擎（内嵌PositionAnalyst）：5条硬性覆盖规则 + 4级severity裁决矩阵（none/low/medium/high）
- flash_move修复：从全平所有持仓改为只平触发标的（单币闪崩≠系统性风险）
- Synthesizer扩容：初选上限3→12，增加机会面供Censor筛选
- 持仓监控补充：DataCollector自动将持仓标的纳入监控（即使不在SymbolRouter活跃列表）
- 交易层Agent数量：7→9（新增PositionAnalyst + BehavioralCritic）

### ✅ Phase 7: 4h RSI 衰减 + 逻辑账户拆分 + Paper Trading（2026-05-19完成）
- **4h RSI 二级保护**（`judge.py _compute_score` 末尾）：1h RSI 未触发硬cap但 4h RSI ≥70/≤30 时 score×0.5。根因 ZEC 事故（1h RSI=64 但 4h=73.9 仍开多 20x→-135 USDT）
- **逻辑账户拆分**（`utils/config_loader.py` + `judge.py _calc_risk_budget`）：新增 `EFFECTIVE_BALANCE_CAP` 环境变量，真实余额 6020 USDT 但风控按 1000 USDT 计算，单笔 max_loss 250→50 与 Daily Hard Stop -50 对齐。cap=None 时等价旧逻辑
- **Paper Trading 全并行**（`agents/trading/paper_executor.py` 新建 ~340 行）：与 MultiExecutor 并行运行，订阅同 `trade_decision:*` 和 `price_tick:*`，独立 in-memory 余额持久化到 `data/paper_*`，发布独立 topic `paper_execution_result` 不污染实盘
- **交易层Agent数量**：9→10（新增 PaperExecutor）

### ✅ Phase 8: 市场 Regime 优化（2026-05-21完成）
- **RegimeManager**（`utils/market_regime.py`）：BTC/ETH bias + 全标的趋势共识 → bullish/bearish/mixed/choppy，2次确认切换 + 30min min_hold 防抖
- **CounterfactualLedger**（`utils/counterfactual_ledger.py`）：仅追踪被 Judge 拒绝且已形成 plan 的信号，记录 shadow_tp/shadow_sl/shadow_expired/shadow_invalidated
- **Short Regime Guard**（`agents/trading/judge.py`）：牛市普通做空拦截，强做空（score≤-70, htf≥2, rr≥1.8, 15m confirm）放行
- **Probe Short**：牛市中允许小仓位探针做空（30% position, 3x leverage, 24h cooldown），同时要求 pending probe 与流动性检查
- **Low R:R Extra Slot**（`utils/candidate_ranker.py`）：低 R:R 多头使用独立额外槽位，不挤占主槽位，rank score 打 70% 折扣
- **验证**：329 passed / 4 deselected / 0 failed

### ✅ R:R Floor Policy 修复（2026-05-26完成）
- **背景**：INJ-USDT 类信号（R:R≈1.45, score=45, choppy regime, trend bullish, daily bullish）被默认 1.50 floor 拦截。Judge 主路径直接对比 `min_rr_threshold`，`_apply_regime_policy` 又重写一份 if/else，两边可能漂移。
- **统一函数**（`agents/trading/judge.py: _select_rr_floor(action, plan, tech, score)`）：唯一入口，主路径与 deferred 路径共用，按顺序匹配 `probe` / `long_bullish_low_rr` / `long_aligned_low_rr` / `short_bullish_strong` / `default` 五个分支并返回 `(min_rr, rr_policy, rr_floor_reason)`。修改 R:R floor **必须改这一处**。
- **新策略 `long_aligned_low_rr`**：mixed/choppy regime 下，仅当 `trend.direction=bullish` AND (`htf_bias=bullish` OR `daily_bias=bullish`) AND 未 `block_long` AND `|score|≥min_deferred_signal_score` 时使用 `RR_FLOOR_LONG_ALIGNED_CHOPPY=1.30`，进 low_rr_extra slot；不放宽空头。
- **配置化阈值**（`utils/config_loader.py`）：`RR_FLOOR_DEFAULT=1.5` / `RR_FLOOR_LONG_BULLISH=1.30` / `RR_FLOOR_LONG_ALIGNED_CHOPPY=1.30` / `RR_FLOOR_SHORT_BULLISH=1.80` / `PROBE_RR_FLOOR=1.30` / `LOW_RR_LONG_ALIGNED_ENABLED=true`，全部进 HARD_LIMITS + env_map + banner。
- **probe 路径**：`PROBE_RR_FLOOR` 替换硬编码 `1.30`，`_can_route_probe_short` / 主路径 / deferred 路径全部从同一函数取值。
- **Attribution 全链路**：`trade_decision.attribution` 新增 `rr_floor_used` / `rr_floor_reason` / `symbol_trend` / `symbol_higher_tf_bias` / `symbol_daily_bias`；被拒决策同样带这五个字段，落 `data/journal/events_*.jsonl`。
- **测试**：新增 `test_rr_floor_policy.py` 20 个 case，覆盖 AC-RR-01..09。
- **验证**：551 passed / 4 deselected / 0 failed
- 详见 `docs/rr_floor_policy_prd.md` / `docs/rr_floor_policy_acceptance.md`。

### ✅ Long Entry Position Guard（2026-05-26 完成）
- **背景**：NEAR-USDT 2026-05-26 14:47 通过 `long_bullish_low_rr` 在 range_pos=0.838 / prev_daily=+15.66% 山顶位置追多。`pending_pullback`（RSI ≥ 70）和 `deferred_15m_confirmation` 都无法覆盖这种"位置过高但 RSI 中性"的多头入场。
- **TechAnalyst 输入**（`agents/trading/tech_analyst.py`）：新增 `entry_context.{position_in_24h_range, pre_12h_return_pct, prev_daily_return_pct}`，保留 `short_context` 兼容旧消费方。
- **统一函数**（`agents/trading/judge.py: _check_entry_position_policy(symbol, action, plan, tech, score, context)`）：long overheat 与 short side guard 的唯一入口，主开仓路径与 `deferred_15m_confirmation` / `deferred_pullback` / `deferred_chase` 三条 deferred 路径共用。**禁止**在 deferred helper 内重写 overheat 判定。
- **触发阈值**：`range_pos>=0.82` 或 `pre_12h>=0.05 ∧ range_pos>=0.75` 或 `prev_daily>=0.10 ∧ range_pos>=0.75`，分别返回 `long_overheat_range_pos` / `long_overheat_pre_move` / `long_overheat_daily_gain`。
- **处理策略**：有效 target（`stop_loss < target < signal_price`，target = `max(stop_loss*1.005, signal*(1-max(LONG_LIVE_PULLBACK_MIN_PCT, atr_pct)))`）→ 创建 `deferred_pullback_overheat`（`chase_eligible=false`，timeout `LONG_LIVE_PULLBACK_TIMEOUT_HOURS`）；target 无效 → 直拒 `long_overheat_no_valid_pullback_target`。deferred 触发后必须重新执行 HTF/15m/RR/EV/Entry Position Guard/slot gate 全套二次确认。
- **EV bucket 修正**：`plan.entry_type` 前移到 `_check_expected_value` 之前，消除 `unknown` bucket key；新增 `EV_BUCKET_MIN_TRADES=10` / `EV_BUCKET_SPARSE_ALLOW_UPLIFT=false`，sparse bucket 禁止抬高 `p_win`，可降仓 / 缩仓。
- **配置化阈值**（`utils/config_loader.py`）：`LONG_LIVE_POSITION_GUARD_ENABLED=true` / `LONG_LIVE_MAX_RANGE_POS=0.82` / `LONG_LIVE_MAX_PRE_MOVE=0.05` / `LONG_LIVE_MAX_DAILY_GAIN=0.10` / `LONG_LIVE_DAILY_GAIN_RANGE_POS=0.75` / `LONG_LIVE_PULLBACK_MIN_PCT=0.025` / `LONG_LIVE_PULLBACK_TIMEOUT_HOURS=4` / `LONG_LIVE_OVERHEAT_DISABLE_CHASE=true` / `EV_BUCKET_MIN_TRADES=10` / `EV_BUCKET_SPARSE_ALLOW_UPLIFT=false`，全部进 HARD_LIMITS + env_map + banner。
- **Attribution 全链路**：`trade_decision.attribution` 新增 `entry_position_status` / `entry_position_block_reason` / `entry_range_pos_24h` / `entry_pre_12h_return_pct` / `entry_prev_daily_return_pct` / `entry_position_policy=long_overheat_v1` / `deferred_target_price` / `deferred_reason` / `ev_bucket_key` / `ev_bucket_trade_count` / `ev_bucket_min_trades` / `ev_bucket_sparse` 共 12 个 optional 字段；被拒决策同样带，落 `data/journal/events_*.jsonl`。
- **回测同构**（`event_backtest.py`）：新增 `long_live_*` 构造参数与 `_check_entry_with_regime` overheat 检查；`prev_daily_return_pct` 列由 `close.pct_change(24)` 预计算。
- **测试**：新增 `test_long_entry_position_guard.py` 23 个 case，覆盖 AC-LONGPOS-01..17（NEAR 复现、三组阈值触发、target 无效拒绝、chase 禁用、四路径一致性、short side guard 主路径生效、bucket key 真实、稀疏 bucket 不 uplift、trade_decision.v2 兼容、回测同构、配置 + banner、审计字段）。
- **验证**：575 passed（彼时基线，2026-05-27 阶段 3 后升至 618 passed）/ 4 deselected / 0 failed
- 详见 `docs/long_entry_position_guard_prd.md` / `docs/long_entry_position_guard_acceptance.md`。

### ✅ 分批止盈生命周期收敛 阶段 1+2+3（2026-05-27 完成）
- **背景**：`_build_okx_attach_algo` 同时挂 SL+TP 触发"OKX 把 TP 也算成保护单"，加仓时 `protection_state` 误判；`_update_trailing` 在 trailing 触发时直接 mutate `tp_filled` / SL，不等 reduce 真实成交；多个 SL cancel/place 路径并存。
- **阶段 1（FR-01/FR-03/FR-06 热修止血）**：`_build_okx_attach_algo` 移除 TP 字段；`check_stop_loss_take_profit` gate legacy scalar TP 当 `take_profit_levels` 存在；`_update_trailing` 不再 mutate `tp_filled` / SL，仅返回 `partial_tp_n` 信号；`reduce_position(tp_advance)` 在 reduce 成功后才推进 `tp_filled` 并 `_move_sl` 锁利位；`_try_acquire_exit_lock` 串行化 close/reduce/partial_tp/risk_alert。
- **阶段 2（FR-02/FR-04/FR-05 保护单 owner 收敛）**：position 加 `exit_owner` / `sl_algo_id` / `sl_algo_clord_id` / `sl_sync_state` / `protection_state`；`_make_sl_clord_id` + `_resolve_attached_sl_algo_id` 让 smart_open 通过 `attachAlgoClOrdId` 回查 algoId；`_replace_protective_sl` 单一入口替代所有 SL cancel/place；保护失败 OKX live 触发 `_halt_symbol(reason='sl_replace_failed')`；`add_to_position` 在 `protection_state != protected` 时拒绝。
- **阶段 3（FR-07 重启 / sync 时存量 algo 迁移）**：`_list_pending_algos` / `_cancel_algo_by_id` / `_migrate_okx_algos_for_symbol` / `_migrate_all_symbols_algos`；`sync_positions` 末尾自动调一次；TP algo 一律撤；唯一 SL algo 归属本地 position 写 `sl_algo_id` + 同步 `stop_loss`；无 SL / 多 SL / side 冲突 → live halt（reason `migrate_missing_sl` / `migrate_multiple_sl` / `migrate_sl_side_conflict`）；本地无仓位的 SL 视为 orphan 全撤。
- **测试**：`test_partial_tp_lifecycle.py` 32 cases（AC-A2/A3/A6/A9 + FR-04 single-entry + FR-05 add 阻断 + AC-A7 algo 迁移）。
- **验证**：618 passed / 4 deselected / 0 failed。
- 详见 `docs/partial_tp_lifecycle_prd.md` / `docs/partial_tp_lifecycle_acceptance.md`。

### ✅ OKX 真实 testnet 语义验收（2026-05-27 完成）
- **范围**：T0–T9 共 10 case 中 7 PASS（T0 配置探测 / T1 long_short_mode 多空双向 / T4 reduceOnly close / T5 51169 already_flat 拒单 / T6 SL 替换流程 / T8 algo 重启迁移 / T9 attachAlgoClOrdId 回查），3 SKIP（T2/T3 需 net_mode 账户、T7 仅 mock_only 已在 mock 矩阵 PASS）。
- **关键 bug 修复**：`_cancel_protective_sl` / `_cancel_algo_by_id` 改走 `cancel_orders([id], symbol, params={'trigger': True})`——直接 `private_post_trade_cancel_algos` 传 dict 或 list 都被 OKX 拒成 50002 "Incorrect json data format"，mock 测试无法覆盖（mock 不还原 OKX 真实序列化路径）。这是必须靠真实 testnet 才能暴露的故障，验证了"mock pass ≠ live ready"的红线。
- **工具**：`verify_okx_testnet_real.py`（10 case 顺序执行，含 `_wait_flat` 轮询覆盖 OKX testnet 状态同步延迟，`_safe_close_remaining` 兜底 reduceOnly + 清空 `IdempotencyGuard`） + `.env.testnet`（隔离 testnet 凭证 + 独立 `data/testnet_positions.json`）；测试 mock 同步更新（`test_partial_tp_lifecycle.py TestAlgoMigration` 3 case 改 mock cancel_orders）。
- **报告**：`docs/generated_reports/OKX执行语义testnet验收报告_20260527_150518.md`（T0-T9）；`docs/generated_reports/OKX执行语义testnet验收报告_20260528_063307.md`（全量 T0-T15 13 PASS / 3 SKIP，含 T10-T15 保护单/close cause 补验）。
- **结论更新**：2026-05-28 P0 整改代码 + T10-T15 真实补验、P1 整改 + T2/T3 net_mode 补跑均为上一轮历史验收记录；第三次审计 P0/P1/P2 整改已闭环（FR-3A/3B/3C/3D，807 passed），但第四次审计（2026-05-28 晚）新增 F4-001 reduce 失败回参误广播 risk_reduced（P0）/ F4-002 pnl_resolved evidence 透传不完整（P1）/ F4-003 owner tag 未用于真实 OKX SL 下单（P1）三阻断未闭环；当前 live 扩容 NO-GO。详见 `docs/audit_remediation_third_pass_20260528_prd.md` / `docs/audit_remediation_third_pass_20260528_acceptance.md` 与 `docs/generated_reports/系统性审计报告_20260528_第四次.md`。

### ✅ 第三次审计 P0/P1/P2 整改（2026-05-28 完成，807 passed）
- **FR-3A `reduce_position()` fail-closed**：`executor.py:2427-2724` 撤旧 SL 失败立即返回 `sl_cancel_failed` / 不清旧 ID / live OKX `_halt_symbol`；reduce reject 后尝试 `_replace_protective_sl` restore 原 SL；任意 reduce 成功后 residual 必重挂 SL 失败标 `protection_state=unknown` 阻断 add/open/reduce。结构化结果含 `protective_update_state/protection_state/halt_required/cancel_ok/reduce_ok/replace_ok/old_sl_algo_id/new_sl_algo_id/sl_sync_state/warnings/reason`。`test_reduce_protective_sl_lifecycle.py` 14 case PASS。
- **FR-3B `_cleanup_protective_orders_on_close()` owner-bound sweep**：新增 `_make_owner_tag_clord_id(symbol)` 生成 `ca + ns(STATE_NAMESPACE) + bot(BOT_INSTANCE_ID) + base + random`（OKX ≤32 chars 字母数字限制）+ `_is_owner_clord_id(clord)` 按 prefix 判定。`_cleanup_protective_orders_on_close()` 三层 owner 判定（已知 sl_algo_id / exact sl_algo_clord_id / owner-prefix）；foreign / unknown algo 不撤、写 `state=foreign_algos_present` + `halt_required=True` + live `_halt_symbol(reason='foreign_algos_present')` 阻断同 symbol 新开仓；`close_position()` 透传 `result.protective_cleanup`（含 cancelled/owned/foreign/unknown_count/warnings）+ `protective_cleanup_state ∈ {cleaned/none/failed/foreign_algos_present/unknown}`。`test_protective_cleanup_owner.py` 12 case PASS。**注**：F4-003 第四次审计发现 owner-tag clOrdId 仅在 cleanup 路径用，**真实 SL 下单**仍用 legacy `_make_sl_clord_id`（`sl + base + random`）；待修复后 owner-prefix sweep 才能完整生效，目前回退为 known_id exact 匹配。
- **FR-3C `pnl_resolved` final close cause 证据 + 幂等**：`utils/realized_pnl_resolver.py::_classify_close_evidence(close_fills, bills, snap)` 输出 `final_close_cause/match_rule/confidence/matched_*_id`，规则：sl_algo_id_exact / sl_algo_clord_id_exact / tp_algo_id_exact / tp_algo_clord_id_exact / bills_liquidation_subtype / close_fills_unmatched(manual) / 默认 external_unknown；仅 `exchange_sl` + `confidence>=0.9` 才 `is_strategy_stop=True`。Judge / Reviewer 按 `correction_event_id|position_id` 幂等去重 LRU set（max 1024）；Judge `_probe_short_sl_count` 受 `is_strategy_stop` 门控（仅 exchange_sl 计数；即便 PnL<0 但 close_cause=external_unknown/manual_close 不计）；legacy payload 缺字段 fail-safe 不计 SL。`test_external_close_final_cause.py` 11 case PASS（含 probe_short 门控扩展）。
- **FR-3D 新闻 ticker 边界匹配**：新增 `utils/symbol_mentions.py` 三 helper（`match_symbol_in_text(symbol, text)` / `extract_symbol_mentions(headlines, symbols, *, now_ts)` / `filter_relevant_headlines(headlines, base, *, now_ts)`）。五条规则 cashtag(1.0) / paren(0.95) / pair(0.95) / keyword(0.85) / word(0.6) + 正则边界 `(?<![A-Z0-9])SYM(?![A-Z0-9])`；`_HIGH_AMBIGUITY_BARE_WORD = {TON, ARB, NEAR, DOT, FIL, OP, FET}` 不放行 word 规则（必须 cashtag/paren/pair/keyword 命中）。`agents/research/news_researcher._extract_symbol_mentions` 与 `agents/trading/multi_data_collector._refresh_news_cache` 都改走 helper；输出 `confidence/match_rule/source/freshness_sec` provenance。`test_symbol_mentions.py` 33 case PASS。
- 详见 `docs/audit_remediation_third_pass_20260528_prd.md` / `docs/audit_remediation_third_pass_20260528_acceptance.md`。

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
