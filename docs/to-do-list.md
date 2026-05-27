# To-Do List

更新日期：2026-05-27  
来源：2026-05-24 系统性审计、全量测试、OKX mock 验收、docs 清理；2026-05-25 OKX posMode 执行故障复核与代码落地；2026-05-26 R:R Floor Policy 修复 + Long Entry Position Guard 上线；2026-05-27 OKX 真实 testnet T0-T9 语义验收 PASS  
当前基线：`618 passed / 4 deselected / 1 warning`，`verify_okx_testnet_semantics.py` mock 10 case PASS（含 posMode close 矩阵 + 拒单状态复核），`test_rr_floor_policy.py` 20 case PASS（覆盖 AC-RR-01..09），`test_long_entry_position_guard.py` 23 case PASS（覆盖 AC-LONGPOS-01..17），`test_partial_tp_lifecycle.py` 32 case PASS（含 FR-07 algo 迁移），OKX 真实 testnet 验收 7 PASS / 3 SKIP（T2/T3 net_mode 账户切换待人工执行，T7 mock_only 已在 mock 矩阵 PASS）。

## 当前 Go/No-Go

- 本地开发：GO。
- Paper/mock：GO。
- 小额 live 灰度：GO（OKX 真实 testnet T0/T1/T4/T5/T6/T8/T9 已 PASS，关键路径全覆盖）。
- live 扩容：GO，前置阻断已解除（仍建议先小额 24h 灰度观察 segmented metrics）。

## P1 阻断 live 扩容

| 状态 | 事项 | 下一步 | 验收标准 |
|---|---|---|---|
| OPEN | OKX net_mode 切换二次验收（可选） | 把 testnet 账户 posMode 切到 net_mode 后跑 T2/T3 | T2 reduce ratio in [0.4, 0.6]、T3 close 后无残余 algo；当前账户为 long_short_mode，已通过 mock 矩阵覆盖 net_mode 闭环 |

## P2 后续优化

| 状态 | 事项 | 下一步 | 验收标准 |
|---|---|---|---|
| OPEN | BehavioralCritic 字段契约统一 | 统一 `BEHAVIORAL_CRITIC_SCHEMA`、prompt、fallback 和 `PositionAnalyst` 消费字段 | LLM 按 schema 或 prompt 输出时，PA 都能读取 counter 建议；新增坏 JSON/缺字段测试 |
| OPEN | Paper 结果独立复盘 | 为 `paper_execution_result` 增加 version 或单独 paper reviewer/dashboard | 可查看 paper vs live 胜率、EV、回撤，不污染 live Reviewer |
| OPEN | LLM audit 脱敏和保留策略 | 增加 `LLM_AUDIT_RETENTION_DAYS`、原始 prompt 记录开关、敏感字段脱敏 | 日志保留可配置，默认不长期保留敏感输入/响应 |
| OPEN | `ContractExecutor` exchange 创建统一 | 将根 `executor.py` 的 ccxt 创建收敛到 `utils/exchange_factory.py` 或共享 helper | 所有 exchange client 的 sandbox/live 语义由单一入口控制 |
| OPEN | Binance legacy path 标识 | 明确当前 live/testnet 只验收 OKX；Binance 分支标为 legacy 或补交易所能力适配 | 文档和代码注释不再暗示 Binance 已具备同等 TP/SL 语义 |
| OPEN | 数据源 provenance | 给跨源 OI/taker/crowd/news 字段补 `source`、`freshness_sec`、`confidence` | Reviewer 可按数据源质量分桶，Judge 不把弱外部信号当强事实 |
| OPEN | Agent health supervisor | Orchestrator 增加 setup failure、loop alive、queue backlog、DLQ、LLM degraded、data degraded 状态 | Telegram `/status` 或 health 输出能看见关键 agent 健康状态 |
| OPEN | 文档瘦身 | `CLAUDE.md`、`docs/architecture.md`、`docs/handoff.md` 历史流水迁出或压缩 | 规则文档只保留当前事实和硬约束，旧测试数仅在历史上下文出现 |

## 已关闭

| 事项 | 验收证据 |
|---|---|
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

## 常用验证命令

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .
python3 -m pytest -q
python3 verify_okx_testnet_semantics.py
python3 verify_okx_testnet_real.py
```
