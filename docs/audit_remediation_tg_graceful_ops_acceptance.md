# TG Graceful Ops 整改验收报告

- 整改窗口：2026-05-30 → 2026-06-01
- 整改基线：F4 闭环后 `860 passed / 4 deselected / 1 warning`
- 闭环目标：5/30 XLM 8 小时静默拒单 bug + `docs/to-do-list.md` 行 58 / 行 64
- 工作分支：`feat/tg-graceful-ops-20260530`（worktree from `feat/audit-fourth-pass-20260529`）
- OpenSpec change：`tg-graceful-ops`
- Comet 状态文件：`openspec/changes/tg-graceful-ops/.comet.yaml`

## 1. 范围

| FR | 等级 | 主题 |
|---|---|---|
| F-TG-001 | P0 | `/resume` + `/force_resume` 同步清 root executor `_halted_symbols` 残留 |
| F-TG-002 | P1 | `/halts` `/resume_symbol` 命令；`/status` 显示 per-symbol halt 行 |
| F-TG-003 | P1 | `/pnl <SYMBOL>` `/pnl_id <event_id>` 手动 PnL correction 命令 |
| F-TG-004 | P1 | `/status` agent health 轻量（Orchestrator 周期写 `agent_health.json`） |

详细需求与验收契约见 OpenSpec delta spec：

- `openspec/changes/tg-graceful-ops/specs/tg-symbol-halt-control/spec.md`
- `openspec/changes/tg-graceful-ops/specs/tg-pnl-correction/spec.md`
- `openspec/changes/tg-graceful-ops/specs/tg-status-enhancement/spec.md`

## 2. 验收命令

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/crypto_tg_pycache python3 -m compileall -q .
python3 -m pytest -q
python3 -m pytest -q -m network
# 真实 TG 命令链验证(待 live 部署后人工执行,见 §6)
```

## 3. 验收结果

### 3.1 字节码扫描

`compileall` 静默退出，无 SyntaxError。

### 3.2 默认全量回归

```
921 passed, 4 deselected, 1 warning in 145.75s
```

相对 F4 闭环后基线 `860 passed`，本次净增 **61 case**（plan 预期 ≥ 35）：

- `test_tg_symbol_halt_control.py` 30 case（F-TG-001 + F-TG-002）
- `test_tg_pnl_correction.py` 15 case（F-TG-003）
- `test_tg_status_enhancement.py` 16 case（F-TG-004）

中途修复 1 个回归（`test_halt_resume_ownership.py::test_executor_resume_with_reconciliation_pass`）：
- 根因：老测试构造 `MultiExecutor.__new__` 时不 mock `executor.executor`，Task 2 加的 `clear_symbol_halt` 调用触发 AttributeError
- 修复：新增 `_safe_clear_symbol_halt` private helper，对 `self.executor` 缺失 fail-soft（生产环境 self.executor 必然存在，仅单测兜底）

### 3.3 network 分层回归

```
1 passed, 3 skipped, 921 deselected, 1 warning in 14.86s
```

3 skipped 是 P1 FR-007 行为（缺 klines.db 时干净 skip），与基线一致。

### 3.4 真实 TG 验收（PENDING — verify 阶段执行）

build 阶段无法跑（live 进程在主分支跑老代码，不含本 worktree 改动）。已在 `docs/generated_reports/tg_graceful_ops_mock_run_pending.md` 记录待验收命令链，verify 阶段合并到 live 部署点 + OS 重启后人工 TG 执行。

## 4. 验收明细

### 4.1 F-TG-001 tg-symbol-halt-control（resume + 公开 API）

| AC | 实现 | 测试 |
|---|---|---|
| `clear_symbol_halt(symbol=None, *, source="unknown")` 暴露 | `executor.py` 916-948 | `TestClearSymbolHalt` 4 + audit log 4 |
| `get_halted_symbols()` 浅拷贝 | `executor.py` 950-955 | `TestGetHaltedSymbols` 2 |
| `_handle_resume` 三成功分支调 `_safe_clear_symbol_halt(None, source=...)` | `agents/trading/executor.py:_handle_resume` | `TestHandleResumeClearsHaltedSymbols` 4 |
| failure 分支不清 | 同上 | covered |
| `force_resume` 清 + audit warning + risk_alert publish | `agents/trading/executor.py` system_command force_resume 分支 | `TestForceResumeClearsWithAudit` 2 |
| audit log 含 source 字段 | `clear_symbol_halt` log line | covered（4 audit-log tests） |

### 4.2 F-TG-002 tg-symbol-halt-control（TG 命令）

| AC | 实现 | 测试 |
|---|---|---|
| `/halts` 列出 per-symbol halt | `_cmd_halts` + `_read_agent_health` + `_format_elapsed` | `TestCmdHalts` 3 + `TestFormatElapsed` 3 |
| `/resume_symbol <SYMBOL>` 走 bus | TG `_cmd_resume_symbol` publish system_command | `TestCmdResumeSymbolViaBus` 2 |
| MultiExecutor 处理 cmd='resume_symbol' | `agents/trading/executor.py:on_message` | `TestExecutorAgentResumeSymbol` 3 |
| 三种新 risk_alert types 回显 | `_handle_risk_alert` 加 critical_types + 分支 | `TestTelegramAlertSubscriptions` 3 |
| TG agent 不持有 root executor 引用 | bus 路由替代直调 | covered |

### 4.3 F-TG-003 tg-pnl-correction

| AC | 实现 | 测试 |
|---|---|---|
| `_resolve_pending_for_pnl_correction` 共用 helper | TG telegram_notifier.py | `TestResolvePendingHelper` 4 |
| `_apply_pnl_correction` 写 manual correction | 同上 | covered（间接测） |
| `/pnl <SYMBOL>` 0/1/多候选分流 | `_cmd_pnl` | `TestCmdPnl` 5 |
| reason 字段写入 correction | 同上 | `TestCmdPnlReason` 2 |
| `/pnl_id <event_id>` 精确匹配 | `_cmd_pnl_id` | `TestCmdPnlId` 4 |
| ledger lazy-init | TG `setup()` 内 `LiveLedger(exchange=None)` | covered |

### 4.4 F-TG-004 tg-status-enhancement

| AC | 实现 | 测试 |
|---|---|---|
| `StatePaths.agent_health` 字段 + namespace 派生 | `utils/state_paths.py` | `TestStatePathsAgentHealth` 4 |
| MultiExecutor publish `halts_snapshot` 周期事件 | `agents/trading/executor.py:_publish_halts_snapshot` | `TestMultiExecutorPublishHaltsSnapshot` 2 |
| Orchestrator 订阅 + 写 `agent_health.json` | `agents/orchestrator.py:_on_halts_snapshot/_write_agent_health/_health_loop` | `TestOrchestratorWritesAgentHealth` 5 |
| `/status` 含 Agents / Bus DLQ / Per-symbol halt 行 | TG `_cmd_status` 末尾 health 块 | `TestStatusEnhancement` 5 |

## 5. 实施顺序与提交链

按 design doc 第 8 章顺序：

1. **F-TG-001** (Task 1-2): root API + agent resume/force_resume 改造
2. **F-TG-004** (Task 3-5): state_paths + MultiExecutor publish + Orchestrator 写 health
3. **F-TG-002** (Task 6-8): TG `/halts` `/resume_symbol` `/status`
4. **F-TG-003** (Task 9-11): TG `/pnl` `/pnl_id` + ledger lazy-init
5. **收尾** (Task 12-16): 字节码 + pytest + network + 验收报告 + 文档同步

11 个实现 commit + 1 个 [TG-fix] regression 修复 commit + 1 个 [TG-acceptance] commit + 1 个 [TG-docs] commit。

| Commit | 内容 |
|---|---|
| `52bebba` | [TG-001] clear_symbol_halt + get_halted_symbols + audit log source 参数 |
| `f00c966` | [TG-001] _handle_resume 三分支 + force_resume 同步清 |
| `ba86dd1` | [TG-004] StatePaths.agent_health |
| `75db499` | [TG-004] MultiExecutor publish halts_snapshot |
| `7be8457` | [TG-004] Orchestrator 订阅 + 写 agent_health.json |
| `663cc1d` | [TG-002] /halts + helpers |
| `ee42737` | [TG-002] /resume_symbol via bus + 三 risk_alert types |
| `775510f` | [TG-004] /status 增强 |
| `f3e5dba` | [TG-003] _resolve_pending_for_pnl_correction + _apply_pnl_correction |
| `5fee9f2` | [TG-003] /pnl + setup ledger lazy-init |
| `360eae2` | [TG-003] /pnl_id |
| `1be7521` | [TG-fix] _safe_clear_symbol_halt fail-soft（test_halt_resume_ownership 回归修复） |

## 6. Go/No-Go

| 范围 | 当前状态 |
|---|---|
| 本地开发 | GO |
| paper / mock | GO |
| 小额 live 灰度 | GO（待真实 TG 验证后扩范围） |
| live 扩容 | CONDITIONAL GO — verify 阶段合并到 live 部署点后 OS 重启 + 真实 TG 命令链验证后解除 |

## 7. 待 verify 阶段执行

- 合并到 `feat/audit-fourth-pass-20260529`（base branch）然后到 main
- live 部署点 `git pull` + OS 重启（按 user feedback：Telegram /restart 同进程循环不重新 import）
- 启动 banner 应该显示 `agent_health → data/agent_health.json` + `BOT_INSTANCE_ID` 行
- 30 秒后 `data/agent_health.json` 应有内容
- 真实 TG 验收命令链（见 §3.4 / `docs/generated_reports/tg_graceful_ops_mock_run_pending.md`）

## 8. 附件

- 全量 pytest 输出: 见 §3.2
- network 输出: 见 §3.3
- Plan: `docs/superpowers/plans/2026-05-30-tg-graceful-ops.md`
- Design Doc: `docs/superpowers/specs/2026-05-30-tg-graceful-ops-design.md`
- OpenSpec change: `openspec/changes/tg-graceful-ops/`
- 待人工验收: `docs/generated_reports/tg_graceful_ops_mock_run_pending.md`
