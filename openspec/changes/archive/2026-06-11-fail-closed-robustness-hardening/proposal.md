## Why

第五次系统性审计（`docs/generated_reports/系统性审计报告_20260610_第五次.md`）第二梯队列出一批"低成本高价值"的 fail-closed / robustness 缺口——单个都不致命，但都削弱实盘系统在故障/边界下的安全姿态。本 change 一次性收口 6 项（全部非 `judge.py`，与并行处理 P1-02/P1-03 的另一窗口零重叠）：

- **P2-03**（latent）：`agents/trading/executor.py:_handle_resume` 在非 matched 对账结果时进入 `if self._reconciler:` 分支调 `self._reconciler.reconcile(executor_positions=...)`，但 `self._reconciler` 是 `utils/reconciliation.py:Reconciler`（PnL 账本对账器，**无 `reconcile` 方法**）→ AttributeError 被 except 吞 → 维持熔断。当前不可达仅因 TG resume 总带 matched 结果早返回。且无 reconciler 的 `else` 分支**无条件恢复**（fail-open 隐患）。
- **P2-06**（latent 安全）：`_handle_risk_alert` 无 `source` 守卫，paper 的 `risk_alert`（`type=paper_unfilled, source=paper_executor`）经共享 `risk_alert` topic 进入 live executor，目前仅靠"type 白名单未命中"而安全——隔离是"恰好不撞名"而非结构性。
- **P2-16**：`agents/message_bus.py` 的 DLQ（deque maxlen 200）+ 重要 topic 无订阅者只静默计数，无主动告警；关键 wiring 断裂不惊动运维。
- **P2-17**：根 `executor.py` config_loader 初始化失败的兜底分支直读 env 风险限额，**跳过 HARD_LIMITS clamp** → 风险限额可 fail-open 到未约束值。
- **P2-20**：`utils/event_journal.py` 只 `flush()` 不 `fsync()`，断电丢最近关键事件（与 `atomic_io` 已 fsync 标准不一致）。
- **P2-21**：`utils/halt_state.py:_save` 异常兜底分支用非原子裸写最关键的 halt 文件，可能写半截。

## What Changes

- **P2-03**：`_handle_resume` 非 matched 路径改为**显式 fail-closed**——不调用任何不存在的 reconcile，统一"非 matched 对账结果 → 维持熔断 + 结构化告警"；评估并收紧无 reconciler 的 `else` 无条件恢复路径，使其不 fail-open。
- **P2-06**：`_handle_risk_alert` 顶部加 `if alert.get('source') == 'paper_executor': return`，把 paper/live 隔离从"白名单未命中"升级为**结构性 source 守卫**。
- **P2-16**：`orchestrator._write_agent_health` / `_health_loop`（已计算 `dlq_size`、已有 `telegram_alert` 通道）增加 DLQ 增长/重要 topic 死信的阈值告警。
- **P2-17**：config_loader 兜底分支也套 `HARD_LIMITS` clamp（复用 config_loader 的 clamp），杜绝风险限额 fail-open。
- **P2-20**：`event_journal` 每条关键事件 `write` 后加 `os.fsync(fileno())`（只 5 类低频 critical topic，成本可接受）。
- **P2-21**：`halt_state._save` 删除非原子裸写兜底，`atomic_write_json` 失败就 log error，不退化为半截写。
- **测试**：每项补/扩对应单测；改执行/风控保持 fail-closed 不回归。

## Capabilities

### New Capabilities
（无）

### Modified Capabilities
- `risk-alert-routing`：新增需求——live executor 的 risk_alert 处理 MUST 以 `source` 结构性守卫拒绝 paper 来源事件，不得依赖"type 白名单恰好未命中"。
- `tg-symbol-halt-control`：新增需求——全局 resume 在非 matched 对账结果时 MUST fail-closed 维持熔断，MUST NOT 调用 PnL `Reconciler` 上不存在的 `reconcile`；无 reconciler 时 MUST NOT 无条件恢复。
- `tg-status-enhancement`：新增需求——DLQ 增长 / 重要 topic 死信 MUST 经 `telegram_alert` 主动告警，不得仅静默计数。

> P2-17 / P2-20 / P2-21 为纯实现健壮性收口（HARD_LIMITS clamp 兜底、journal fsync、halt 原子写），**不改变任何 requirement 级行为契约**，无 delta spec；以 design.md + 测试覆盖。

## Impact

- **代码**：`agents/trading/executor.py`（P2-03 / P2-06）、`agents/orchestrator.py`（P2-16）、根 `executor.py`（P2-17）、`utils/event_journal.py`（P2-20）、`utils/halt_state.py`（P2-21）。
- **测试**：`test_halt_resume_ownership.py` / `test_reconciliation.py`（P2-03）、`test_riskguard_upgrade.py` 或新建（P2-06）、`test_tg_status_enhancement.py`（P2-16）、`test_drawdown_baseline.py` 或新建（P2-17）、新建 journal/halt 原子性用例（P2-20/21）。
- **不影响**：`judge.py`（P1-02/P1-03 另一窗口）；深度 halt 语义重构（per-symbol halt 持久化）；保护单交易所同步与并发锁（P2-08/09，需 testnet 复现，另立 change）。
- **红线**：改执行/风控保持 fail-closed 不回归；基线当前 `1071 passed`，变更后须全绿。本 change 叠在 `add-position-tp-sink-halt-recovery` 分支之上（P1-01 未并入 main）。
