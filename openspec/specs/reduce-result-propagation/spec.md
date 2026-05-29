## ADDED Requirements

### Requirement: Agent reduce 路径必须按 reduce_ok / ok 分支处理终态

执行层 Agent 在调用 `executor.reduce_position()` 后，MUST 基于返回 dict 中的 `reduce_ok` / `ok` / `protective_update_state` 字段决定 `execution_result.v2` 的 `status`，并且 MUST NOT 用 truthy 判断把失败结果广播为 `risk_reduced`。本要求 SHALL 覆盖三条路径：PositionAnalyst 的部分平仓 (`source='position_analyst' && action='close' && size_pct<1.0`)、风控减仓 (`portfolio_exposure` / `correlation_risk`)、partial TP 锁利 (`partial_tp_1` / `partial_tp_2`)。

#### Scenario: pre-trade 失败 (sl_cancel_failed / sl_restore_failed) 必须广播 rejected
- **WHEN** `reduce_position()` 返回 `{reduce_ok: False, reason: "sl_cancel_failed"}` 或 `{reduce_ok: False, reason: "sl_restore_failed"}`（reduce 单还没下到交易所）
- **THEN** Agent MUST publish `status="rejected"`，MUST NOT 发 `risk_reduced` 或 `reduce_failed`
- **AND** payload MUST 含 `reason`（来自 `result.reason`）
- **AND** payload MUST NOT 含 `reduce_pct` 或 `reduce_pct=0`

#### Scenario: 交易所 reject (reduce_rejected) 必须广播 reduce_failed
- **WHEN** `reduce_position()` 返回 `{reduce_ok: False, reason: "reduce_rejected"}`（reduce 单已尝试下交易所但被拒）
- **THEN** Agent MUST publish `status="reduce_failed"`，MUST NOT 发 `risk_reduced` 或 `rejected`
- **AND** payload MUST 含 `reason="reduce_rejected"`
- **AND** payload MUST NOT 含 `reduce_pct` 或 `reduce_pct=0`

#### Scenario: dust_closed 视为平仓终态而非减仓
- **WHEN** `reduce_position()` 返回 `{reduce_ok: True, protective_update_state: "dust_closed", protection_state: "closed"}`（剩余仓位过小被 root executor 删除）
- **THEN** Agent MUST publish `status="executed"` 且 `action="close"`（不是 risk_reduced，不是 reduce）
- **AND** payload MUST 含 `protection_state="closed"`
- **AND** 走 close 文案路径而非 reduce 文案路径

#### Scenario: reduce_ok=True 但 ok=False 必须标记 protection_failed
- **WHEN** `reduce_position()` 返回 `{reduce_ok: True, ok: False, protective_update_state: "replace_failed"}`（reduce 已成交但 residual SL 重挂失败）
- **THEN** Agent MUST publish `status="risk_reduced"` 且 payload 含 `protection_failed=True`
- **AND** payload MUST 含 `protection_state="unknown"`
- **AND** payload `reduce_pct` MUST 使用 `result.actual_reduce_amount` 折算的 actual pct，MUST NOT 用请求 pct

#### Scenario: ok=True 走干净 risk_reduced
- **WHEN** `reduce_position()` 返回 `{reduce_ok: True, ok: True, protective_update_state: "protected"}`
- **THEN** Agent publish `status="risk_reduced"`，`protection_failed` 必须不存在或 False
- **AND** `protection_state="protected"`

#### Scenario: 三路径必须共用同一分流函数
- **WHEN** PositionAnalyst 部分平、portfolio_exposure 风控减仓、partial_tp_1 任一路径调用 `reduce_position()`
- **THEN** MUST 经由同一个 `_classify_reduce_outcome(result, requested_pct)` helper 派生 `status` / `reason` / `actual_reduce_pct` / `protection_failed` / `protection_state` / `action_override`，MUST NOT 在三处各自写 if/else 分支

#### Scenario: result is None 必须广播 rejected
- **WHEN** `reduce_position()` 抛异常被 catch 或返回 `None`
- **THEN** Agent MUST publish `status="rejected"` 且 `reason="executor_returned_none"`

### Requirement: PortfolioRiskGuard 必须按实际成交结果调整本地敞口

PortfolioRiskGuard 监听 `execution_result.v2` 时，对 reduce 类终态的本地 `_positions[symbol]['amount_usdt']` 调整 MUST 基于 Agent 透传的实际百分比，而不是请求百分比；失败/拒绝终态 MUST NOT 缩敞口；保护单失败 SHALL 额外发 risk_alert。

#### Scenario: rejected / reduce_failed 不缩敞口
- **WHEN** 收到 `status ∈ {"rejected", "reduce_failed"}` 的 execution_result（含 reduce 类 action）
- **THEN** RiskGuard MUST NOT 修改 `_positions[symbol]['amount_usdt']`

#### Scenario: dust_closed 移除 symbol 而非缩敞口
- **WHEN** 收到 `status="executed"` 且 `action="close"` 且来源是 reduce 路径（dust_closed）
- **THEN** RiskGuard MUST 走现有 close 分支，从 `_positions` 移除 symbol（同 force_closed 处理）
- **AND** MUST NOT 在已移除 symbol 之后再尝试缩 `amount_usdt`

#### Scenario: risk_reduced 按 actual_reduce_pct 缩敞口
- **WHEN** 收到 `status="risk_reduced"`，payload `reduce_pct=R`
- **THEN** `_positions[symbol]['amount_usdt'] *= (1 - R)`，R MUST 取自 payload 的 `reduce_pct`（已是 actual pct）

#### Scenario: protection_failed 触发 protection_failed risk_alert
- **WHEN** 收到 `status="risk_reduced"` 且 `protection_failed=True`
- **THEN** RiskGuard 仍按 actual pct 缩敞口（reduce 已成交）
- **AND** 必须 publish `risk_alert` 且 `type="protection_failed"`，含 `symbol` / `protective_update_state` / `request_id`

### Requirement: TelegramNotifier 必须按 protective_update_state 分流减仓文案

Telegram 推送对 reduce 类终态 MUST 区分干净减仓与保护单异常，并且 MUST NOT 在保护单异常时仅发"✂️ 减仓"。

#### Scenario: 干净减仓走简短文案
- **WHEN** 收到 `status="risk_reduced"` 且 `protection_failed` 为 False/缺失
- **THEN** 发送形如 `✂️ 减仓 <symbol> <pct>%` 的简短消息

#### Scenario: protection_failed 走故障告警文案
- **WHEN** 收到 `status="risk_reduced"` 且 `protection_failed=True`
- **THEN** 发送故障文案，必须包含 `protective_update_state` 字段值（如 `replace_failed` / `restore_failed` / `cancel_failed`）和 `protection_state="unknown"` 字样

#### Scenario: dust_closed 走平仓文案而非减仓文案
- **WHEN** 收到 `status="executed"` 且 `action="close"` 且 payload 标识来源是 reduce 路径（如携带 `protective_update_state="dust_closed"` 或 `reduce_origin=True`）
- **THEN** 走现有平仓文案分支（含 PnL）
- **AND** MUST NOT 同时发"减仓"文案

#### Scenario: rejected / reduce_failed 不发减仓文案
- **WHEN** 收到 `status ∈ {"rejected", "reduce_failed"}`，action 为 reduce 类
- **THEN** MUST NOT 发送任何带"减仓"字样的消息（rejected 默认文案分支不属于减仓推送）
