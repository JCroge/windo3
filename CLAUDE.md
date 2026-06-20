# Crypto Trading System - AI 协作指南

## 当前事实

- 当前系统是多 Agent 加密货币趋势交易系统，不是跨交易所套利系统。
- 生产、paper、testnet、实盘验收主入口统一为 `python3 run_agents.py`。
- `main.py` 和 `live_trading.py` 是归档/调试路径，不能作为生产入口。
- 当前基线（2026-06-20 全量实测）：`1338 passed / 8 failed / 4 deselected`。**8 failed = `test_round2_probe_long_dispatcher.py`(4) + `test_round2_request_id_position.py`(4)，全量运行的 asyncio event-loop 测试间污染（`RuntimeError: no current event loop`），隔离单跑全 PASS、base-ref 亦同批失败 → 非任何 change 引入。** 历史基线 `1331`（2026-06-20 前两 change）、`1314`（2026-06-18）、`1302`（2026-06-17）。
- **2026-06-20 连归 3 个 comet change（基线 1314→1338；前 2 个 observability-only 不碰 live，第 3 个改 live executor.py 需手动重启 live）**：(1) **`fix-shadow-logger-replay-baseline-parity`——影子记录器 lever1 增量口径修正**（详见下方风控红线影子记录器条目）：原 `live(real) vs replay(both)` 混入复盘偏差，改两臂同复盘 + baseline 复现自检闸；实证 37 条 shadow_holds 全是复盘失真、**lever1 真实增量=0**。(2) **`ev-decouple-forward-ab`（新 capability，新驱动 `cf_ev_decouple_ab.py`）——复核胜率解耦放行单前向期望**（详见下方风控红线条目）：真跑 69 accept→38 解耦放行（69% 只因解耦才过门），但两桶 CF 结算均 INSUFFICIENT_SAMPLE 诚实门拒答，**suggestive 读数证伪"解耦放行更差"假设（解耦放行 −0.35R/簇 反优于双门皆过 −0.80R/簇）→ 近期负收益不能干净归因到胜率解耦**；常驻 harness 数据累积后重跑。(3) **`fix-phantom-position-resync`（MODIFIED `position-sync-resilience`，改 live executor.py）——仓位同步补录双确认**（详见下方风控红线条目）：修 `sync_positions` 平仓后从交易所滞后快照补录幽灵持仓（60s `_close_cooldown` 被 OKX 76s 上报延迟击穿，近 3 天复发 3 次 UNI/XLM/XRP）；双确认 persist-2-ticks + protection-unknown 告警去重 + migrate_missing_sl halt 自愈；20x 杠杆查明=`_calc_risk_budget` 恒定风险公式按设计非 bug。**2026-06-20 复盘实盘**：真实余额=1732 USDT（用户手动出金后确认，旧 ~3994 作废）；60 笔已平仓累计 −20.29U/胜率 21.7%、近期负收益期（XLM −10.09 拖累），属市场态+边缘信号非单一门可调。
- **2026-06-18 下午又连归 2 个 comet change（全流程归档入 main，已重启 live PID 15057 ~18:29 加载）**：① **`rotation-respect-position-hold`（+11 `test_rotation_respect_position_hold.py`，新 capability `symbol-rotation-position-guard`，改 live 行为）——轮换尊重持仓研判**：SymbolRouter 标的轮换时对**仍持仓**的标的保留在 active 集（B-revised，不进 removed、不发平仓），出场决策交回 PositionAnalyst；config `rotation_close_held_enabled`（默认 **false**=不强平持仓=保护，env `ROTATION_CLOSE_HELD_ENABLED=true` 回滚旧强平）；fail-safe 读持仓失败→退化旧强平（flat 安全）；启动 banner 加「轮换强平持仓: 关闭」。根因=轮换路径从未查持仓、越权砍掉 PA 判 hold 的持仓右尾（XLM 实证：三次判 hold 仍被轮换平在低点、事后涨 +1.33%）。② **`fix-cf-lab-fidelity-epoch-resolution`（observability-only，MODIFIED `deterministic-replay-harness`）——CF 实验室保真度纪元解析修复**：`replay_decision` 改**四层合并** `production_base < _EPOCH_FALLBACK(缺键录制纪元默认) < config_snapshot(录值优先) < 扰动override(顶层)`，修磁带横跨两纪元致全局 pin 系统性发散；残余根因=`_install_config_flags` 漏还原 `_ev_winrate_gate_enabled`/`_ev_neutral_p_win`（ev_gate `getattr` 默认 True 强制门开）已补；**可信度判据改为 accept/reject 二元保真 ≥0.95（硬门，实测 0.996）**，gate 严格保真降为诊断（实测 0.73→**0.969**）；加纪元守卫测试（缺键须 ⊆ `_EPOCH_FALLBACK ∪ _GATE_IRRELEVANT`，防默认翻转静默复发）。**用 CF lab 看 accept/reject 非 gate 严格保真。****2026-06-17 当天在 1285 之上连归 4 个 comet change 并已重启 live 加载新代码（PID 46766，~20:45；资金 cap 仍 300）**：① `cf-lab-driver-portfolio-param-parity`（observability，CF 驱动组合参数对齐 live −300/300，+0 测试）；② **`trend-entry-levers-default-on`（+3，**改 live 开仓决策**）——lever2 阶梯 effective_rr 口径修正默认开**（config `ladder_rr_enabled` 默认 True，env `LADDER_RR_ENABLED=false` 可即时回滚；lever1 `path_evidence_aligned_enabled` **仍默认关**）；③ **`trend-entry-shadow-decision-logger`（+10，observability-only **不碰 live**）——前向影子决策记录器**：每信号在决策磁带 chokepoint 复用 `replay_decision` 旁路跑 both-levers 影子决策，write-only 记 real(lever2-only) vs shadow(both)=**lever1 增量**到 `data/shadow_decision_log.jsonl`（config `shadow_decision_logger_enabled` 默认 True / env `SHADOW_DECISION_LOGGER_ENABLED=false` 可关），fire-and-forget fail-safe 绝不破 live，填 lever1 path-evidence 数据墙；④ **`fix-lever2-low-rr-sizing-tp1`（+4，hotfix 改 live）——lever2 sizing 副作用修复**：低 R:R 保护性缩仓判定改用 **TP1 口径 `effective_rr_tp1`**（不被阶梯松绑），单一收口 `_apply_low_rr_sizing`（地板 gate 仍用阶梯口径多开仓不变）。lever2 定价=干净趋势 P(达TP2)68%/R:R 频率不敏感/rejected 流 A/B 含亏单 +0.181R/簇=**是 bug 非赌**。**以下为 1285 及之前历史**：1285 = 1270 之上叠加 **`trend-entry-rr-fidelity` +15**：诊断"干净趋势零开仓"（regime 判 choppy + bias 漏报 → 拿 default 1.50 而非 long_aligned 1.30；`effective_rr` 只数 TP1 而 executor 实际 50/25/25 阶梯离场）→ 实现两入场杠杆（彼时两 config 开关均默认关；lever2 现已默认开+上 live 见上）：① `_select_rr_floor` path-evidence OR 分支（policy `long_aligned_path_evidence`，禁前视，已接两处 `low_rr_policies`）/ ② `_compute_ladder_rr` 离场比例加权 effective_rr（Option B 无概率折扣——概率折扣只缩分子不缩阶梯化后风险分母会把 R:R 反向压低）。CF 重放四臂 A/B inconclusive；lever2 在 `rejected_signal_events` 流忠实 A/B 单笔含亏单净 +0.21R/簇但样本薄（13% 覆盖/近3天）→ 保持默认关。加 rejected 流 `tech_context` 埋点供 lever1 日后验证。comet 归档，2 新 capability `trend-aligned-rr-floor`+`ladder-weighted-rr`。后续拆出：① P2 bias 根治 / ① lever1 A/B / ② v2 概率校准 / ② 组合 slot/EV 瓶颈诊断。1270 = 1255 之上叠加**多旋钮联合扫描** `joint-knob-sweep` +15：新模块 `utils/joint_knob_sweep.py`（`sweep_grid` 笛卡尔积扫描 baseline 臂单次复用 / `compute_interactions` 2-way 因子交互项 synergy·additive·antagonism + 锚点自检 / `recommend_direction_nd` 多维轴邻居孤峰守卫），对 `sequential_perturbation.py` 仅纯提取 `_summarize_arm`（行为不变）；observability-only，红线守卫扩展。真跑 853 条磁带 fidelity 0.947 **全 additive 无交互**：rr_floor_default × min_confidence 联合放宽翻转 90% 决策 gate-label 仍 PnL delta=0/CF opens=2 → 证伪「被另一门掩盖」假设，独立佐证地板 1.50 维持。1255 = 1238 之上叠加**反事实实验室三连修**：`fix-cf-lab-ev-coldstart-deadlock` +9 → `fix-cf-lab-replay-config-parity` +5 → `fix-cf-lab-symbol-state-injection` +3，均 observability-only，comet 全流程归档入 main）。**三连修使 L3b 实验室端到端首次跨可信线**：驱动 `cf_direction_recommendation.py` baseline_fidelity 1.0(虚假死锁)→0.34→0.798→**0.944（untrustworthy=False）**；依次修：CF EV-gate 冷启动死锁（CF rolling 胜率窗口镜像 Reviewer + 暖启动播种 + gate-level fidelity）/ replay config parity（replay 用生产 config 基线 `production_base_config`，决策磁带录 `config_snapshot` schema v3）/ `_inject_cf_state` 还原录制 `_symbol_state`（信号强度上下文）。**实验室首个可信结论**：放宽 choppy R:R 地板 / `min_confidence` 的 PnL delta≈0 → 非高价值杠杆，独立佐证地板 1.50 维持。1238 = 1223 + `decision-tape-capture-fix` +11 + `tick-capture-retention-prune` +4；1223 = 1149 + **反事实策略实验室 L1-L4**（5 个 comet change）；1149 由第五次审计 + ccxt keysort 修复 + Agent 故障可见性 + 持仓同步重试 + Agent Health Supervisor + tick-loop 挂死检测 + bot LLM env 隔离构成。
- **2026-06-18 两处风控调参（comet 全流程归档入 main，已重启 live PID 32773 ~10:39 加载）**：① `raise-consecutive-loss-limit`（tweak）连亏熔断 3→5（config.yaml `risk.consecutive_loss_limit` / env `CONSECUTIVE_LOSS_LIMIT`，默认 3）；② **`ev-gate-winrate-decouple`（full，+3 → `test_ev_gate.py` 13，新 capability `open-gate-ev`）——剔除开仓门胜率因子**：config 开关 `ev_winrate_gate_enabled`（默认 True；config.yaml 现设 **false**）关闭后 `_get_p_win` 返回固定 `ev_neutral_p_win`(0.55, p_win_source=`fixed`)、`_check_expected_value` 跳过胜率<40%硬阈值与分桶覆盖，**保留 EV 阈值经济门**（仍按 R:R/成本拦）。三处开关引用用 `getattr(...,True)` 容错 `MultiJudge.__new__` 测试。**live 实测生效**：EV 门不再因实际胜率(25%)拦开仓（同一信号 EV −1.86→+4.13），卡点下移到 `quality_gate`（LLM 观望 → conf<60）与 Short Regime Guard（`daily_bearish_required`）。这两道是正交市场约束，衰减期保留。详见 `openspec/specs/open-gate-ev/spec.md` + `docs/superpowers/specs/2026-06-18-ev-gate-winrate-decouple-design.md`。
- **反事实实验室磁带捕获已修复并生产生效（2026-06-15 `decision-tape-capture-fix`）**：此前 Judge 录制点把 `tech_analysis={}`/`llm_output=None` 写死，致全部磁带不可回放、L2/L4 空转；已改为经 `_symbol_llm_cache` + `_symbol_tech_tape_cache` 两个**专属侧信道**捕获真实 tech+llm（OS 重启后实测新磁带 schema v2、tech 非空、llm 有）。旧磁带永久 `replayable=false`；用 `cf_direction_recommendation.py`（repo 根，可复用驱动）跑 L2 终验 + L4 方向推荐，**需等新磁带累积 ≥数百笔**才有结论。
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

- 跨 Agent symbol 使用内部格式 `BASE-USDT`；交易所 API 调用现场转换。**消费侧落记前必须经 `utils/symbol.py::to_internal` 归一（2026-06-20 `fix-reviewer-symbol-format-and-marginal-settle`）**：ReviewerAgent 写 `trade_record['symbol']` 与 `[复盘] 记录交易` 日志的 symbol 在 3 处入口（`_process_trade_result` reduce/close + `_apply_pnl_resolution`）套 `to_internal`，防上游 leak 的 `-SWAP`/ccxt 格式污染 trade_history 与下游分桶/工具（实证致 `track_marginal60.py` 配对失败 8 单未结算）；归一只统一记录格式，**不碰匹配键**（pnl_resolution upsert 按 `entry_request_id`/`position_id`）。`track_marginal60.py` 结算源读权威 `data/live_position_lifecycle.json` 的 `total_realized_pnl`（仅 `reconcile_status=matched` 计入，pending 标未结算），fill 与 lifecycle 都归一后按 symbol+side+opened_at≈fill_ts(±300s) join。不回填历史 `trade_history.json`。
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
- 仓位同步补录双确认（2026-06-20 `fix-phantom-position-resync`，MODIFIED `position-sync-resilience`）：`executor.sync_positions` 对**本地缺失、交易所新出现**的持仓 MUST 连续 `position_resync_confirm_ticks`（默认 2，HARD_LIMITS 1-10）个 sync tick 确认（`_pending_resync` 计 tick + 扫尾清幽灵）后才补录，防交易所平仓后上报延迟产生幽灵持仓（实证 OKX 滞后 76s 击穿原 60s `_close_cooldown`，近 3 天复发 3 次 UNI/XLM/XRP）。`_close_cooldown` 60s 作第一道防线保留（与双确认互斥：cooldown 只对已补录仓位设、`_pending_resync` 只存未补录 symbol）。protection-unknown(`migrate_missing_sl`) 告警经 `_alert_protection_unknown` 单点收口去重（同 symbol+reason 仅状态变化记 ERROR；halt 幂等 `is_symbol_halted` 守卫；testnet 不 halt 语义保留），protected 恢复 / symbol 移除时清 `_last_protection_alert` 使再失能重新告警。幽灵被 sync 移除时自动清 `migrate_missing_sl` halt（仅此 reason 自愈，其它 fail-closed halt 不动）。**安全不放松**：真·无保护仓位（2 tick 确认补录后 reconcile 仍无 SL）照旧 halt。不改 `_calc_risk_budget`（动态杠杆 = 恒定风险 max_loss/(margin×sl_dist) 上限 20x，max_loss bounded 5%，20x 是 tight-SL 的设计输出非 bug）。
- close/reduce 不应被开仓风控阻断；open/add 必须经过余额、回撤、slot、订单能力预检。
- 修改 Judge / 策略公式必须同步事件回测或补同构测试，不能只看 mock 单测。
- 修改 R:R floor 必须改 `Judge._select_rr_floor` 单一函数，主路径与 `_apply_regime_policy` 共用；不能在调用点重新写 if/else 分支。`probe` / `long_bullish_low_rr` / `long_aligned_low_rr` / `long_aligned_path_evidence` / `short_bullish_strong` / `default` 六种 policy 标签由该函数返回。`long_aligned_path_evidence`（2026-06-17 `trend-entry-rr-fidelity`，**仍默认关** config `path_evidence_aligned_enabled`）：choppy/mixed 下 long 趋势 bias 漏报时用入场前客观路径证据（`entry_context.pre_12h_return_pct`/`position_in_24h_range` + `trend.strength`，**禁前视**）补授 1.30 地板。
- **lever2 阶梯口径与低 R:R 缩仓必须解耦（2026-06-17 `trend-entry-levers-default-on` + `fix-lever2-low-rr-sizing-tp1`）**：lever2（`ladder_rr_enabled`，**默认开**）让 `effective_risk_reward_ratio` 按 executor 真实 50/25/25 阶梯加权（`_compute_ladder_rr`，Option B 无概率折扣），**只用于 R:R 地板 gate**（多开仓）。低 R:R 保护性缩仓/降杠杆/独立 slot 必须经**单一收口 `Judge._apply_low_rr_sizing`**（主路径 + `_apply_regime_policy` 共用，`low_rr_policies` 集合只此一处），且缩仓判定 + `rr_scale` **必须用 TP1 口径 `effective_rr_tp1`**（不得用阶梯值，否则阶梯抬高的 R:R 会把低-R:R 单松绑成全仓满杠杆放大敞口）。**新增任何授 <1.5 地板的 long policy 加入 `_apply_low_rr_sizing` 的 `low_rr_policies` 即可**（单点）。lever2 阶梯口径修改必须改 `Judge._select_rr_floor`/`_compute_ladder_rr` 并同步 event_backtest（结构性失真则用 rejected 流 A/B，见反事实实验室红线）。
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
- Agent 健康聚合必须经 `utils/health_snapshot.py::build_health_snapshot` 单一纯函数派生四维度（loop-alive / queue backlog / LLM degraded / data degraded），2026-06-12。健康快照是 **observability-only write-only**：写入 `agent_health.json` 与驱动 Orchestrator `_maybe_alert_health_transitions` 边沿告警 + `/status`/`/health` 展示，**严禁**任何 gate/rank/veto/halt/daily-stop 读取健康状态做交易决策（与 provenance 同性质）。loop-alive 维度含两路互补检测：`BaseAgent._last_alive_ts`（message loop 0.5s 心跳，抓事件循环级死）+ `_tick_enter_ts`/`_tick_exit_ts`（tick-loop 挂死，`enter>exit AND now-enter>AGENT_TICK_STALL_TIMEOUT_SEC`=120，抓单 agent tick 卡死；扁平阈值锚定最长健康单次 tick=ReviewerAgent 60s，零误报），告警/`/health` detail 区分两者；`_last_work_ts` 仅展示绝不告警。告警边沿触发 + 恢复通知、四维度独立、持续不健康静默；DLQ/`agent_task_failed`/Judge `risk_alert{llm_degraded}`（决策路径）各自独立，不并入此机。详见 `docs/superpowers/specs/2026-06-12-agent-health-supervisor-design.md` + `docs/superpowers/specs/2026-06-12-agent-tick-stall-detection-design.md`。
- 反事实回放产物（决策磁带 `decision_replay_tape.jsonl` / 反事实 PnL `utils/counterfactual_pnl.py` / 1s tick `klines_1s.db`）是 **observability-only write-only**（2026-06-13，change `counterfactual-replay-foundation`，反事实策略实验室路线图 #1，与 `data-source-provenance` / `agent-health-supervisor` 同性质）：**严禁**任何 gate/rank/veto/halt/daily-stop 读取做交易决策；`tests/test_cf_red_line_guard.py` 守卫（Judge 写决策磁带、collector 写 tick store 允许，禁止的是决策/风控路径**读** CF 产物）。决策磁带经 `utils/decision_tape.py::DecisionTape.record_decision` 在 Judge accept/reject 两点写入（`getattr` 防御，部分构造缺 tape 绝不破决策），内联存 parsed LLM 输出（self-contained，抗 llm_audit 7 天过期）；反事实 PnL 复用 executor `CostModel`、同根 K 线 SL/TP 冲突取 SL-first 并量化偏差带、资金费用决策时点 funding_rate 近似标 `funding_approx`；诚实性 gate（Wilson 胜率 + 固定种子 bootstrap 净 PnL + 三档样本，`n<30` 拒答）单点收口于 `utils/cf_honesty_gate.py::summarize_bucket`。**已知 fidelity 限制**：1s tick 实际粒度受 collector 取价周期（~10s）约束，当前为 ~10s bar（仍远好于 1m 解 SL/TP 同根歧义；真 1s 需后续 websocket tick 流）。L2 全带回放+golden master / L3 组合态扰动 / L4 扫描+置信度门为后续 change。详见 `docs/superpowers/specs/2026-06-13-counterfactual-replay-foundation-design.md`。
- 决策磁带捕获契约（2026-06-15，change `decision-tape-capture-fix`）：磁带的 `tech_analysis` 与 `llm_output_inline` 必须反映**决策实际输入**——禁止写空 `{}`/`null` 占位。Judge 经两个**专属侧信道** `self._symbol_llm_cache` + `self._symbol_tech_tape_cache`（镜像 `_symbol_tech_cache` 模式：`_make_decision` 顶部 reset/set、symbol 退出 pop、ranked-flush 从候选 re-prime）捕获，两个录制 chokepoint 防御性 `getattr` 读侧信道。**红线**：tape/flush 代码**绝不写** live `_symbol_tech_cache`（它被 `_regime_manager.update`/`is_probe_short_eligible`/probe 流动性 gate live 读取，写它会破坏 observability-only）；守卫测试 `test_flush_does_not_mutate_live_tech_cache`。`utils/decision_tape.py::build_bundle` 的 `replayable = state_snapshot 非 null AND tech 非空`，`SCHEMA_VERSION=decision_replay_record.v2`；旧 v1 空记录永久 `replayable=false`，回放/扫描端按 `replayable` 过滤。详见 `docs/superpowers/specs/2026-06-15-decision-tape-capture-fix-design.md`。
- 确定性回放 harness（决策状态快照 + `utils/decision_replay.py` + driver `cf_replay_driver.py`）是 **observability-only write-only**（2026-06-13，change `deterministic-replay-golden-master`，路线图 #2 / L2，同 #1 性质）：**严禁**任何 gate/rank/veto/halt/daily-stop 读取（守卫 `tests/test_cf_red_line_guard.py::test_decision_paths_do_not_read_replay_products`；Judge 经 `_capture_state_snapshot` **写**状态快照允许，禁止的是**读**回放产物）。决策磁带扩存 `state_snapshot_before_decision`（~14 个跨决策可变状态白名单，set→list，不 pickle；旧 record 缺快照标 `replayable=false`）使回放可忠实还原 Judge 决策时隐藏输入。harness 用 `MultiJudge.__new__` + `restore_state`（含**还原真实 `RegimeManager` 灌入快照内部状态**，不重写 `is_short_allowed`——L2 核心是复用真实代码零发散）+ mock 决策路径仅有的 3 个外部 await（`_update_balance`/`_ask_llm` 注入内联 LLM/`publish` capture）+ patch `time.time` → 跑真实 `_make_decision` 截获 publish。golden-master `compare_decision` 三层：离散字段（action/confidence/各 gate 标签）字节级、plan 连续字段 <0.5% 容差、`reasoning` 仅信息不判负——**复现钉决策逻辑不钉自由文本**。**真实数据终验**（N≥50 带状态 record 跑 driver 期望 100% 复现）待埋点累积 = follow-up。详见 `docs/superpowers/specs/2026-06-13-deterministic-replay-golden-master-design.md`。
- 逐决策扰动引擎 `utils/perturbation_replay.py`（2026-06-14，change `perturbation-replay-per-decision`，路线图 #3 第一步 / L3a）是 **observability-only write-only**（守卫 `tests/test_cf_red_line_guard.py` 加 `perturbation_replay` 禁读断言）：同一 record 用 baseline vs perturbed 旋钮 config 跑两次 L2 `replay_decision`，量化 gate 翻转（`flip_kind ∈ accept_to_reject/reject_to_accept/gate_label_change/none/baseline_mismatch`）。**baseline 复现自检闸**：baseline replay 的 accept/reject 类须与录下 `decision` 一致，否则标 `baseline_mismatch` 排除出翻转统计（把 L2 golden-master 变成翻转结论的可信前置）。`build_perturbation_report` 按 reject_reason×regime×side 分桶 + Wilson CI + L1 诚实 gate 薄样本拒答 + `fidelity_note`。**保真天花板**：**逐决策独立、不含级联**（早期翻转改变后续状态留 L3b）；只对**非 LLM 旋钮**（R:R floor/EV/gate 阈值/slot）确定（LLM 取录制内联）。能回答"放宽 choppy R:R 地板"类在录下决策点的翻转率，但非整策略 PnL（待 L3b）。详见 `docs/superpowers/specs/2026-06-14-perturbation-replay-per-decision-design.md`。
- 序列组合态扰动重演 `utils/cf_portfolio.py` + `utils/sequential_perturbation.py`（2026-06-14，change `sequential-portfolio-perturbation`，路线图 #3 第二步 / L3b，反事实实验室收官）是 **observability-only write-only**（守卫 `tests/test_cf_red_line_guard.py` 加 `cf_portfolio`/`sequential_perturbation` 禁读断言）：按时间序重放整条磁带 + 维护扰动后 CF 组合状态（`CounterfactualPortfolio`：slot/equity/EV 计数/**独立 CF `ArchetypeCooldown`**/daily-stop 累加器），CF 开仓用 L1 `resolve_counterfactual` 估算退出 → 喂回 CF 状态，给整策略 PnL/胜率/回撤 **delta**。**完全隔离**：CF 决策绝不 publish 真实 bus、绝不读真实 cooldown `is_cooled()`/daily-stop 状态（独立 CF 实例）。**两臂同估算 → 系统性偏差在 delta 抵消**（结论以 delta 为主非绝对值）。**baseline 序列保真自检（信任锚）**：每步 CF EV 状态 = 序列起点 `_seed_cf_prior`（recs[0] 录制先验，磁带窗口前真实战绩）+ **各臂自累计**的 CF 结果（**绝不 per-record 注入 reality 演化计数**——否则人为抬高 fidelity 并掩盖级联）；baseline-sim 决策 vs 录下决策一致率 < 阈值（默认 0.8）标 `untrustworthy` 拒给 delta。**保真天花板**：退出仅 SL/TP/24h（漏 trailing/partial/risk-close ~10-20%），误差沿序列累积，报 `divergence_ratio`/CF 开仓数/估算 PnL 占比 + L1 诚实 gate。详见 `docs/superpowers/specs/2026-06-14-sequential-portfolio-perturbation-design.md`。
- 旋钮扫描 + 方向推荐 `utils/knob_sweep.py`（2026-06-14，change `perturbation-knob-sweep`，路线图 #4，**反事实策略实验室 L1-L4 收官**）是 **observability-only write-only**（守卫 `tests/test_cf_red_line_guard.py` 加 `knob_sweep` 禁读断言）：`sweep_knob` 对单旋钮显式值列表逐值跑 L3b `build_delta_report`；`recommend_direction` 门控（剔 L3b untrustworthy + 薄样本）→ 按 delta 净 PnL 排名 → **多重比较守卫**（连贯趋势才推荐，孤立尖刺标 `isolated_spike` 拒答，actionable 门槛随扫描值数收紧抵消选择性偏差）→ confidence 三因子透明（baseline_fidelity × divergence 惩罚 × 样本档，**报出三原始因子不藏单一数字**）。证据不足输出 `no_actionable_direction` **绝不杜撰方向**；报出 `all_values` 全貌 + 继承 L3b `fidelity_note`。**绝不自动改线上 config（只出建议，人审）**。详见 `docs/superpowers/specs/2026-06-14-perturbation-knob-sweep-design.md`。
- 前向影子决策记录器 `utils/shadow_decision_logger.py`（2026-06-17，change `trend-entry-shadow-decision-logger`）是 **observability-only write-only**（守卫 `tests/test_cf_red_line_guard.py::test_decision_paths_do_not_read_shadow_products`，executor/halt/riskguard/reviewer/position_analyst 禁读影子产物，**Judge 写路径豁免**）：交易层每个信号在决策磁带 chokepoint（`judge.py` accept/reject 两点）经 `Judge._schedule_shadow`（sync fire-and-forget `create_task`，无 loop/异常皆 fail-safe **绝不破 live 决策**）+ `_maybe_log_shadow`（async）复用 `replay_decision` 隔离机器（mock 外部 await/缓存 llm/捕获 publish **绝不进真实 bus**/`MultiJudge.__new__` 不碰 live 实例）对**同一 bundle** 跑 both-levers（`path_evidence_aligned_enabled=True` AND `ladder_rr_enabled=True`）影子决策，write-only 记 `{real_action+gate, shadow_action+gate, flip_kind, tech_context, plan}` 到 `data/shadow_decision_log.jsonl`。live 现 lever2-only → **影子 − 实盘 = lever1 纯增量**。影子决策**绝不** publish 真实 bus/下单/mutate live Judge·portfolio·cooldown·daily-stop。config `shadow_decision_logger_enabled` 默认开 / env `SHADOW_DECISION_LOGGER_ENABLED=false` 可关。离线对比驱动 `cf_shadow_lever1_compare.py`（筛 `flip_kind=shadow_opens` → `resolve_counterfactual`+klines 结算 lever1 增量 + 诚实门）。**已知 follow-up**：影子日志无 prune（无界 append，待补 retention）；路径未走 state_paths 命名空间。详见 `docs/superpowers/specs/2026-06-17-trend-entry-shadow-decision-logger-design.md`。**（2026-06-20 `fix-shadow-logger-replay-baseline-parity`）lever1 增量口径修正**：原 `live(real) vs replay(both-levers)` 混入复盘保真偏差（实证 37 条 shadow_holds 全是复盘失真、lever1 真实 delta=0），改为 **`replay(lever2-only baseline) vs replay(both-levers shadow)` 两臂同复盘（偏差抵消）+ baseline 复现自检闸**（`replay(lever2-only)` 的 accept/reject 不复现 live record → 标 `baseline_mismatch=True` 排除）。新增 jsonl 字段 `baseline_action`/`baseline_gate`/`baseline_mismatch`，`flip_kind` 改基于 baseline vs shadow。不动 ev-gate config（config-parity 假设已证伪）。
- 胜率解耦复核驱动 `cf_ev_decouple_ab.py`（2026-06-20，change `ev-decouple-forward-ab`，新 capability，镜像 `cf_lever2_rejected_ab.py`）是 **observability-only write-only**（守卫 `tests/test_cf_red_line_guard.py::test_decision_paths_do_not_read_ev_decouple_ab`，决策/风控路径禁 import/读）：复核 `ev-gate-winrate-decouple`（06-18）放行的边缘单前向期望。对决策磁带 `decision=accept` 流 **gate-toggle 两臂复盘**——baseline `replay(ev_winrate_gate_enabled=False)`(=live 现配置，复现 live accept 自检失真排除) vs 反事实 `replay(ev_winrate_gate_enabled=True)`(=06-18 前旧胜率门)，旧门翻 reject = **"解耦放行"**。两桶(解耦放行 vs 双门皆过)簇去重(symbol,side,>1h)+`resolve_counterfactual`+klines **统一 CF 结算**(TP1 保守含亏单)比净 R，`cf_honesty_gate.summarize_bucket`(**min_sample=30 不下调**)领先裁定薄样本拒答；real PnL(symbol+ts 模糊 join lifecycle，无 request_id)作次要 sanity。**真跑：69 accept→54 忠实/38 解耦放行(全 ev_gate)；两桶均 INSUFFICIENT_SAMPLE 拒答，suggestive 解耦放行 −0.35R/簇 反优于双门皆过 −0.80R/簇 → 证伪"解耦放行更差"假设。** 绝不下单/改 config/mutate live。**回滚/约束胜率解耦须另起 change（本驱动只量化不改 live）。** **教训：CF 结算契约须传 `resolve_counterfactual` 所需字段(`entry_price`/`created_at`/`side`/`stop_loss`/`take_profit`)而非 live plan 原始 dict(字段是 `entry_ref`、无 `created_at`)，集成测试勿全 mock resolve。** 详见 `docs/superpowers/specs/2026-06-20-ev-decouple-forward-ab-design.md`。

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
