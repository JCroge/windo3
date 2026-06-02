## Why

5/30 03:55 XLM SL replace failed 触发了 root executor 的 per-symbol halt（`_halted_symbols` 字典），同时也写了全局 `halt_state.json`。运维通过 Telegram `/resume` 命令解全局 halt 后系统看似已恢复，但 **per-symbol halt 是 in-memory，TG `/resume` 不清它**——结果 XLM 被静默拒绝开仓持续 8 小时（`[Halt] XLM-USDT-SWAP 已 halt，拒绝智能开仓`），运维没有可见性也没有可操作工具，只能等下次进程重启。

同时 `docs/to-do-list.md` 行 58 / 行 64 还有两个 OPEN 的 TG 运维优化没落地：手动 PnL correction 命令、`/status` 缺少 agent 健康可见性。

本 change 把"TG 优雅运维"作为一个完整故事闭环：让运维可以**看到、操作、确认**所有 halt / pending PnL / agent health 状态，从 TG 单一入口，命令准确执行下去不留残留。

## What Changes

- **F-TG-001 修复 resume bug**：`agents/trading/executor.py:_handle_resume` 在全局 resume 成功后同步清理 root executor 的 `_halted_symbols`；force_resume 路径同样清理。新增 `ContractExecutor.clear_symbol_halt(symbol=None)` 公开 API：传 symbol 清单一个、不传清全部。
- **F-TG-002 per-symbol halt 可见可控**：
  - 新增 `/halts` 命令，列出当前所有 per-symbol halt 项（symbol + reason + halted_at）
  - 新增 `/resume_symbol <SYMBOL>` 命令，只解一个 symbol 的 per-symbol halt（不动全局）
  - `/status` 输出增加"per-symbol halt"区块（哪怕只有 0 个也打印一行，避免盲操作）
- **F-TG-003 `/pnl` 手动 PnL correction**（todo 行 58）：
  - 命令格式 `/pnl <SYMBOL> <NET_PNL_USDT> [reason]`
  - 用 `LiveLedger.find_pending_external_closes()` 找该 symbol 的未 supersede pending；候选恰好 1 条 → 写 correction（`source=manual_tg_review`）；多候选或 0 候选 → 拒绝并提示
  - 写入幂等：相同 symbol+net_pnl+pending_event_id 不重复累计
- **F-TG-004 `/status` agent health 轻量版**（todo 行 64 轻量）：
  - 输出 N agent 已注册 / N 个任务存活
  - bus DLQ 计数（如有）
  - 各 agent 是否报过 setup 失败（按 `Orchestrator._tasks` 状态读取）
  - 不引入 heartbeat / loop alive 重构（推迟到独立 change）

## Capabilities

### New Capabilities

- `tg-symbol-halt-control`: TG 端可以查看 / 单个解除 root executor per-symbol halt；全局 resume 同步清理 in-memory 残留
- `tg-pnl-correction`: TG 端能手动给 pending external close 写 final PnL correction，幂等且需明确候选
- `tg-status-enhancement`: TG `/status` 增强可见性，覆盖 per-symbol halt + agent health 轻量信号

### Modified Capabilities

无（新建三个独立能力）。

## Impact

- **代码**：
  - `executor.py` (root)：新增 `clear_symbol_halt(symbol=None)` + `get_halted_symbols()` 公开方法
  - `agents/trading/executor.py:_handle_resume / system_command 'force_resume'`：同步清 `_halted_symbols`
  - `agents/trading/telegram_notifier.py`：新增 `/halts` / `/resume_symbol` / `/pnl` 命令；`/status` 增强
  - 可能新增 helper 文件用于 TG 命令参数解析（如 `utils/tg_commands.py`，待 design 决定）
- **测试**：
  - 新增 `test_tg_symbol_halt_control.py`（resume 清残留 + /halts + /resume_symbol）
  - 新增 `test_tg_pnl_correction.py`（候选解析 + 幂等 + 拒绝场景）
  - 新增 `test_tg_status_enhancement.py`（/status 内容包含 per-symbol halt + agent count）
- **运行时**：
  - 状态文件不变（halt_state.json / live_order_events.jsonl 复用现有契约）
  - 不影响开仓 / 平仓 / 风控决策路径（只动 TG 入口 + resume 清理）
- **依赖**：无新依赖
- **运维 SOP**：补 README/runbook 一行——XLM 类 symbol halt 现可用 `/halts` 查 / `/resume_symbol XLM` 单解
