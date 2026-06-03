## ADDED Requirements

### Requirement: Telegram critical_types SHALL include pullback_unfilled and paper_unfilled

The `critical_types` set in `agents/trading/telegram_notifier.py:_handle_risk_alert` SHALL include both `'pullback_unfilled'` (live) and `'paper_unfilled'` (paper). Risk alerts of these types SHALL produce user-visible Telegram messages, not be silently dropped.

#### Scenario: Live pullback_unfilled triggers TG message
- **WHEN** `executor.py:_execute_limit_order` cancels a limit with `no_fallback=True` and emits `_enqueue_drift_alert('pullback_unfilled', symbol, side, limit_price, timeout_sec)`
- **AND** the resulting `risk_alert` bus event reaches `_handle_risk_alert`
- **THEN** the alert SHALL pass the `critical_types` check
- **AND** a Telegram message SHALL be sent with prefix `[实盘]` (or equivalent) and include `symbol / side / limit_price / timeout_sec`

#### Scenario: Paper paper_unfilled triggers TG message
- **WHEN** `paper_executor._wait_paper_limit_fill` times out a pending limit with `limit_no_fallback=True` and publishes `risk_alert{type='paper_unfilled', source='paper_executor'}`
- **THEN** the alert SHALL pass the `critical_types` check
- **AND** a Telegram message SHALL be sent with prefix `[模拟]` (or equivalent) so users can distinguish from live events
- **AND** the message SHALL include `symbol / side / entry_zone / request_id`

#### Scenario: Other alert types unaffected
- **WHEN** any pre-existing critical alert type (e.g., `flash_move`, `max_drawdown`, `protection_failed`, `tp_invariant_breach`) is published
- **THEN** routing behavior SHALL remain identical to the pre-change baseline (no regression)

### Requirement: Risk alerts SHALL distinguish paper vs live by source field

Every `risk_alert` payload SHALL include a `source` field. Paper-originated alerts use `source='paper_executor'`; live-originated alerts use `source='executor'` (or whatever the existing live path emits). Telegram message formatting SHALL key off `source` to apply paper/live prefix and SHALL NOT collapse the two into one indistinguishable message.

#### Scenario: Paper alert has source=paper_executor
- **WHEN** any `paper_unfilled` alert is constructed by paper_executor
- **THEN** the payload SHALL include `source='paper_executor'`

#### Scenario: Live alert has live source
- **WHEN** any `pullback_unfilled` alert is constructed by live executor (root or agent layer)
- **THEN** the payload SHALL include `source='executor'` (or another non-paper identifier consistent with existing live alerts)

#### Scenario: TG message prefix reflects source
- **WHEN** `_handle_risk_alert` formats a message for `pullback_unfilled` or `paper_unfilled`
- **THEN** the message SHALL include a paper-vs-live distinguishing prefix derived from `source`
- **AND** absence of `source` SHALL default to live behavior with a warning log (fail-safe — never silently treat unknown source as paper)
