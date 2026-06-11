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
