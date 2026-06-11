# Comet Design Handoff

- Change: fail-closed-robustness-hardening
- Phase: design
- Mode: compact
- Context hash: 9b802dccc0b79189502d18c794cf1d00c8e1eec9fdd990d68f81fe6bf3646a75

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/fail-closed-robustness-hardening/proposal.md

- Source: openspec/changes/fail-closed-robustness-hardening/proposal.md
- Lines: 1-39
- SHA256: ffb904fefcf6d0531f075d34621b0603882c80f9e00ade52324b786b51ae6fdd

```md
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
```

## openspec/changes/fail-closed-robustness-hardening/design.md

- Source: openspec/changes/fail-closed-robustness-hardening/design.md
- Lines: 1-50
- SHA256: dcbdd5b7f5a6ee4878dc05b57becff6cdcf83a8990d93a9f40451bc3330264a1

```md
# 高层设计：fail-closed / robustness 收口（6 项）

> open 阶段高层决策。深度技术 RFC 由 comet-design 产出至 `docs/superpowers/specs/`。全部非 judge.py，叠在 add-position-tp-sink-halt-recovery 分支上。

## 一手代码事实（已核对）

- **P2-03** `agents/trading/executor.py:_handle_resume`（约 508-533）三分支：(a) matched → 恢复 + return；(b) `if self._reconciler:` → 调 `self._reconciler.reconcile(executor_positions=...)`，但 `_reconciler` 是 `utils/reconciliation.py:Reconciler`（方法仅 check_recent_bills/should_run/run_and_report/auto_resolve_pending，**无 reconcile**）→ AttributeError → except 维持熔断；(c) `else`（无 reconciler）→ `confirm_resume(reconcile_ok=True)` **无条件恢复**。`_reconciler` 在 38/53 默认 None、62 条件设为 Reconciler。
- **P2-06** `_handle_risk_alert`（约 544）只读 `type`/`scope`，无 `source` 守卫；paper 经 `paper_executor.py` 发 `risk_alert{type=paper_unfilled, source=paper_executor}` 到共享 topic，live executor 订阅裸 `risk_alert` 收到它。
- **P2-16** `orchestrator._write_agent_health`（284-329）已算 `dlq_size = len(getattr(bus,'_dead_letter',[]))`、`_health_loop`（331）每 30s 调它；已有 `bus.publish("orchestrator","telegram_alert",{...})`（203）通道。
- **P2-17** 根 `executor.py`（104-118）：config_loader 成功路径已 clamp；except 兜底直读 `os.getenv` 风险限额，**不经 HARD_LIMITS clamp**。
- **P2-20** `utils/event_journal.py`（83-84）：`self._fd.write(line+"\n"); self._fd.flush()`，无 fsync。
- **P2-21** `utils/halt_state.py:_save`（103-114）：主路径 `atomic_write_json`；except 兜底 `open(path,'w'); json.dump`（非原子）。

## 决策

### P2-03：非 matched resume 统一 fail-closed
把 (b)+(c) 两分支**合并为单一显式 fail-closed**：非 matched 对账结果 → `confirm_resume(reconcile_ok=False)` + 维持熔断 + warning。理由：
- `_reconciler` 是 PnL 账本对账器，本就不该承担持仓对账；`reconcile()` 调用是历史重构遗留 bug。
- "想绕过对账恢复" 已有专门的 `force_resume`（跳过对账，显式授权）。常规 `resume` 必须带 matched 对账结果（TG 侧已做四方对账）才能恢复。
- 因此 `else`（无 reconciler）无条件恢复是 fail-open 隐患，收紧为同样维持熔断。
- **契约**：常规 resume 的唯一成功条件 = `reconciliation_result.status == 'matched'`；其余一律维持熔断，运维用 `/force_resume` 显式恢复。
- 需核对 `test_halt_resume_ownership.py` / `test_reconciliation.py` 是否依赖旧 else 自动恢复；若有，按新契约调整（force_resume 覆盖该语义）。

### P2-06：结构性 source 守卫
`_handle_risk_alert` 顶部加 `if alert.get('source') == 'paper_executor': return`。paper 永不驱动 live 平仓/风控，隔离从"白名单未命中"升级为结构性。

### P2-16：DLQ 阈值告警
`orchestrator` 加 `self._prev_dlq_size`（init 0）。`_write_agent_health` 算出 `dlq_size` 后：若 `dlq_size > _prev_dlq_size`（DLQ 增长，说明有新死信/重要 topic 无订阅者），publish 一条 `telegram_alert{type='bus_dlq_growth', dlq_size, delta}`；更新 `_prev_dlq_size`。复用现有 30s health loop + telegram_alert 通道，零新循环。

### P2-17：兜底也 clamp
except 兜底分支拿到 env 原始值后，复用 config_loader 的 HARD_LIMITS clamp（import clamp helper 或 HARD_LIMITS 字典）对 max_amount/max_dd/max_daily/cap 各自 clamp，杜绝 fail-open。若 clamp helper 不便复用，退而 fail-closed：config_loader 失败直接 raise 拒绝启动（更激进，按 build 期实测取舍）。

### P2-20：journal fsync
`write`+`flush` 后加 `os.fsync(self._fd.fileno())`。journal 只记 5 类低频 critical topic，fsync 成本可接受，与 atomic_io 标准对齐。

### P2-21：删非原子兜底
`halt_state._save` 删除 except 内的 `open(path,'w'); json.dump` 裸写，改为只 `logger`（或静默）记录 atomic_write_json 失败。halt 文件宁可写失败被上层发现，不要写半截（损坏时 _load fail-closed halt=True 仍兜得住）。

## 测试与红线

- 每项补/扩单测（见 tasks）。
- P2-03 改 resume 契约属风控行为，核对 event_backtest 是否涉及（resume 非回测路径，预计无同构对象，记录理由）。
- 基线 1071 → 变更后须全绿。

## Spec Patch（delta spec）

- `risk-alert-routing` MODIFIED：paper source 结构性守卫。
- `tg-symbol-halt-control` MODIFIED：非 matched resume fail-closed 契约。
- `tg-status-enhancement` MODIFIED：DLQ 死信主动告警。
- P2-17/20/21 纯实现，无 delta。
```

## openspec/changes/fail-closed-robustness-hardening/tasks.md

- Source: openspec/changes/fail-closed-robustness-hardening/tasks.md
- Lines: 1-36
- SHA256: d53cc4641abdb044d341d3d49c4c7efed900a1514ab4448a4f327bdbd57bac72

```md
# Tasks: fail-closed-robustness-hardening

## P2-03：非 matched resume 统一 fail-closed
- [ ] `agents/trading/executor.py:_handle_resume` 合并 (b)+(c) 分支为单一 fail-closed：非 matched → `confirm_resume(reconcile_ok=False)` + 维持熔断 + warning；删除 `self._reconciler.reconcile(...)` 死调用
- [ ] 核对 `test_halt_resume_ownership.py` / `test_reconciliation.py` 是否依赖旧 else 自动恢复；按新契约调整或补用例
- [ ] 补用例：非 matched resume（含无 reconciler）维持熔断不恢复；matched 仍恢复；force_resume 仍可跳过

## P2-06：risk_alert source 守卫
- [ ] `_handle_risk_alert` 顶部加 `if alert.get('source') == 'paper_executor': return`
- [ ] 补用例：paper_executor 来源 risk_alert 不触发任何 live 平仓/缩仓；live 来源不受影响

## P2-16：DLQ 阈值告警
- [ ] `agents/orchestrator.py` 加 `_prev_dlq_size`；`_write_agent_health` 在 dlq 增长时 publish `telegram_alert{type='bus_dlq_growth'}`
- [ ] 补用例（`test_tg_status_enhancement.py`）：dlq_size 增长触发告警；不增长不告警

## P2-17：config 兜底 HARD_LIMITS clamp
- [ ] 根 `executor.py` config_loader except 兜底分支对 env 风险限额套 HARD_LIMITS clamp（复用 config_loader helper）；或失败 fail-closed
- [ ] 补用例：config_loader 抛异常时风险限额仍被 clamp 到 HARD_LIMITS 内，不 fail-open

## P2-20：event_journal fsync
- [ ] `utils/event_journal.py` write+flush 后加 `os.fsync(self._fd.fileno())`
- [ ] 补用例：写入后文件描述符被 fsync（mock os.fsync 断言调用）

## P2-21：halt_state 删非原子裸写兜底
- [ ] `utils/halt_state.py:_save` 删除 except 内非原子裸写，改 logger 记录失败
- [ ] 补用例：atomic_write_json 失败时不产生半截文件（不再裸写）

## 同构与回归（CLAUDE.md 红线）
- [ ] 核对 event_backtest 是否涉及 resume/risk_alert 路径（预计无，记录理由）
- [ ] 全量 `python3 -m pytest -q` 全绿（基线 1071 + 新增用例上调）
- [ ] `compileall executor.py agents utils` 通过

## 归档阶段事项（非 build 勾选项，散文记录）

> comet-archive 阶段处理：delta spec（risk-alert-routing / tg-symbol-halt-control / tg-status-enhancement）同步至 master。
> CLAUDE.md / `docs/to-do-list.md` / 记忆的 P2 关闭——**并发避让**：与另一窗口 P1-02/P1-03 统一协调后再一次性追加，不在本 change 单独改共享文档。
```

## openspec/changes/fail-closed-robustness-hardening/specs/risk-alert-routing/spec.md

- Source: openspec/changes/fail-closed-robustness-hardening/specs/risk-alert-routing/spec.md
- Lines: 1-17
- SHA256: 8a511c35fe42fc3ab1c6e676d2811374fb2db3502287325188cc4d76409ed476

```md
## ADDED Requirements

### Requirement: live executor 的 risk_alert handler 必须以 source 守卫拒绝 paper 来源
live `MultiExecutor._handle_risk_alert` 处理 `risk_alert` 事件时，MUST 在分发任何动作前以
`source` 字段做结构性守卫——`source == 'paper_executor'` 的事件 MUST 被直接忽略（return），
不得进入任何 live 平仓 / 缩仓 / halt 分支。paper 与 live 共用 `risk_alert` topic 时，隔离
MUST 由该 source 守卫保证，MUST NOT 依赖"paper 的 alert type 恰好不在 live 白名单内"这一
脆性巧合。

#### Scenario: paper 来源 risk_alert 不驱动 live 动作
- **WHEN** live executor 收到 `risk_alert{source='paper_executor', type='paper_unfilled'}`
- **THEN** handler MUST 直接 return，不调用任何 close / reduce / halt
- **AND** 即便未来 paper 复用与 live 白名单同名的 type，也 MUST NOT 触发 live 平仓

#### Scenario: live 来源 risk_alert 不受守卫影响
- **WHEN** live executor 收到 `risk_alert{source!='paper_executor'}`（如 emergency_close / max_drawdown）
- **THEN** handler MUST 正常按 type 分发处理
```

## openspec/changes/fail-closed-robustness-hardening/specs/tg-status-enhancement/spec.md

- Source: openspec/changes/fail-closed-robustness-hardening/specs/tg-status-enhancement/spec.md
- Lines: 1-18
- SHA256: d22c4c4ad56a5566ac6dbd7e160c0eb272c919eafff22f31bfec7a5468fcb4a2

```md
## ADDED Requirements

### Requirement: bus DLQ 增长必须主动告警
Orchestrator 周期性健康循环（已有 `_health_loop` / `_write_agent_health`，约 30s）在算出
`dlq_size = len(bus._dead_letter)` 后，MUST 与上一次记录的 `_prev_dlq_size` 比较；当
`dlq_size > _prev_dlq_size`（出现新死信，说明有 enqueue 失败或重要 topic 无订阅者）时，MUST
经现有 `telegram_alert` 通道主动 publish 一条告警事件（含当前 dlq_size 与本次增量 delta），
不得仅把 DLQ 计数静默写入 `agent_health.json`。比较基准 `_prev_dlq_size` MUST 在每次健康
tick 后更新，使告警按 30s cadence 天然限流、不重复刷屏。

#### Scenario: DLQ 增长触发告警
- **WHEN** 某次健康 tick 算出 `dlq_size=3` 且 `_prev_dlq_size=0`
- **THEN** MUST publish `telegram_alert{type='bus_dlq_growth', dlq_size=3, delta=3}`
- **AND** 随后 `_prev_dlq_size` MUST 更新为 3

#### Scenario: DLQ 未增长不告警
- **WHEN** 某次健康 tick 的 `dlq_size <= _prev_dlq_size`
- **THEN** MUST NOT publish bus_dlq_growth 告警
```

## openspec/changes/fail-closed-robustness-hardening/specs/tg-symbol-halt-control/spec.md

- Source: openspec/changes/fail-closed-robustness-hardening/specs/tg-symbol-halt-control/spec.md
- Lines: 1-25
- SHA256: b3c8f98403e90e690a92745dcbb94d3208eb2361d3b90bd33018f3a403728b48

```md
## ADDED Requirements

### Requirement: 全局 resume 非 matched 对账结果时必须 fail-closed 维持熔断
`MultiExecutor._handle_resume` 处理常规 `resume`（非 force_resume）时，唯一允许恢复交易的条件
MUST 是 `payload.reconciliation_result.status == 'matched'`。任何非 matched 的情形——包括
缺 `reconciliation_result`、status 非 matched、或无本地对账器——MUST fail-closed：
`confirm_resume(reconcile_ok=False)` 维持熔断 + 记 warning，MUST NOT 恢复交易。

系统 MUST NOT 在该路径调用 PnL 账本对账器 `utils/reconciliation.py:Reconciler` 上不存在的
`reconcile` 方法。绕过对账恢复 MUST 经独立的 `/force_resume` 路径显式授权，常规 `/resume`
MUST NOT 提供无对账的恢复。

#### Scenario: 非 matched resume 维持熔断
- **WHEN** `_handle_resume` 收到 `reconciliation_result` 缺失或 status != 'matched'
- **THEN** MUST 调 `confirm_resume(reconcile_ok=False)` 维持熔断
- **AND** MUST NOT 恢复交易（`_trading_halted` 不被置 False）
- **AND** MUST NOT 抛 AttributeError（不调不存在的 reconcile）

#### Scenario: 无本地对账器也不无条件恢复
- **WHEN** `_handle_resume` 非 matched 且 `self._reconciler` 为 None
- **THEN** MUST 维持熔断（fail-closed），MUST NOT `confirm_resume(reconcile_ok=True)`

#### Scenario: matched 仍正常恢复
- **WHEN** `reconciliation_result.status == 'matched'`
- **THEN** MUST `confirm_resume(reconcile_ok=True)` + 清 per-symbol halt + 恢复交易
```

