# 项目阶段总览

更新日期：2026-08-06
Tactical V2 代码基线：`884ba60`

本文回答三个问题：

1. 现在系统到底有多少功能模块。
2. 每个模块负责什么、怎么用、在什么场景下用。
3. 当前项目处于什么阶段，后续重点是什么。

## 一句话结论

项目已经从最初的套利验证，演进成一套 OKX USDT 永续趋势交易系统：主链路是 `run_agents.py` 多 Agent 自动交易，辅链路包括 Tactical Exit Track、Shadow Tactical live sidecar、Paper 双轨、反事实实验室和一组运维/风控/账本工具。

当前工程能力已经比较完整，关键风险不在“能不能跑”，而在“自主策略 edge 是否足够稳定”。Tactical V2 已完成 shadow gate、sidecar drain 和首轮 live cohort，当前阶段是固定 `100U x 3` 的受控 live 观察，不扩大容量。

## 规模口径

这里按功能域统计，不按单个 `.py` 文件统计。

| 口径 | 数量 | 说明 |
|---|---:|---|
| 一级功能域 | 15 | 下文逐项梳理，适合新人和运维理解系统 |
| 非测试 Python 文件 | 128 | 包含主链路、工具、脚本、旧实验/归档代码 |
| 测试文件 | 172 | 根目录 `test_*.py` 111 个，`tests/` 下 61 个 |
| 主生产入口 | 1 | `python3 run_agents.py` |
| Sidecar 入口 | 1 | `python3 scripts/shadow_tactical_live_sidecar.py ...` |
| 旧归档域 | 1 | CEX 套利相关代码，仅作历史参考 |

最新本地全量回归为 `1878 passed, 4 deselected`。Tactical V2 live promotion、入口精确回查/自愈和重启后 PnL replay 的专项与云服验收记录见 `docs/superpowers/reports/2026-07-28-promote-shadow-tactical-v2-live-verify.md` 与 `docs/superpowers/reports/2026-08-06-fix-tactical-canceled-entry-self-heal-verify.md`。

## 当前阶段

| 维度 | 状态 |
|---|---|
| 工程链路 | 主链路、风控、执行、账本、Paper、TG 运维、健康观测都已可运行 |
| 实盘状态 | Tactical V2 首轮 `LIVE 100U x 3`；不扩大容量 |
| 策略状态 | 趋势单是主要 edge 假设；choppy/mixed/neutral 无方向单已被多轮诊断证明质量差 |
| Tactical | V2 已接管合格 Tactical 执行；旧 V1 live 分支不作为当前开仓路径 |
| Sidecar | resident monitor 保留历史 owner 与审计能力，当前 `admission_enabled=false` |
| 最大红线 | 观测/反事实产物 write-only，不允许进入 live gate/rank/veto/halt/daily-stop |

2026-08-06 云服快照：Main PID `2663623`，Sidecar PID `1773370`；V2 `LIVE 100U x 3`，`0 active / 0 pending / 3 free`，`integrity_halt=null`，protection/reconciliation 均 `verified`，rolling PnL `-0.9593U`、loss streak `1`；`data/positions.json` 为空。Sidecar `admission_enabled=false`，当前 active=0，历史累计 `opened=69/rejected=1505`。两者均为常驻进程，不是 systemd/pm2；没有部署应用级 supervisor。

## 15 个一级功能域

| # | 功能域 | 主要文件 | 作用 | 怎么用 / 适用场景 |
|---:|---|---|---|---|
| 1 | 启动与编排 | `run_agents.py`, `agents/orchestrator.py` | 启动所有 Agent、触发研判周期、健康快照、优雅停机和 `/restart` execv 重启 | 生产、paper、testnet、实盘验收统一跑 `python3 run_agents.py`；不要用 `main.py`/`live_trading.py` 做生产入口 |
| 2 | Agent 基础设施 | `agents/base.py`, `agents/message_bus.py`, `agents/llm_client.py` | Agent 生命周期、进程内 topic 总线、优先级、DLQ、LLM JSON 调用和规则降级 | 新增 Agent 或消息类型时先看这里；新增关键 topic 要补优先级、journal 和测试 |
| 3 | 研判层选币 | `agents/research/*` | MarketScanner 扫 OKX 合约，Sentiment/News 收集外部信号，Synthesizer + Censor 两阶段筛选，SymbolRouter 发布 active symbols | 用于每 4h 更新交易标的；低流动性、低 OI、上线时间不足等会 fail-closed 过滤 |
| 4 | 多维行情采集 | `agents/trading/multi_data_collector.py` | 采集 K 线、盘口、OI、爆仓、资金费率、Taker、大单、多空比、新闻快照和价格 tick | 交易层实时输入；数据 degraded 只能降低置信或阻止高置信决策，不能伪装成完整数据 |
| 5 | 技术分析 | `agents/trading/tech_analyst.py`, `indicators.py` | 把多维数据转成 trend、levels、momentum、money_flow、microstructure、crowd、risk、rule_signal | Judge 的上游信号；改指标或信号字段必须同步 Judge attribution 和事件回测 |
| 6 | Judge 决策引擎 | `agents/trading/judge.py`, `utils/candidate_ranker.py` | 生成 `trade_decision.v2`，负责 score、R:R floor、EV、ranking、slot、deferred entry、Main/Tactical 分类 | 改开仓策略主要改这里；核心入口有 `_select_rr_floor`、`_check_entry_position_policy`、`_classify_short_entry_risk`、`_classify_regime_flat_gate`、`_classify_track` |
| 7 | Main/Tactical 出口轨道 | `agents/trading/judge.py`, `executor.py` | Main Trend Runner 走趋势持有和分批止盈；Tactical 用独立 TP1、max hold、thesis health、weakened/no-progress 退出 | Tactical 适合弱/混合环境的短线落袋实验；Main 适合强趋势右尾，不要用 regime+side 反推出口语义 |
| 8 | Agent 执行层 | `agents/trading/executor.py` | 消费 `trade_decision`，统一 open/close/reduce/add，发布 `execution_result.v2` | 所有执行结果必须保留 request_id、track、exit_profile、close cause、protective cleanup 等字段 |
| 9 | 底层合约执行器 | `executor.py` | CCXT/OKX 下单、posMode 参数、attached SL、保护单迁移、entry drift、TP/SL 生命周期、仓位同步 | 所有真实交易语义集中在这里；改 OKX 参数、保护单、平仓/减仓必须跑相关单测和 testnet 验收 |
| 10 | 风控与熔断 | `risk_manager.py`, `agents/trading/portfolio_risk_guard.py`, `utils/halt_state.py`, `utils/position_reconciler.py` | 余额/回撤/日亏、组合级风险、per-symbol halt、全局 halt、对账恢复 | live 扩容前必须看这里；manual/daily/reconcile halt 保持 sticky，保护单自愈只覆盖 allowlist 原因 |
| 11 | 持仓管理 | `agents/trading/position_analyst.py`, `agents/trading/behavioral_critic.py` | 持仓 7 因子复评、行为偏差检测、close/reduce/add/hold 仲裁 | 用于持仓后的出场和加减仓；不负责开仓筛选，硬性覆盖优先于 LLM 建议 |
| 12 | 复盘、账本与 PnL | `agents/trading/reviewer.py`, `utils/live_ledger.py`, `utils/realized_pnl_resolver.py`, `scripts/backfill_realized_pnl.py` | 交易历史、Daily Hard Stop、segmented metrics、OKX fills/bills 已实现 PnL 解析、pending->final correction | 任何收益统计必须优先使用 final PnL；close 类 payload 必须用 `pnl_is_final=True` 守门 |
| 13 | Paper 与双轨模拟 | `agents/trading/paper_executor.py`, `agents/trading/paper_dual_track_report.py` | 不下真单的影子账户，realistic/idealized 双账本，对比限价漏单成本 | 策略观察和 paper/live gap 诊断用；`paper_execution_result` 不得污染 live Reviewer/风控 |
| 14 | 运维与健康观测 | `agents/trading/telegram_notifier.py`, `utils/health_snapshot.py`, `utils/event_journal.py`, `utils/state_paths.py`, `utils/config_loader.py` | TG 命令、agent loop/queue/LLM/data 健康、关键事件 journal、状态命名空间、配置硬限 | 日常运维用 `/status`、`/health`、`/halts`、`/resume`、`/paper_gap`；状态路径以启动 banner 为准 |
| 15 | 实验室与影子系统 | `utils/counterfactual_ledger.py`, `utils/decision_tape.py`, `utils/decision_replay.py`, `utils/perturbation_replay.py`, `utils/sequential_perturbation.py`, `utils/knob_sweep.py`, `utils/joint_knob_sweep.py`, `utils/shadow_tactical_live.py`, `scripts/shadow_tactical_live_sidecar.py`, `cf_*.py`, `pattern_forward_shadow.py` | 被拒信号追踪、确定性回放、扰动扫描、旋钮推荐、形态研究、Tactical shadow、Shadow Tactical live sidecar | 用来找策略方向及观察 Sidecar owner/drain；当前 `admission_enabled=false`，默认 observability-only，不允许恢复 Sidecar admission、自动改线上配置或参与 live gate |

## 关键运行链路

主链路：

```text
research_trigger
  -> MarketScanner / SentimentResearcher / NewsResearcher
  -> ResearchSynthesizer preliminary
  -> Censor
  -> ResearchSynthesizer final
  -> SymbolRouter
  -> symbol_update
  -> MultiDataCollector
  -> TechAnalyst
  -> Judge
  -> MultiExecutor + PaperExecutor
  -> ContractExecutor / Reviewer / RiskGuard / PositionAnalyst / Telegram
```

Tactical shadow-only：

```text
Judge._classify_track
  -> _apply_tactical_shadow_profile
  -> data/rejected_signal_events.jsonl
  -> data/rejected_signal_lifecycle.json
  -> shadow_tp / shadow_sl / shadow_tactical_max_hold / shadow_expired
```

历史 Sidecar owner/drain 路径（当前 admission 关闭）：

```text
data/rejected_signal_events.jsonl
  -> scripts/shadow_tactical_live_sidecar.py
  -> strict eligible filter（仅 legacy owner/drain）
  -> sidecar-owned state/ledger/owners files
  -> monitor_sidecar_owned_exposure()
  -> reduce_position / close_position when ownership is proven

Tactical V2 live：

```text
eligible Tactical candidate
  -> tactical_intent.v2 / episode dedup
  -> executable ask/bid + frozen-entry policy
  -> V2-owned protection / exit / final-PnL outbox
  -> governor / Reviewer / Judge
```
```

Sidecar 当前严格 eligible filter 是：

```text
event_type=rejected_plan_created
record.track=tactical
record.exit_profile=tactical_v1
record.tactical_track_gate=pass
```

## 常用入口

启动主系统：

```bash
python3 run_agents.py
```

后台启动主系统：

```bash
nohup python3 run_agents.py > logs/launcher_$(date +%Y%m%d_%H%M%S).log 2>&1 &
```

Sidecar 状态：

```bash
python3 scripts/shadow_tactical_live_sidecar.py status
```

Sidecar admission 当前为 `admission_enabled=false`，受 2026-08-12 NO-GO gate 约束。状态查询可执行；历史 24h live run 命令仅保留为禁止项，不得执行。本 replay 的 `live_rollout_ready=false`；只有真实 quote-level executable evidence 与 fill-bound protection evidence 分别通过后，才可重新评审 Sidecar restoration。

```bash
# PROHIBITED while the 2026-08-12 NO-GO gate is active; do not execute:
# python3 scripts/shadow_tactical_live_sidecar.py run --duration-hours 24 --size-usdt 100 --max-active 3
```

Sidecar 停止并尝试处理可证明归属的敞口：

```bash
python3 scripts/shadow_tactical_live_sidecar.py stop
```

Tactical/sidecar 聚焦验证：

```bash
pytest tests/test_shadow_tactical_live_core.py tests/test_shadow_tactical_live_cli.py tests/test_shadow_tactical_live_executor.py tests/test_shadow_tactical_owner_isolation.py tests/test_shadow_tactical_exit_monitoring.py tests/test_entry_drift_hybrid_policy.py test_partial_tp_lifecycle.py -q
```

默认回归：

```bash
python3 -m pytest -q
```

策略诊断/反事实方向：

```bash
python3 cf_direction_recommendation.py
python3 cf_ev_decouple_ab.py
python3 cf_choppy_neutral_tp1_floor_ab.py
python3 cf_neutral_momentum_rescue_ab.py
```

## 使用场景速查

| 场景 | 先看/先用 | 不要做 |
|---|---|---|
| 新人接手 | 本文档 -> `README.md` -> `CLAUDE.md` -> `docs/runbook.md` | 不要先翻 `docs/handoff.md` 的长历史线 |
| 日常运维 | `docs/runbook.md`、TG `/status`、`/health`、`/halts`、日志启动 banner | 不要只看 stale `halt_state.reason`，必须看 `halted/can_open_new` |
| 改开仓策略 | `agents/trading/judge.py` + `event_backtest.py` + `docs/development.md` | 不要只改 Judge mock 单测，不同步回测或 attribution |
| 改 OKX 执行 | `executor.py` + OKX testnet 验收脚本 | 不要手写 posSide/reduceOnly 绕过构造器 |
| 看 Tactical shadow 是否赚钱 | `data/rejected_signal_events.jsonl` + `data/rejected_signal_lifecycle.json` | 不要用 PaperExecutor 当 Tactical shadow 证据 |
| 看 live Tactical 是否赚钱 | LiveLedger / Reviewer final PnL，按 `track/exit_profile/tactical_close_reason` 分桶 | 不要把 pending/estimated PnL 算进最终收益 |
| 观察 Sidecar owner/drain | Sidecar `status` + state/owners/audit 文件 | 当前禁止 live run；不要改 Main `.env`、恢复 admission 或让 Main 领养 sidecar 仓位 |
| 策略研究 | 反事实实验室、pattern runner、`cf_*.py` | 不要把 observability-only 产物接回 live 决策 |

## 关键状态文件

主系统：

| 文件 | 用途 |
|---|---|
| `data/positions.json` | Main 本地持仓 |
| `data/risk_state.json` | RiskManager 状态 |
| `data/riskguard_state.json` | RiskGuard、Tactical circuit |
| `data/halt_state.json` | 全局 halt |
| `data/agent_health.json` | `/status` 和 `/health` 输入 |
| `data/live_order_events.jsonl` | live 订单事件 |
| `data/live_position_lifecycle.json` | live 持仓生命周期 |
| `data/rejected_signal_events.jsonl` | 被拒/影子决策事件，Tactical shadow/sidecar 输入 |
| `data/rejected_signal_lifecycle.json` | 被拒/影子决策生命周期 |

Sidecar：

| 文件 | 用途 |
|---|---|
| `data/shadow_tactical_live_state.json` | sidecar watermark、seen shadow ids、stop_at |
| `data/shadow_tactical_live_events.jsonl` | sidecar 审计事件 |
| `data/shadow_tactical_live_owners.json` | shadow_id -> sidecar order/protection 归属 |
| `data/shadow_tactical_live_positions.json` | sidecar 本地持仓 |
| `data/shadow_tactical_live_order_events.jsonl` | sidecar 订单事件 |
| `data/shadow_tactical_live_position_lifecycle.json` | sidecar 持仓生命周期 |

## 当前红线

- 不删除或覆盖 `.env`、`data/`、`logs/`。
- `main.py` / `live_trading.py` 是归档/调试路径，不是生产入口。
- 观测产物只能 write-only；不能让 `decision_replay`、pattern、CF、health snapshot、provenance 反向影响 live gate/rank/veto/halt/daily-stop。
- LLM 只能辅助，不能绕过 R:R、EV、余额、熔断、保护单和订单预检。
- Sidecar 与 Main 同 OKX 账户时必须保持 owner isolation；Main 不能 backfill sidecar-owned 仓位，algo migration 不能取消 sidecar/manual protection。
- OKX `net_mode` 下禁止 sidecar 同标的堆叠，除非未来专门实现 aggregate/per-lot position model。
- 2026-08-12 Sidecar admission NO-GO：必须保持 `admission_enabled=false`。本 replay 的 `live_rollout_ready=false`，不授权恢复 Sidecar admission、扩大 V2 保证金/槽位或修改生产配置。恢复评审必须等待真实 quote-level executable evidence 与 fill-bound protection evidence 分别通过。

## 后续重点

1. 继续观察 Main 的 `tech_analyst` backlog，避免队列长期高位拖慢决策。
2. 继续累计 Tactical/sidecar 样本，按 final PnL 和 `track/exit_profile/tactical_close_reason` 分桶判断，而不是按单次 shadow 样本下结论。
3. Sidecar 不得恢复 admission。无 ghost exposure、ambiguous net-mode stack 和未解决保护单等 owner/exposure 检查仍是必要条件，但不足以授权续跑；真实 quote-level executable evidence 与 fill-bound protection evidence 还必须分别通过。本 replay 的 `live_rollout_ready=false`，也不授权容量扩张或生产配置变更。
4. 如需长期无人值守，再单独设计并验收 supervisor；当前云服没有应用级 cron/systemd/pm2 守护，不要把 `nohup` 误记为自动拉起。
5. 策略改善重点应回到上游方向质量、体制识别和趋势行情筛选；单纯放宽 R:R、confidence 或入场门已经多次被反事实实验室证明不是高价值杠杆。
