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

`/resume_symbol <SYMBOL>` MUST 通过 bus publish `system_command{cmd='resume_symbol', symbol=<normalized>, source='telegram'}`，由 MultiExecutor agent 接收并调用 `self.executor.clear_symbol_halt(normalized_symbol)`。MUST NOT 由 TelegramNotifier agent 直接持有 root executor 引用或直接修改其内存（保持 agent 隔离）。MUST NOT 触碰全局 `halt_state.json` / `HaltState`。symbol 参数 MUST 经 `executor._normalize_symbol` 归一化以容忍 `XLM` / `XLM-USDT` / `XLM-USDT-SWAP` 等输入形态。

#### Scenario: 解锁存在的 symbol
- **WHEN** `_halted_symbols = {"XLM-USDT-SWAP": {...}}`
- **AND** TG 收到 `/resume_symbol XLM`
- **THEN** TG MUST publish `system_command{cmd='resume_symbol', symbol='XLM-USDT-SWAP', source='telegram'}`
- **AND** MultiExecutor agent 接收后 MUST 调用 `self.executor.clear_symbol_halt('XLM-USDT-SWAP')`
- **AND** `_halted_symbols` 清空该项
- **AND** TG 回消息确认解除（含 symbol）

#### Scenario: TG agent 必须不持有 root executor 引用
- **WHEN** TelegramNotifier 实例化
- **THEN** 实例 MUST NOT 含直接指向 ContractExecutor 实例的属性
- **AND** `/resume_symbol` 必须经由 bus system_command 路由

#### Scenario: 解锁不存在的 symbol 返回友好消息
- **WHEN** `_halted_symbols = {}`
- **AND** TG 收到 `/resume_symbol XLM`
- **THEN** 回消息表明 "<SYMBOL> 没有被 halt"
- **AND** 不抛异常，不修改全局 halt 状态

#### Scenario: /resume_symbol 不动全局 halt
- **WHEN** `halt_state.json` 显示全局 halted=true
- **AND** TG 收到 `/resume_symbol XLM`
- **THEN** 全局 `HaltState.halted` MUST 仍为 true（仅清 per-symbol halt）

#### Scenario: 缺参或多余参数提示用法
- **WHEN** TG 收到 `/resume_symbol`（无参数）
- **THEN** 回消息提示用法 `用法: /resume_symbol <SYMBOL>`

### Requirement: 清理操作必须记 audit 日志

`clear_symbol_halt` 在清掉 ≥1 项时 MUST 通过 root executor 的 logger 记 INFO 级日志，含被清的 symbol 列表与触发源（来自 `_handle_resume` / TG `/resume_symbol`）。便于事后审计 5/30 XLM 类问题。

#### Scenario: 清单条 symbol 时记录调用方
- **WHEN** TG `/resume_symbol XLM` 调用清理
- **THEN** logger.info 输出 MUST 含 "XLM-USDT-SWAP"
- **AND** MUST 含触发上下文（如 "telegram" 字串）

#### Scenario: 全局 resume 清空时记录数量与列表
- **WHEN** `_handle_resume` 清掉 3 个 symbol
- **THEN** logger.info 输出 MUST 含数量 "3"
- **AND** MUST 列出所有 3 个 symbol

### Requirement: resume_symbol 全局 halt 仍在时必须诚实回显
系统 SHALL 在 `/resume_symbol <SYMBOL>` 清掉 per-symbol halt 后，若全局
`HaltState.halted` 仍为 true，使 TG 回显 MUST 明确告知"per-symbol halt 已清，但全局仍 halt，需 `/resume`
（带对账）才能恢复开新仓"，避免运维误以为已恢复交易（恢复语义陷阱）。该回显 MUST
由 TG / MultiExecutor agent 层基于其持有的 `halt_state` 判断生成。MUST NOT 改变
`clear_symbol_halt` 的返回类型（保持返回 int 项数，兼容既有调用方与测试）。MUST NOT
清除、绕过或修改全局 `HaltState`（深度 halt 语义重构属独立后续 change，本需求只补
回显诚实性）。

#### Scenario: 清 per-symbol 但全局仍 halt
- **WHEN** `_halted_symbols={"XLM-USDT-SWAP":{...}}` 且全局 `HaltState.halted==true`
- **AND** TG 收到 `/resume_symbol XLM`
- **THEN** per-symbol halt 被清（`clear_symbol_halt` 返回 1）
- **AND** 全局 `HaltState.halted` MUST 仍为 true
- **AND** TG 回显 MUST 含"全局仍 halt"提示与 `/resume` 指引

#### Scenario: 清 per-symbol 且全局未 halt 不附加提示
- **WHEN** `_halted_symbols={"XLM-USDT-SWAP":{...}}` 且全局 `HaltState.halted==false`
- **AND** TG 收到 `/resume_symbol XLM`
- **THEN** TG 回显正常确认解除该 symbol，MUST NOT 附加全局 halt 提示

#### Scenario: clear_symbol_halt 返回类型不变
- **WHEN** 任意 `/resume_symbol` 或 `_handle_resume` 调用 `clear_symbol_halt`
- **THEN** `clear_symbol_halt` 返回值 MUST 仍为 int（被清项数），既有调用方与测试不破

### Requirement: 全局 resume 非 matched 对账结果时必须 fail-closed 维持熔断
系统 SHALL 保证：`MultiExecutor._handle_resume` 处理常规 `resume`（非 force_resume）时，唯一允许
恢复交易的条件 MUST 是 `payload.reconciliation_result.status == 'matched'`。任何非 matched 的情形——包括
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
