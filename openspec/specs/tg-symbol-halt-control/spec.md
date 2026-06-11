## ADDED Requirements

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
