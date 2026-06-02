# Comet Design Handoff

- Change: tg-graceful-ops
- Phase: design
- Mode: compact
- Context hash: e95a4ffec72cc8947b4f0296548dd38012becc79063785beadd670031638545f

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/tg-graceful-ops/proposal.md

- Source: openspec/changes/tg-graceful-ops/proposal.md
- Lines: 1-53
- SHA256: 878ab8d169b94034170775c55f09a2afc6d004d95e3f639bdd7a528d3155a808

```md
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
```

## openspec/changes/tg-graceful-ops/design.md

- Source: openspec/changes/tg-graceful-ops/design.md
- Lines: 1-221
- SHA256: 70a7bc0ce6ca924d49df9f9c2d16650876a19175d1d132400e9e99253aed44c6

[TRUNCATED]

```md
## Context

Telegram 是本系统**唯一的人工运维入口**。当 root executor 触发 per-symbol halt（如 `_halt_symbol(reason='sl_replace_failed')`）时，会同时写全局 `halt_state.json`+触发 in-memory `_halted_symbols[symbol]`。运维通过 `/resume` 命令只能解全局 halt，per-symbol 残留在内存里直到进程重启——5/30 XLM 案例正是这个 bug 的真实暴露：8 小时静默拒单。

同期 `docs/to-do-list.md` 还有两个未闭环的 TG 优化（`/pnl` 行 58 + `/status` agent health 行 64），跟本次 bug 同属"运维可见性 + 控制"主题，本 change 一起闭环。

参考：
- `executor.py:900-915`：`_halt_symbol` / `is_symbol_halted` 实现
- `agents/trading/executor.py:376-407`：`_handle_resume` 现有逻辑
- `agents/trading/telegram_notifier.py:368-394`：命令分发
- `utils/live_ledger.py:392-491`：`apply_pnl_resolution` / `find_pending_external_closes` 已有 API

## Goals / Non-Goals

**Goals:**
- 修复 `/resume` 不清 per-symbol halt 残留 bug（彻底闭环 5/30 XLM 类问题）
- 提供 TG 端可见性：`/halts` 列锁的 symbol；`/status` 输出 per-symbol halt
- 提供 TG 端控制：`/resume_symbol <SYMBOL>` 单 symbol 解锁
- 落地 `/pnl <SYMBOL> <NET_PNL>` 手动 PnL correction（todo 58）
- `/status` 增加 agent health 轻量信号（todo 64 轻量）
- 三个新 capability 都有完整 test 覆盖；无回归

**Non-Goals:**
- 不引入 agent heartbeat / loop alive 重构（这是另一个 change）
- 不改 `_halted_symbols` 数据结构（保持 in-memory dict，仅暴露读/清 API）
- 不持久化 per-symbol halt 到磁盘（重启清零是合理行为，避免重启后旧 symbol 永远锁住）
- 不改 `LiveLedger.apply_pnl_resolution` 内部契约（只加 TG 入口）
- 不改全局 halt 语义（HaltState 的 halt/resume/force_resume 不动）
- 不在 TG 端实现 `/halt_symbol`（手动 halt 单 symbol）—— root executor 不需要这个能力

## Decisions

### Decision 1：`_halted_symbols` 通过公开方法清理，agent 不直接动私有字段

**问题**：agent 层 `_handle_resume` 怎么清 `_halted_symbols`？三个选项：

- A：agent 直接 `self.executor._halted_symbols.clear()` —— 私有字段穿透，封装破坏
- B：root executor 暴露 `clear_symbol_halt(symbol=None)` 公开方法，agent 调用
- C：root executor 监听 bus 上的 `system_command{cmd=resume}` —— 但 root 不是 agent，不在 bus 上

**选择 B**。`clear_symbol_halt(symbol=None)` 语义明确：传 symbol 清一个，不传清全部。新增配套 `get_halted_symbols() -> dict`（返回快照副本，不暴露内部字典引用）。

```python
# executor.py (root)
def clear_symbol_halt(self, symbol: Optional[str] = None) -> int:
    """清除 per-symbol halt 残留。

    Args:
        symbol: 指定 symbol 仅清该项；None 清全部。
    Returns:
        清掉的项数（用于审计日志）。
    """
    halted = getattr(self, '_halted_symbols', {})
    if symbol is None:
        n = len(halted)
        halted.clear()
        return n
    if symbol in halted:
        del halted[symbol]
        return 1
    return 0

def get_halted_symbols(self) -> Dict[str, dict]:
    """返回 _halted_symbols 的浅拷贝快照。"""
    return dict(getattr(self, '_halted_symbols', {}))
```

### Decision 2：`/resume` 路径修复——HaltState 解全局成功后调用 `clear_symbol_halt(None)`

**问题**：什么时机清？

`_handle_resume` 当前三种成功分支都设置 `self._trading_halted = False`：
1. payload 已带 `reconciliation_result.matched`（line 380-384）
2. 本地 reconciler 跑完无 blocking issues（line 386-395）
3. 无 reconciler，直接恢复（line 405-407）

三种都是"全局 halt 已 confirm 解除"的语义点。**在每个分支后都调用 `self.executor.clear_symbol_halt(None)`**。

`force_resume` 路径（agent 层 line 87-89）同样清理：force 是用户主动绕过对账，per-symbol halt 也是用户的责任，一并清掉符合"force"语义。

```

Full source: openspec/changes/tg-graceful-ops/design.md

## openspec/changes/tg-graceful-ops/tasks.md

- Source: openspec/changes/tg-graceful-ops/tasks.md
- Lines: 1-58
- SHA256: fddda7a8f84063d14e7b3ccd9cafe6e2afb3e604eb9185eacff8f6cb46573c2f

```md
## 1. F-TG-001 Resume 清 per-symbol halt（根因修复）

- [ ] 1.1 `executor.py` 新增 `clear_symbol_halt(symbol: Optional[str] = None) -> int` 公开方法（清单个 / 全部 / 缺失返回 0）
- [ ] 1.2 `executor.py` 新增 `get_halted_symbols() -> Dict[str, dict]` 返回浅拷贝
- [ ] 1.3 `clear_symbol_halt` 内部 logger.info 记录被清的 symbol 列表 + 来源（来自参数 source 字段或 logger 自带 caller info）
- [ ] 1.4 `agents/trading/executor.py:_handle_resume` 三条成功分支（line 380-384 / 386-395 / 405-407）末尾各加 `self.executor.clear_symbol_halt(None)` 调用
- [ ] 1.5 `agents/trading/executor.py:on_message system_command cmd=force_resume` 分支（line 86-89）加 `self.executor.clear_symbol_halt(None)` 调用
- [ ] 1.6 创建 `test_tg_symbol_halt_control.py`，添加 `TestClearSymbolHalt` 类（4 case：不传 / 指定 / 不存在 / 浅拷贝）
- [ ] 1.7 添加 `TestHandleResumeClearsSymbolHalt` 类（4 case：reconcile_ok 三分支 + force_resume）
- [ ] 1.8 跑 `python3 -m pytest -q test_tg_symbol_halt_control.py test_executor_upgrade.py` 确认无回归

## 2. F-TG-002 `/halts` + `/resume_symbol` + `/status` per-symbol halt 行

- [ ] 2.1 `agents/trading/telegram_notifier.py` 在 `_handle_command` handlers 字典加 `/halts` 与 `/resume_symbol`
- [ ] 2.2 实现 `_cmd_halts`：通过 bus 异步查询或直接读 `data/<ns_>agent_health.json` 的 `halted_symbols` 字段（design 决定走 bus query for 实时）
  - 选 bus query 路径：publish `system_command{cmd='query_halts'}`,MultiExecutor agent 响应 publish `halts_snapshot`,TG 订阅一次性 reply
  - 或更简单选 file 直读路径：直接读 health.json,接受 30s 延迟（决定后保留一种实现）
- [ ] 2.3 实现 `_cmd_resume_symbol`：解析 symbol 参数，publish `system_command{cmd='resume_symbol', symbol=...}`，MultiExecutor agent 响应调用 `executor.clear_symbol_halt(normalized_symbol)`
- [ ] 2.4 `agents/trading/executor.py:on_message` 增加 `cmd='resume_symbol'` 与 `cmd='query_halts'`（如选 bus query 路径）
- [ ] 2.5 `_cmd_status` 增加 "Per-symbol halt" 行，从 health.json 读 `halted_symbols`（无文件时降级文案）
- [ ] 2.6 添加 `TestCmdHalts` 类（3 case：空 / 一个 / 多个）
- [ ] 2.7 添加 `TestCmdResumeSymbol` 类（4 case：解锁存在 / 解锁不存在 / 不动全局 / 缺参提示）
- [ ] 2.8 添加 `TestStatusPerSymbolHalt` 类（3 case：0 / 1 / 多个截断）
- [ ] 2.9 跑 `python3 -m pytest -q test_tg_symbol_halt_control.py test_telegram_notifier.py 2>/dev/null` 确认无回归

## 3. F-TG-003 `/pnl` 手动 PnL correction

- [ ] 3.1 `agents/trading/telegram_notifier.py` 在 handlers 字典加 `/pnl`
- [ ] 3.2 实现 `_cmd_pnl`：解析 `<SYMBOL> <NET_PNL> [reason]`，参数错误回用法提示
- [ ] 3.3 调 `LiveLedger.find_pending_external_closes()`，按 `symbol == normalized_symbol` 过滤
- [ ] 3.4 0/1/多 三分支：1 → 构造 resolution 调 `apply_pnl_resolution`；其他 → 拒绝 + 友好消息（多候选时列出 event_id 与 pending 时间）
- [ ] 3.5 创建 `test_tg_pnl_correction.py`，添加 `TestCmdPnl` 类（6 case：1 候选成功 / 0 候选 / 多候选 / NET_PNL 解析失败 / 缺参 / chat_id 拒绝）
- [ ] 3.6 添加 `TestCmdPnlIdempotency` 类（2 case：重复提交相同 net_pnl 走 0 候选 / 不同 net_pnl 走 0 候选）
- [ ] 3.7 添加 `TestCmdPnlReasonField` 类（2 case：含 reason 写入 / 不含 reason 仍成功）
- [ ] 3.8 跑 `python3 -m pytest -q test_tg_pnl_correction.py test_live_ledger.py test_exchange_realized_pnl_resolver.py` 确认无回归

## 4. F-TG-004 `/status` agent health 轻量

- [ ] 4.1 `utils/state_paths.py` 增加 `agent_health: str` 字段（live=`data/agent_health.json`, testnet=`data/testnet_agent_health.json`），更新 `as_banner_lines` 与 `for_namespace`
- [ ] 4.2 `agents/trading/executor.py:MultiExecutor` 增加 `_write_agent_health()` 方法，schema 含 6 字段（ts / agents_registered / tasks_alive / tasks_failed / halted_symbols / bus_dlq_size）
- [ ] 4.3 在 `_run_reconciliation` 或 tick 周期调用 `_write_agent_health()`，确保 ≤30s 一次
- [ ] 4.4 `agents/orchestrator.py` 暴露 `get_agent_health() -> dict`（agents_registered / tasks_alive / tasks_failed），由 MultiExecutor 通过 self.config 或 module-level lookup 调用
  - 备选：由 orchestrator 周期性写 health.json，executor 仅追加 halted_symbols 与 bus_dlq_size 字段（避免单点全责）
- [ ] 4.5 `_cmd_status` 增加 "Agents:" 行 与 "Bus DLQ:" 行
- [ ] 4.6 创建 `test_tg_status_enhancement.py`，添加 `TestAgentHealthSchema` 类（3 case：写入完整 / namespace 派生 / 失败不阻塞）
- [ ] 4.7 添加 `TestStatusAgentHealth` 类（4 case：含 Agents 行 / 含 Bus DLQ 行 / 异常任务可见 / health 缺失 fallback）
- [ ] 4.8 跑 `python3 -m pytest -q test_tg_status_enhancement.py test_state_namespace.py` 确认无回归

## 5. 全量回归与验证收尾

- [ ] 5.1 字节码扫描 `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_tg_pycache python3 -m compileall -q .`
- [ ] 5.2 默认全量回归 `python3 -m pytest -q` 必须从 860 至少升至 ≥878（+18 case 新增最小预期）
- [ ] 5.3 network 分层回归 `python3 -m pytest -q -m network` 仍 4 通过
- [ ] 5.4 在本地 mock TG 跑一遍命令链：`/halts` → `/resume_symbol XLM` → `/halts` → `/pnl XLM 0.42` → `/status`，截图 / 日志记录到验收报告
- [ ] 5.5 撰写验收报告 `docs/audit_remediation_tg_graceful_ops_acceptance.md`（含验收命令、AC 列表、Mock TG run 摘要）
- [ ] 5.6 更新 `CLAUDE.md` 当前事实段：tg-graceful-ops 闭环 + 新基线
- [ ] 5.7 更新 `docs/to-do-list.md`：行 58 `/pnl` 与 行 64 `/status` agent health 移到"已关闭"
- [ ] 5.8 更新 `docs/runbook.md`（如有）：补充 `/halts` / `/resume_symbol` / `/pnl` 运维 SOP
```

## openspec/changes/tg-graceful-ops/specs/tg-pnl-correction/spec.md

- Source: openspec/changes/tg-graceful-ops/specs/tg-pnl-correction/spec.md
- Lines: 1-106
- SHA256: db5fb69a6558dcec02bebb73c6d6deaaa62853ebb2f9e1786c5eb5602672e6b6

[TRUNCATED]

```md
## ADDED Requirements

### Requirement: TG `/pnl <SYMBOL> <NET_PNL> [reason]` 必须为 pending external close 写 PnL correction

TG 命令 `/pnl` MUST 接收 `<SYMBOL>` 与 `<NET_PNL>`（USDT，可正可负 float），可选 `[reason]`。命令内部 MUST 调用 `LiveLedger.find_pending_external_closes()` 找该 symbol 的未 supersede pending 候选，候选恰好 1 条时 MUST 调用 `LiveLedger.apply_pnl_resolution()` 写 `external_close_correction` 事件，`source='manual_tg_review'`。

#### Scenario: 候选恰好 1 条时写 correction
- **WHEN** Ledger 有 1 条 XLM-USDT-SWAP pending external_close 未被 supersede
- **AND** TG 收到 `/pnl XLM 0.42`
- **THEN** MUST 调用 `apply_pnl_resolution`，resolution 含 `realized_pnl_net_usdt=0.42` 和 `pnl_status='final'`
- **AND** ledger 写入 `event_type='external_close_correction'` 事件，`source='manual_tg_review'`，`supersedes_event_id` 指向原 pending
- **AND** TG 回消息确认（含 symbol、net_pnl、新 event_id 或 supersede 信息）

#### Scenario: 候选 0 条时拒绝并提示
- **WHEN** Ledger 没有 XLM-USDT-SWAP 的 pending external_close
- **AND** TG 收到 `/pnl XLM 0.42`
- **THEN** MUST NOT 调用 `apply_pnl_resolution`
- **AND** TG 回消息表明 "未找到 XLM 的 pending external close"

#### Scenario: 候选多于 1 条时拒绝并提示用 /pnl_id
- **WHEN** Ledger 有 2 条 XLM-USDT-SWAP pending external_close 未被 supersede
- **AND** TG 收到 `/pnl XLM 0.42`
- **THEN** MUST NOT 调用 `apply_pnl_resolution`
- **AND** TG 回消息列出候选 event_id 列表
- **AND** 提示用户使用 `/pnl_id <event_id> <net_pnl>`（即便 `/pnl_id` 暂未实现，引导消息保留）

#### Scenario: NET_PNL 解析失败拒绝
- **WHEN** TG 收到 `/pnl XLM abc`
- **THEN** MUST NOT 查 ledger
- **AND** TG 回用法提示 `用法: /pnl <SYMBOL> <NET_PNL_USDT> [reason]`

#### Scenario: 缺参拒绝
- **WHEN** TG 收到 `/pnl XLM`（缺 NET_PNL）
- **THEN** MUST 回用法提示

#### Scenario: 仅授权 chat_id 可执行
- **WHEN** TG 收到 `/pnl XLM 0.42` 但 chat_id ≠ 配置的 `_chat_id`
- **THEN** 命令 MUST 静默忽略

### Requirement: TG `/pnl_id <event_id> <NET_PNL> [reason]` 必须按 event_id 精确匹配 pending

当 `/pnl <SYMBOL>` 因多候选拒绝时，`/pnl_id` MUST 提供按 event_id 精确匹配的回退命令。命令 MUST 调用 `LiveLedger.find_pending_external_closes()` 并按 `event_id == 参数 event_id` 过滤；命中恰好 1 条时写 correction，0 条时拒绝。

#### Scenario: event_id 命中恰好 1 条 pending 时写 correction
- **WHEN** Ledger 含 pending event_id="abc-123" 未 supersede
- **AND** TG 收到 `/pnl_id abc-123 0.42`
- **THEN** MUST 调用 `apply_pnl_resolution`，resolution 含 `realized_pnl_net_usdt=0.42`，`source='manual_tg_review'`
- **AND** 写入 correction event，`supersedes_event_id='abc-123'`
- **AND** TG 回消息确认（含原 event_id 与新 net_pnl）

#### Scenario: event_id 不存在或已 supersede 拒绝
- **WHEN** Ledger 不含活跃 pending event_id="abc-123"（已 supersede 或不存在）
- **AND** TG 收到 `/pnl_id abc-123 0.42`
- **THEN** MUST NOT 调用 `apply_pnl_resolution`
- **AND** TG 回消息表明 "未找到活跃的 pending event_id=abc-123"

#### Scenario: 缺参或 NET_PNL 解析失败
- **WHEN** TG 收到 `/pnl_id abc-123`（缺 NET_PNL）或 `/pnl_id abc-123 abc`
- **THEN** MUST NOT 查 ledger
- **AND** TG 回用法提示 `用法: /pnl_id <event_id> <NET_PNL_USDT> [reason]`

#### Scenario: 仅授权 chat_id 可执行
- **WHEN** TG 收到 `/pnl_id abc-123 0.42` 但 chat_id ≠ 配置的 `_chat_id`
- **THEN** 命令 MUST 静默忽略

### Requirement: TG `/pnl` 与 `/pnl_id` 必须共用候选解析 helper

`/pnl` 与 `/pnl_id` MUST 共享一个内部 helper（如 `_resolve_pending_for_pnl_correction(filter_fn)`），仅在候选过滤函数上不同（symbol-based vs event_id-based）。共享 helper MUST 实施一致的"候选恰好 1 条才写 correction、0 或多候选拒绝"语义。

#### Scenario: helper 接受 filter 函数
- **WHEN** helper 被调用，filter 仅返回 1 条 pending
- **THEN** MUST 进入 correction 写入分支

#### Scenario: helper 多候选时返回拒绝
- **WHEN** helper 被调用，filter 返回 ≥2 条 pending
- **THEN** MUST 返回拒绝结果（含候选 event_id 列表）
- **AND** 调用方根据上下文给出具体提示（`/pnl` 提示用 `/pnl_id`；`/pnl_id` 不会出现该分支因为 event_id 唯一）

### Requirement: TG `/pnl` 写入必须幂等

```

Full source: openspec/changes/tg-graceful-ops/specs/tg-pnl-correction/spec.md

## openspec/changes/tg-graceful-ops/specs/tg-status-enhancement/spec.md

- Source: openspec/changes/tg-graceful-ops/specs/tg-status-enhancement/spec.md
- Lines: 1-99
- SHA256: 2132fdd8415c1af14c74d8af16c71ae065990f103854e74b8814225c4754195e

[TRUNCATED]

```md
## ADDED Requirements

### Requirement: `/status` 命令必须显示 per-symbol halt 数量与 symbol 列表

`/status` 输出 MUST 包含一行表示当前 root executor `_halted_symbols` 的状态。无 halt 时输出 0；有 halt 时输出数量 + symbol 列表（最多 5 个，超出用 `…+N` 省略）。来源 MUST 是 `data/<ns_>agent_health.json`（30s 延迟可接受）。

#### Scenario: 无 per-symbol halt
- **WHEN** agent_health.json 中 `halted_symbols = {}`
- **AND** TG 收到 `/status`
- **THEN** 输出 MUST 含 "Per-symbol halt: 0" 或等价表述

#### Scenario: 有一个 per-symbol halt
- **WHEN** agent_health.json 中 `halted_symbols = {"XLM-USDT-SWAP": {...}}`
- **AND** TG 收到 `/status`
- **THEN** 输出 MUST 含 "Per-symbol halt: 1"
- **AND** MUST 含 "XLM"（symbol 简写或全名）

#### Scenario: 多个 halt 截断展示
- **WHEN** halted_symbols 含 7 个 symbol
- **AND** TG 收到 `/status`
- **THEN** 输出 MUST 显示前 5 个 + "…+2" 类似省略标记

#### Scenario: agent_health.json 缺失时 fallback
- **WHEN** `data/agent_health.json` 不存在
- **AND** TG 收到 `/status`
- **THEN** Per-symbol halt 行 MUST 输出降级文案（如 "Per-symbol halt: ?（health 文件缺失）"）
- **AND** 其他 status 字段不受影响

### Requirement: `/status` 命令必须显示 agent 注册数与任务存活数

`/status` 输出 MUST 包含一行表示已注册 agent 数 + 任务存活数 + 异常任务数。来源同样为 `agent_health.json`。

#### Scenario: 健康状态正常
- **WHEN** agent_health.json 中 `agents_registered=17, tasks_alive=17, tasks_failed=0`
- **AND** TG 收到 `/status`
- **THEN** 输出 MUST 含 "Agents: 17 注册 / 17 任务存活 / 0 异常" 或等价表述

#### Scenario: 有异常任务
- **WHEN** agent_health.json `tasks_failed=2`
- **AND** TG 收到 `/status`
- **THEN** 输出 MUST 含 "2" 异常计数（应明显可见，便于运维察觉）

### Requirement: `/status` 命令必须显示 bus DLQ 计数

`/status` 输出 MUST 包含一行 bus DLQ size。来源同样为 `agent_health.json` 的 `bus_dlq_size` 字段。无 DLQ attribute 时输出 0。

#### Scenario: DLQ 为空
- **WHEN** agent_health.json `bus_dlq_size=0`
- **THEN** 输出 MUST 含 "Bus DLQ: 0"

#### Scenario: DLQ 有积压
- **WHEN** agent_health.json `bus_dlq_size=3`
- **THEN** 输出 MUST 含 "Bus DLQ: 3"

### Requirement: Orchestrator 必须周期性写 agent_health.json

`agents/orchestrator.py` 的 Orchestrator MUST 在已有 tick 周期内（≤30s）写一次 `data/<ns_>agent_health.json`，schema 含 `ts / agents_registered / tasks_alive / tasks_failed / halted_symbols / bus_dlq_size`。

数据来源：
- `agents_registered / tasks_alive / tasks_failed`：Orchestrator 直读 `_tasks` / `_research_agents` / `_trading_agents`
- `halted_symbols`：MultiExecutor agent 周期性 publish `halts_snapshot{halted_symbols=...}` 总线事件，Orchestrator 订阅并缓存最新值
- `bus_dlq_size`：Orchestrator 直读 `MessageBus.get_instance()._dlq` 长度（缺失 attribute 时为 0）
- `ts`：Orchestrator 写入时戳

MultiExecutor MUST NOT 直接写 `agent_health.json`（避免双写 race）。

#### Scenario: 文件按 namespace 派生
- **WHEN** `STATE_NAMESPACE=testnet`
- **AND** Orchestrator 写 health
- **THEN** 文件路径 MUST 是 `data/testnet_agent_health.json`（与 `state_paths.get_state_paths()` 一致）

#### Scenario: 写入 schema 完整
- **WHEN** health 被写
- **THEN** JSON MUST 含全部 6 字段（ts, agents_registered, tasks_alive, tasks_failed, halted_symbols, bus_dlq_size）
- **AND** `halted_symbols` MUST 来自 Orchestrator 缓存的最新 halts_snapshot 事件
- **AND** `bus_dlq_size` MUST 来自 `MessageBus.get_instance()._dlq` 长度（缺失 attribute 时 0）

#### Scenario: 写入失败不阻塞主循环
- **WHEN** health 写入因磁盘错误失败
- **THEN** 异常 MUST 被吞掉并 logger.warning
```

Full source: openspec/changes/tg-graceful-ops/specs/tg-status-enhancement/spec.md

## openspec/changes/tg-graceful-ops/specs/tg-symbol-halt-control/spec.md

- Source: openspec/changes/tg-graceful-ops/specs/tg-symbol-halt-control/spec.md
- Lines: 1-123
- SHA256: 10b1da4219f245a394e5dead695a0b432b12add559bf6f4e81d9aafa3d51b216

[TRUNCATED]

```md
## ADDED Requirements

### Requirement: 全局 resume 必须同步清理 root executor 的 per-symbol halt

`agents/trading/executor.py:_handle_resume` 在三条成功分支（payload-confirmed reconciliation matched / 本地 reconciler 通过 / 无 reconciler 直接恢复）任一成功后，MUST 调用 `self.executor.clear_symbol_halt(None)` 清空 root executor 的 in-memory `_halted_symbols`。同样地，`system_command{cmd='force_resume'}` 路径 MUST 同步清理。这避免了 5/30 XLM 案例中 8 小时静默拒单的 bug。

#### Scenario: 全局 resume 成功后 per-symbol halt 全部清除
- **WHEN** `_halted_symbols` 含 `{"XLM-USDT-SWAP": {...}}`
- **AND** `_handle_resume` 任一成功分支被触发（reconcile_ok=True）
- **THEN** `executor.get_halted_symbols()` MUST 返回空字典
- **AND** 后续 `_execute_decision` 对 XLM-USDT-SWAP 不再因 `is_symbol_halted` 拒绝

#### Scenario: 对账失败时 per-symbol halt 不清
- **WHEN** `_handle_resume` 走本地 reconciler 路径
- **AND** reconciler 返回 blocking_issues
- **THEN** `executor.get_halted_symbols()` 保持原状（halt 维持）

#### Scenario: force_resume 同样清理 per-symbol halt 并打 audit warning
- **WHEN** `system_command{cmd='force_resume', source='telegram'}` 被处理
- **AND** `_halted_symbols` 含 N≥1 个 symbol
- **THEN** `_halted_symbols` MUST 被清空
- **AND** logger.warning MUST 输出列出被清的 symbol 列表与各自 reason（如 "force_resume cleared 1 per-symbol halt: [XLM-USDT-SWAP (sl_replace_failed)]"）
- **AND** Telegram MUST 回显被清的 symbol 列表（提示用户确认根因已排除）

#### Scenario: force_resume 在 _halted_symbols 为空时不打 audit warning
- **WHEN** `system_command{cmd='force_resume'}` 被处理
- **AND** `_halted_symbols` 为空
- **THEN** logger.warning MUST NOT 输出"cleared per-symbol halt"内容
- **AND** Telegram 回显不包含被清 symbol 列表

### Requirement: ContractExecutor 必须暴露 clear_symbol_halt 与 get_halted_symbols 公开 API

`executor.py` (root) MUST 提供两个公开方法：`clear_symbol_halt(symbol: Optional[str]=None) -> int` 与 `get_halted_symbols() -> Dict[str, dict]`，用于外部按 symbol 清除/查询 in-memory `_halted_symbols`。Agent 层 MUST NOT 直接访问 `_halted_symbols` 私有字段。

#### Scenario: clear_symbol_halt 不传参清全部
- **WHEN** `_halted_symbols = {"A": {...}, "B": {...}}`
- **AND** 调用 `clear_symbol_halt(None)` 或 `clear_symbol_halt()`
- **THEN** 返回 2
- **AND** `_halted_symbols` 为空

#### Scenario: clear_symbol_halt 指定 symbol 仅清该项
- **WHEN** `_halted_symbols = {"A": {...}, "B": {...}}`
- **AND** 调用 `clear_symbol_halt("A")`
- **THEN** 返回 1
- **AND** `_halted_symbols` 只剩 `{"B": {...}}`

#### Scenario: clear_symbol_halt 不存在的 symbol 返回 0
- **WHEN** `_halted_symbols` 不含 "X"
- **AND** 调用 `clear_symbol_halt("X")`
- **THEN** 返回 0
- **AND** 不抛异常

#### Scenario: get_halted_symbols 返回浅拷贝
- **WHEN** `_halted_symbols = {"A": {"reason": "x"}}`
- **AND** snapshot = `get_halted_symbols()`
- **AND** snapshot["A"]["reason"] = "modified"
- **THEN** 内部 `_halted_symbols["A"]["reason"]` 仍是 "x"（顶层 dict 是浅拷贝，但调用方不应修改字段值；这里只断言顶层 add/del 不影响内部）

### Requirement: TG `/halts` 命令必须列出所有 per-symbol halt

`/halts` 命令 MUST 通过 `executor.get_halted_symbols()` 读取当前 per-symbol halt 字典，按 symbol / reason / halted_at 格式化输出到 Telegram。无 symbol 被锁时输出明确的"无 halt"消息。

#### Scenario: 有 halt 时输出每条 reason 与时间
- **WHEN** `_halted_symbols = {"XLM-USDT-SWAP": {"reason": "sl_replace_failed", "halted_at": <8h ago ts>}}`
- **AND** TG 收到 `/halts`
- **THEN** 回消息 MUST 含 "XLM-USDT-SWAP"
- **AND** MUST 含 "sl_replace_failed"
- **AND** MUST 含表示已经过去 8 小时的相对时间字串

#### Scenario: 无 halt 时输出明确消息
- **WHEN** `_halted_symbols = {}`
- **AND** TG 收到 `/halts`
- **THEN** 回消息 MUST 表示无 per-symbol halt（如"✅ 无 per-symbol halt"）

#### Scenario: 仅授权 chat_id 可执行
- **WHEN** TG 收到 `/halts` 但 chat_id ≠ 配置的 `_chat_id`
- **THEN** 命令 MUST 静默忽略（与现有命令权限一致）

### Requirement: TG `/resume_symbol <SYMBOL>` 命令必须只解一个 symbol 的 halt

```

Full source: openspec/changes/tg-graceful-ops/specs/tg-symbol-halt-control/spec.md

