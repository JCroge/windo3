# Tasks: fail-closed-robustness-hardening

## P2-03：非 matched resume 统一 fail-closed
- [x] `_handle_resume` 合并 (b)+(c) 为单一 fail-closed：非 matched → confirm_resume(reconcile_ok=False) + 维持熔断 + warning；删死 reconcile 调用（commit bdb4d94）
- [x] 核对既有测试：`test_tg_symbol_halt_control.py` 两例（local-reconciler / no-reconciler 恢复）按新契约改为维持熔断（commit 55cc8c4）
- [x] 补用例：非 matched（含无 reconciler）维持熔断；object()-reconciler 不抛 AttributeError；matched 仍恢复（test_halt_resume_ownership.py）

## P2-06：risk_alert source 守卫
- [x] `_handle_risk_alert` 顶部加 paper_executor source 守卫（commit 6090692）
- [x] 用例：paper 源不触发 live close；live 源不受影响（test_risk_alert_source_guard.py）

## P2-16：DLQ 增长告警
- [x] `orchestrator` 加 `_prev_dlq_size` + `_maybe_alert_dlq_growth`，`_write_agent_health` 返回 dlq_size，`_health_loop` 调用（commit 1a326ac）
- [x] 用例：dlq 增长触发 telegram_alert；不增长不发（test_dlq_growth_alert.py）

## P2-17：config 兜底 HARD_LIMITS clamp
- [x] config_loader 加 `clamp_to_hard_limits`（clamp 不 raise）；executor 兜底复用之（commit 681d65d）
- [x] 用例：超界值被 clamp 到 HARD_LIMITS 内、None 保持、在界内不动（test_config_clamp_fallback.py）

## P2-20：event_journal fsync
- [x] `event_journal._write_line` write+flush 后加 os.fsync（commit eedbeb9）
- [x] 用例：append 关键事件后 os.fsync 被调（test_event_journal_fsync.py）

## P2-21：halt_state 删非原子裸写兜底
- [x] `halt_state._save` 删非原子裸写，改 logger（commit 9424e60）
- [x] 用例：atomic 写失败时不再裸写 json.dump（test_halt_state_atomic_save.py）

## 同构与回归（CLAUDE.md 红线）
- [x] event_backtest 无 resume/risk_alert/journal/halt/config 决策路径（grep 为空）→ 纯实现/agent 层，无同构对象需同步
- [x] 全量 `python3 -m pytest -q` = `1081 passed / 4 deselected / 1 warning`（1071 + 10 新增）
- [x] `compileall executor.py agents utils` 通过

## 归档阶段事项（非 build 勾选项，散文记录）

> comet-archive 阶段处理：delta spec（risk-alert-routing / tg-symbol-halt-control / tg-status-enhancement）同步至 master。
> CLAUDE.md / `docs/to-do-list.md` / 记忆的 P2 关闭——**并发避让**：与另一窗口 P1-02/P1-03 统一协调后再一次性追加，不在本 change 单独改共享文档。
