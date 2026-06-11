---
comet_change: fail-closed-robustness-hardening
role: technical-design
canonical_spec: openspec
---

# 技术设计：fail-closed / robustness 收口（6 项）

> 上游事实源是 OpenSpec 产物。本文描述 HOW。范围 = 第五次审计第二梯队 6 项（用户已确认整批 + P2-03 收紧）。全部非 judge.py，叠在 add-position-tp-sink-halt-recovery 分支上。需求口径以 `openspec/changes/fail-closed-robustness-hardening/specs/` 为准。

## P2-03：非 matched resume 统一 fail-closed（唯一行为契约变更）

**现状**（`agents/trading/executor.py:_handle_resume` 约 508-533）三分支：
- (a) `reconciliation_result.status == 'matched'` → 恢复 + return
- (b) `if self._reconciler:` → `self._reconciler.reconcile(executor_positions=...)` —— `_reconciler` 是 `utils/reconciliation.py:Reconciler`（PnL 账本，**无 reconcile 方法**）→ AttributeError → except 维持熔断
- (c) `else`（无 reconciler）→ `confirm_resume(reconcile_ok=True)` 无条件恢复（fail-open 隐患）

**改为**两分支：
```python
if reconciliation_result and reconciliation_result.get('status') == 'matched':
    self._halt_state.confirm_resume(resume_by=source, reconcile_ok=True)
    self._trading_halted = False
    self._safe_clear_symbol_halt(None, source=f"_handle_resume:{source}")
    self.logger.info(f"[解除熔断] 通过{source}触发，对账通过")
    return
# 非 matched：fail-closed 维持熔断，绕过对账须显式 /force_resume
self._halt_state.confirm_resume(resume_by=source, reconcile_ok=False)
self.logger.warning(
    f"[熔断维持] {source} resume 未带 matched 对账结果，维持熔断；"
    f"如需跳过对账请用 /force_resume"
)
```

**契约**：常规 `/resume` 唯一成功条件 = `reconciliation_result.status == 'matched'`（TG 侧四方对账后才发）。其余一律 fail-closed 维持熔断；`/force_resume`（独立路径，已存在）是显式跳过对账的唯一授权方式。删除对 PnL `Reconciler` 不存在的 `reconcile` 调用，消除 latent AttributeError，并收紧无 reconciler 的 fail-open。

**风险/核对**：`test_halt_resume_ownership.py` / `test_reconciliation.py` 若依赖旧 (c) 自动恢复，按新契约调整（语义由 force_resume 覆盖）。`_reconciler` 在 985/994/1040 的 PnL 用途（should_run/auto_resolve_pending/run_and_report）不动。

## P2-06：risk_alert 结构性 source 守卫

`_handle_risk_alert(self, alert)`（约 544）顶部加：
```python
if alert.get('source') == 'paper_executor':
    return
```
paper 经 `paper_executor.py` 发 `risk_alert{type=paper_unfilled, source=paper_executor}` 到共享 topic；live executor 订阅裸 `risk_alert` 收到它。当前仅靠"type 白名单未命中"安全（脆性）。本守卫使隔离结构化——paper 来源永不驱动 live 平仓/缩仓，无论将来 type 是否与 live 白名单撞名。

## P2-16：DLQ 增长主动告警

`orchestrator` 加 `self._prev_dlq_size = 0`（init）。`_write_agent_health`（284-329）已算 `dlq_size = len(getattr(bus,'_dead_letter',[]))`；在写完 health 后：
```python
if dlq_size > self._prev_dlq_size:
    await/publish telegram_alert {type:'bus_dlq_growth', dlq_size, delta: dlq_size - self._prev_dlq_size}
self._prev_dlq_size = dlq_size
```
复用现有 30s `_health_loop`（天然限流）+ `bus.publish("orchestrator","telegram_alert",...)`（203 已有通道）。`_write_agent_health` 是同步方法，告警发布走与 203 一致的方式（必要时在 `_health_loop` 异步上下文发）。

## P2-17：config 兜底也 clamp

根 `executor.py`（104-118）except 兜底分支拿 env 原始值后，复用 `utils/config_loader` 的 HARD_LIMITS clamp 对 max_amount/max_dd/max_daily/cap 各自 clamp。实现期确认 config_loader 暴露的 clamp helper（HARD_LIMITS 字典或 clamp 函数）；若不便复用，内联一个 min/max clamp 用同一组 HARD_LIMITS 常量。**不选** fail-closed 拒启动——实盘宁可用 clamp 后的安全默认启动，也不要因 config_loader 偶发失败完全不启动。

## P2-20：journal fsync

`utils/event_journal.py`（83-84）`self._fd.write(line+"\n"); self._fd.flush()` 后加 `os.fsync(self._fd.fileno())`。只 5 类低频 critical topic，fsync 成本可接受，与 `atomic_io` 已 fsync 标准对齐。

## P2-21：halt_state 删非原子裸写

`utils/halt_state.py:_save`（103-114）删除 except 内 `open(path,'w'); json.dump` 裸写，改为 `logger`（模块级 logger 或 print 到 stderr）记录 atomic_write_json 失败。halt 文件宁可写失败被发现，不要写半截；损坏时 `_load` 仍 fail-closed（halt=True）兜底。

## 测试策略

- P2-03：非 matched resume（含无 reconciler）维持熔断 / matched 仍恢复 / force_resume 仍跳过（`test_halt_resume_ownership.py`）
- P2-06：paper_executor 来源 risk_alert 不触发 live 平仓/缩仓；live 来源不受影响
- P2-16：dlq 增长触发 telegram_alert，不增长不发（`test_tg_status_enhancement.py`）
- P2-17：config_loader 抛异常时风险限额仍被 clamp 到 HARD_LIMITS 内
- P2-20：写入后 os.fsync 被调（mock 断言）
- P2-21：atomic_write_json 失败时不再裸写（mock 断言不调 open(path,'w')）

## 同构核对（CLAUDE.md 红线）

resume / risk_alert / journal / halt / config 均非 `event_backtest.py` 决策路径（grep 确认）。无同构对象需同步，记录理由。

## Spec Patch（回写 delta spec）

- `risk-alert-routing` ADDED：live executor risk_alert handler MUST 以 source 守卫拒绝 paper 来源。
- `tg-symbol-halt-control` ADDED：非 matched resume MUST fail-closed 维持熔断。
- `tg-status-enhancement` ADDED：DLQ 增长 MUST 经 telegram_alert 主动告警。
- P2-17/20/21 纯实现健壮性，无 requirement 变更，无 delta。
