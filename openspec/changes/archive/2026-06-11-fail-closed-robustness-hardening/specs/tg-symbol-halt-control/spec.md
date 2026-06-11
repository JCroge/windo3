## ADDED Requirements

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
