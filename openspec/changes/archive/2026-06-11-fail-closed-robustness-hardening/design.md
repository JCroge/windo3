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
