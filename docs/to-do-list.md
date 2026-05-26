# To-Do List

更新日期：2026-05-26  
来源：2026-05-24 系统性审计、全量测试、OKX mock 验收、docs 清理；2026-05-25 OKX posMode 执行故障复核与代码落地；2026-05-26 R:R Floor Policy 修复  
当前基线：`551 passed / 4 deselected / 1 warning`，`verify_okx_testnet_semantics.py` mock 10 case PASS（含 posMode close 矩阵 + 拒单状态复核），`test_rr_floor_policy.py` 20 case PASS（覆盖 AC-RR-01..09）。

## 当前 Go/No-Go

- 本地开发：GO。
- Paper/mock：GO。
- 小额 live 灰度：NO-GO，直到 OKX 真实 testnet/sandbox 上完成 posMode 矩阵 smoke test 并签字（代码已落地，仅缺真实环境证据）。
- live 扩容：NO-GO，直到 OKX 真实 testnet 语义验收完成。

## P1 阻断 live 扩容

| 状态 | 事项 | 下一步 | 验收标准 |
|---|---|---|---|
| CODE-COMPLETE / WAITING-TESTNET | OKX posMode 执行兼容 | 用 OKX demo/testnet key 跑 `docs/okx_posmode_execution_acceptance.md` 的 T0-T9 案例，记录 raw response、final position、algo orders | 实现侧：`executor.py` 已收敛 `_build_okx_open_params` / `_build_okx_close_params` / `_build_okx_algo_params`，业务路径全部接入；拒单状态复核 (`_handle_okx_close_reject`)、symbol halt、`availPos` 钳制就位；`test_okx_posmode_executor.py` 38 PASS，`verify_okx_testnet_semantics.py` 10 case PASS。剩余：testnet 真实账户证据未采集，live 扩容仍 NO-GO |
| BLOCKED | OKX 真实 testnet 语义验收 | 使用 OKX sandbox/testnet key 执行 market open + attached TP/SL、limit timeout、insufficient balance、min amount、posMode-aware close/reduce、move SL、close 后条件单状态、duplicate clOrdId 等 case | `docs/generated_reports/OKX执行语义testnet验收报告_20260522.md` 更新为真实 raw response、normalized result、final position/order state；全部 PASS 后才允许扩容评审 |

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

## 常用验证命令

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .
python3 -m pytest -q
python3 verify_okx_testnet_semantics.py
```
