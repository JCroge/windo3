## 1. F-TG-001 Resume 清 per-symbol halt（根因修复）

- [x] 1.1 `executor.py` 新增 `clear_symbol_halt(symbol: Optional[str] = None) -> int` 公开方法（清单个 / 全部 / 缺失返回 0）
- [x] 1.2 `executor.py` 新增 `get_halted_symbols() -> Dict[str, dict]` 返回浅拷贝
- [x] 1.3 `clear_symbol_halt` 内部 logger.info 记录被清的 symbol 列表 + 来源（来自参数 source 字段或 logger 自带 caller info）
- [x] 1.4 `agents/trading/executor.py:_handle_resume` 三条成功分支（line 380-384 / 386-395 / 405-407）末尾各加 `self.executor.clear_symbol_halt(None)` 调用
- [x] 1.5 `agents/trading/executor.py:on_message system_command cmd=force_resume` 分支（line 86-89）加 `self.executor.clear_symbol_halt(None)` 调用
- [x] 1.6 创建 `test_tg_symbol_halt_control.py`，添加 `TestClearSymbolHalt` 类（4 case：不传 / 指定 / 不存在 / 浅拷贝）
- [x] 1.7 添加 `TestHandleResumeClearsSymbolHalt` 类（4 case：reconcile_ok 三分支 + force_resume）
- [x] 1.8 跑 `python3 -m pytest -q test_tg_symbol_halt_control.py test_executor_upgrade.py` 确认无回归

## 2. F-TG-002 `/halts` + `/resume_symbol` + `/status` per-symbol halt 行

- [x] 2.1 `agents/trading/telegram_notifier.py` 在 `_handle_command` handlers 字典加 `/halts` 与 `/resume_symbol`
- [x] 2.2 实现 `_cmd_halts`：通过 bus 异步查询或直接读 `data/<ns_>agent_health.json` 的 `halted_symbols` 字段（design 决定走 bus query for 实时）
  - 选 bus query 路径：publish `system_command{cmd='query_halts'}`,MultiExecutor agent 响应 publish `halts_snapshot`,TG 订阅一次性 reply
  - 或更简单选 file 直读路径：直接读 health.json,接受 30s 延迟（决定后保留一种实现）
- [x] 2.3 实现 `_cmd_resume_symbol`：解析 symbol 参数，publish `system_command{cmd='resume_symbol', symbol=...}`，MultiExecutor agent 响应调用 `executor.clear_symbol_halt(normalized_symbol)`
- [x] 2.4 `agents/trading/executor.py:on_message` 增加 `cmd='resume_symbol'` 与 `cmd='query_halts'`（如选 bus query 路径）
- [x] 2.5 `_cmd_status` 增加 "Per-symbol halt" 行，从 health.json 读 `halted_symbols`（无文件时降级文案）
- [x] 2.6 添加 `TestCmdHalts` 类（3 case：空 / 一个 / 多个）
- [x] 2.7 添加 `TestCmdResumeSymbol` 类（4 case：解锁存在 / 解锁不存在 / 不动全局 / 缺参提示）
- [x] 2.8 添加 `TestStatusPerSymbolHalt` 类（3 case：0 / 1 / 多个截断）
- [x] 2.9 跑 `python3 -m pytest -q test_tg_symbol_halt_control.py test_telegram_notifier.py 2>/dev/null` 确认无回归

## 3. F-TG-003 `/pnl` 手动 PnL correction

- [x] 3.1 `agents/trading/telegram_notifier.py` 在 handlers 字典加 `/pnl`
- [x] 3.2 实现 `_cmd_pnl`：解析 `<SYMBOL> <NET_PNL> [reason]`，参数错误回用法提示
- [x] 3.3 调 `LiveLedger.find_pending_external_closes()`，按 `symbol == normalized_symbol` 过滤
- [x] 3.4 0/1/多 三分支：1 → 构造 resolution 调 `apply_pnl_resolution`；其他 → 拒绝 + 友好消息（多候选时列出 event_id 与 pending 时间）
- [x] 3.5 创建 `test_tg_pnl_correction.py`，添加 `TestCmdPnl` 类（6 case：1 候选成功 / 0 候选 / 多候选 / NET_PNL 解析失败 / 缺参 / chat_id 拒绝）
- [x] 3.6 添加 `TestCmdPnlIdempotency` 类（2 case：重复提交相同 net_pnl 走 0 候选 / 不同 net_pnl 走 0 候选）
- [x] 3.7 添加 `TestCmdPnlReasonField` 类（2 case：含 reason 写入 / 不含 reason 仍成功）
- [x] 3.8 跑 `python3 -m pytest -q test_tg_pnl_correction.py test_live_ledger.py test_exchange_realized_pnl_resolver.py` 确认无回归

## 4. F-TG-004 `/status` agent health 轻量

- [x] 4.1 `utils/state_paths.py` 增加 `agent_health: str` 字段（live=`data/agent_health.json`, testnet=`data/testnet_agent_health.json`），更新 `as_banner_lines` 与 `for_namespace`
- [x] 4.2 `agents/trading/executor.py:MultiExecutor` 增加 `_write_agent_health()` 方法，schema 含 6 字段（ts / agents_registered / tasks_alive / tasks_failed / halted_symbols / bus_dlq_size）
- [x] 4.3 在 `_run_reconciliation` 或 tick 周期调用 `_write_agent_health()`，确保 ≤30s 一次
- [x] 4.4 `agents/orchestrator.py` 暴露 `get_agent_health() -> dict`（agents_registered / tasks_alive / tasks_failed），由 MultiExecutor 通过 self.config 或 module-level lookup 调用
  - 备选：由 orchestrator 周期性写 health.json，executor 仅追加 halted_symbols 与 bus_dlq_size 字段（避免单点全责）
- [x] 4.5 `_cmd_status` 增加 "Agents:" 行 与 "Bus DLQ:" 行
- [x] 4.6 创建 `test_tg_status_enhancement.py`，添加 `TestAgentHealthSchema` 类（3 case：写入完整 / namespace 派生 / 失败不阻塞）
- [x] 4.7 添加 `TestStatusAgentHealth` 类（4 case：含 Agents 行 / 含 Bus DLQ 行 / 异常任务可见 / health 缺失 fallback）
- [x] 4.8 跑 `python3 -m pytest -q test_tg_status_enhancement.py test_state_namespace.py` 确认无回归

## 5. 全量回归与验证收尾

- [x] 5.1 字节码扫描 `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_tg_pycache python3 -m compileall -q .`
- [x] 5.2 默认全量回归 `python3 -m pytest -q` 必须从 860 至少升至 ≥878（+18 case 新增最小预期）
- [x] 5.3 network 分层回归 `python3 -m pytest -q -m network` 仍 4 通过
- [x] 5.4 在本地 mock TG 跑一遍命令链：`/halts` → `/resume_symbol XLM` → `/halts` → `/pnl XLM 0.42` → `/status`，截图 / 日志记录到验收报告
- [x] 5.5 撰写验收报告 `docs/audit_remediation_tg_graceful_ops_acceptance.md`（含验收命令、AC 列表、Mock TG run 摘要）
- [x] 5.6 更新 `CLAUDE.md` 当前事实段：tg-graceful-ops 闭环 + 新基线
- [x] 5.7 更新 `docs/to-do-list.md`：行 58 `/pnl` 与 行 64 `/status` agent health 移到"已关闭"
- [x] 5.8 更新 `docs/runbook.md`（如有）：补充 `/halts` / `/resume_symbol` / `/pnl` 运维 SOP
