---
change: fail-closed-robustness-hardening
design-doc: docs/superpowers/specs/2026-06-11-fail-closed-robustness-hardening-design.md
base-ref: e1333c58af0b36c61ac100f656f0e2cd31b20a03
archived-with: 2026-06-11-fail-closed-robustness-hardening
---

# fail-closed / robustness 收口（6 项）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。TDD + 每任务提交。

**Goal:** 一次性收口第五次审计第二梯队 6 项 fail-closed/robustness 缺口（全非 judge.py）。

**Architecture:** 6 处 surgical 改：P2-03 resume 契约收紧（agents/trading/executor.py）、P2-06 risk_alert source 守卫（同）、P2-16 DLQ 告警（orchestrator）、P2-17 config clamp 兜底（根 executor.py）、P2-20 journal fsync（utils/event_journal.py）、P2-21 halt 原子写（utils/halt_state.py）。

**Tech Stack:** Python 3.9, pytest。

archived-with: 2026-06-11-fail-closed-robustness-hardening
---

## Task 1: P2-06 risk_alert source 守卫（最简，先做）

**Files:** Modify `agents/trading/executor.py`（`_handle_risk_alert` 顶部）；Test `test_riskguard_upgrade.py` 或新建

- [ ] **Step 1: 失败测试** — paper_executor 来源 risk_alert 不触发 live 动作（断言 handler 早 return，不调 close/reduce）
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** — `_handle_risk_alert` 顶部（docstring 后第一行）加：
```python
        if alert.get('source') == 'paper_executor':
            return
```
- [ ] **Step 4: 跑测试确认通过 + 全 risk_alert 套件回归**
- [ ] **Step 5: 提交** `fix(executor): structural paper-source guard in _handle_risk_alert (P2-06)`

## Task 2: P2-03 非 matched resume fail-closed

**Files:** Modify `agents/trading/executor.py:_handle_resume`；Test `test_halt_resume_ownership.py`

- [ ] **Step 1: 失败测试** — 非 matched resume（含 `_reconciler=None` 与 `_reconciler=Reconciler` 两态）维持熔断（`_trading_halted` 仍 True / confirm_resume reconcile_ok=False）；matched 仍恢复
- [ ] **Step 2: 跑测试确认失败/暴露旧行为**
- [ ] **Step 3: 实现** — 把 `_handle_resume` 的 (b)`if self._reconciler` + (c)`else` 两分支整体替换为：
```python
        # 非 matched：fail-closed 维持熔断，绕过对账须显式 /force_resume
        self._halt_state.confirm_resume(resume_by=source, reconcile_ok=False)
        self.logger.warning(
            f"[熔断维持] {source} resume 未带 matched 对账结果，维持熔断；"
            f"如需跳过对账请用 /force_resume"
        )
```
（保留 (a) matched 分支不变；删除对 `self._reconciler.reconcile(...)` 的死调用）
- [ ] **Step 4: 核对既有测试** — `test_halt_resume_ownership.py` / `test_reconciliation.py` 若依赖旧 else 自动恢复，按新契约调整（force_resume 覆盖）
- [ ] **Step 5: 跑测试确认通过**
- [ ] **Step 6: 提交** `fix(executor): non-matched resume fail-closed, drop dead Reconciler.reconcile (P2-03)`

## Task 3: P2-21 halt_state 删非原子裸写

**Files:** Modify `utils/halt_state.py:_save`；Test 新建/`test_state_namespace.py`

- [ ] **Step 1: 失败测试** — atomic_write_json 抛异常时 `_save` 不调 `open(path,'w')`（mock 断言）
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** — `_save` 删除 except 内 `try: open(path,'w'); json.dump ... except: pass`，改为：
```python
        except Exception as e:
            try:
                import logging
                logging.getLogger('halt_state').error(f"halt_state 原子写失败: {e}")
            except Exception:
                pass
```
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交** `fix(halt_state): drop non-atomic naked-write fallback in _save (P2-21)`

## Task 4: P2-20 event_journal fsync

**Files:** Modify `utils/event_journal.py`；Test 新建/`test_p1k_message_bus.py` 旁

- [ ] **Step 1: 失败测试** — 写入关键事件后 `os.fsync` 被调（mock os.fsync 断言）
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** — `self._fd.flush()` 后加：
```python
        try:
            os.fsync(self._fd.fileno())
        except OSError:
            pass
```
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交** `fix(event_journal): fsync after critical-event write (P2-20)`

## Task 5: P2-17 config 兜底 HARD_LIMITS clamp

**Files:** Modify 根 `executor.py`（104-118 except 兜底）；Test 新建/`test_drawdown_baseline.py`

- [ ] **Step 1: 核对** `utils/config_loader.py` 的 HARD_LIMITS / clamp helper 暴露形态（grep `HARD_LIMITS`/`def clamp`），确定复用方式
- [ ] **Step 2: 失败测试** — config_loader 抛异常时风险限额仍被 clamp 到 HARD_LIMITS 内（构造超界 env，断言 RiskManager 收到 clamp 后值）
- [ ] **Step 3: 跑测试确认失败**
- [ ] **Step 4: 实现** — except 兜底分支拿到 env 原始值后，复用 config_loader 的 clamp（import helper 或 HARD_LIMITS 字典 min/max）对 max_amount/max_dd/max_daily/cap 各自 clamp
- [ ] **Step 5: 跑测试确认通过**
- [ ] **Step 6: 提交** `fix(executor): clamp env-fallback risk limits to HARD_LIMITS (P2-17)`

## Task 6: P2-16 DLQ 增长告警

**Files:** Modify `agents/orchestrator.py`；Test `test_tg_status_enhancement.py`

- [ ] **Step 1: 核对** `_write_agent_health` / `_health_loop` 上下文（同步 vs async）与 telegram_alert publish 方式（参照 line 203）
- [ ] **Step 2: 失败测试** — dlq_size 由 0→3 触发 `telegram_alert{type='bus_dlq_growth'}`；不增长不发
- [ ] **Step 3: 跑测试确认失败**
- [ ] **Step 4: 实现** — orchestrator init 加 `self._prev_dlq_size = 0`；在算出 dlq_size 后比较 `_prev_dlq_size`，增长则 publish telegram_alert（含 dlq_size + delta），随后更新 `_prev_dlq_size`。若 `_write_agent_health` 是同步、publish 需 async，则在 `_health_loop` 异步上下文中发。
- [ ] **Step 5: 跑测试确认通过**
- [ ] **Step 6: 提交** `feat(orchestrator): proactive telegram alert on DLQ growth (P2-16)`

## Task 7: 回归 + 同构 + 收尾

- [ ] **Step 1: 全量** `python3 -m pytest -q` 全绿（基线 1071 + 新增上调）
- [ ] **Step 2: 编译** `compileall executor.py agents utils`
- [ ] **Step 3: 同构** 核对 event_backtest 无 resume/risk_alert/journal/halt/config 决策路径 → 记录理由
- [ ] **Step 4: 勾选 change tasks.md，提交**

archived-with: 2026-06-11-fail-closed-robustness-hardening
---

## Self-Review

- **Spec coverage**：delta `risk-alert-routing`→Task1；`tg-symbol-halt-control`→Task2；`tg-status-enhancement`→Task6；纯实现 P2-21/20/17→Task3/4/5。全覆盖。
- **Placeholder scan**：实现代码完整；Task5/6 的 Step1「核对」是真实实现期校验点（config_loader API、health loop async 上下文），非 placeholder。
- **Type consistency**：`source` 字段、`_prev_dlq_size`、`bus_dlq_growth` type、HARD_LIMITS 复用三处一致。
