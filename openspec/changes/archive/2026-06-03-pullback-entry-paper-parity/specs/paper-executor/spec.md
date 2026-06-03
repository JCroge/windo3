## ADDED Requirements

### Requirement: Paper Executor SHALL respect plan order_type when opening positions

When `paper_executor._open_paper(symbol, action, plan, decision)` receives a `plan` with `order_type='limit'`, the paper account SHALL NOT immediately fill at `latest_price`. Instead, the position SHALL be queued as a pending limit and resolved by `_wait_paper_limit_fill` based on `entry_zone`, `limit_timeout_sec` and `limit_no_fallback` fields. When `plan.order_type` is missing, `'market'`, or any other value, the existing immediate-fill behavior SHALL be preserved (fail-safe default).

#### Scenario: Limit plan defers to wait_paper_limit_fill
- **WHEN** `_open_paper` receives `plan={order_type:'limit', entry_zone:[low, high], limit_timeout_sec:1800, limit_no_fallback:True, ...}` and no existing position
- **THEN** the position SHALL NOT appear in `_positions[symbol]` immediately
- **AND** an entry SHALL be added to `_pending_limits[symbol]` with `created_at`, `plan` snapshot, `decision` snapshot, `entry_method='limit_pending'`
- **AND** no `paper_trades.jsonl` record SHALL be appended yet

#### Scenario: Market plan keeps legacy immediate fill
- **WHEN** `_open_paper` receives `plan={order_type:'market', ...}` or `plan` without `order_type` field
- **THEN** the position SHALL be created in `_positions[symbol]` at `latest_price` in the same call
- **AND** the position record SHALL include `entry_method='market'`

#### Scenario: Limit plan with missing entry_zone falls back to market
- **WHEN** `_open_paper` receives `plan={order_type:'limit'}` but `entry_zone` is `[]`, `[0, 0]`, or absent
- **THEN** the system SHALL log a warning and fall back to market behavior with `entry_method='market'` (fail-safe — never silently drop a trade_decision)

### Requirement: Paper Executor SHALL detect entry_zone hits via price_tick stream

`_wait_paper_limit_fill` SHALL evaluate pending limits whenever a new `price_tick` arrives for the symbol. A pending limit is considered filled when the tick price falls within `[min(entry_zone), max(entry_zone)]` inclusive, even momentarily. Fill price SHALL be the midpoint of `entry_zone`. The check SHALL also run on a periodic cleanup loop to handle timeout regardless of tick activity.

#### Scenario: Tick price inside entry_zone triggers fill
- **WHEN** a pending limit exists with `entry_zone=[0.4043, 0.4047]`
- **AND** a `price_tick` arrives with `price=0.4044`
- **THEN** the position SHALL be added to `_positions[symbol]` with `entry_price=0.4045` (midpoint), `entry_method='limit_filled'`
- **AND** the pending entry SHALL be removed from `_pending_limits`
- **AND** a `paper_trades.jsonl` open event SHALL NOT be written (only on close, consistent with current behavior)

#### Scenario: Tick price crosses entry_zone instantaneously
- **WHEN** a pending limit exists with `entry_zone=[0.4043, 0.4047]`
- **AND** two consecutive ticks arrive at 0.4042 then 0.4060 (touching 0.4045 only momentarily between)
- **AND** the system observed at least one tick within `[0.4043, 0.4047]`
- **THEN** the position SHALL be filled at midpoint 0.4045 with `entry_method='limit_filled'`

#### Scenario: Tick price never enters entry_zone
- **WHEN** a pending limit exists with `entry_zone=[0.4043, 0.4047]`
- **AND** all ticks during `limit_timeout_sec` window are outside the zone
- **THEN** the position SHALL NOT be filled by tick-driven path
- **AND** at the timeout the cleanup loop SHALL trigger the timeout branch (next requirement)

### Requirement: Paper Executor SHALL handle limit timeout per limit_no_fallback

When `_wait_paper_limit_fill` reaches `created_at + limit_timeout_sec` without entry_zone hit, behavior SHALL match `executor.py:_execute_limit_order` semantics:
- If `plan.limit_no_fallback == True` (pullback policy default): the pending limit SHALL be removed without opening a position. A rejection record SHALL be appended to `_rejected_log` and a `risk_alert` SHALL be published with `type='paper_unfilled'`.
- If `plan.limit_no_fallback == False`: the pending limit SHALL be filled at the latest tick price as a market fallback. The position SHALL be created with `entry_method='market'` (fallback path collapses into market for downstream simplicity) and a separate `paper_limit_fallback_used` log entry.

#### Scenario: Pullback policy timeout (no_fallback=True)
- **WHEN** a pending limit times out with `limit_no_fallback=True`
- **THEN** no `_positions[symbol]` entry SHALL be created
- **AND** `_rejected_log` SHALL receive a record with `reason='paper_unfilled'`, `request_id` from decision, and `entry_method='limit_unfilled'`
- **AND** a bus event SHALL be published: `topic='risk_alert'`, `payload={type:'paper_unfilled', source:'paper_executor', symbol, side, entry_zone, request_id}`

#### Scenario: Non-pullback limit timeout (no_fallback=False)
- **WHEN** a pending limit times out with `limit_no_fallback=False`
- **AND** the latest tick price is available
- **THEN** the position SHALL be created in `_positions[symbol]` at the latest tick price with `entry_method='market'`
- **AND** an info log SHALL note `paper_limit_fallback_used` for traceability
- **AND** no `paper_unfilled` risk_alert SHALL be published (this is success, not rejection)

#### Scenario: Non-pullback limit timeout with no tick available
- **WHEN** a pending limit times out with `limit_no_fallback=False` but `_latest_price[symbol]` is missing
- **THEN** the pending limit SHALL be removed and a `paper_unfilled` rejection SHALL be recorded with `reason='paper_unfilled_no_tick'` (fail-safe — never use a stale entry_zone midpoint as fallback)

### Requirement: Paper account records SHALL include entry_method field

Every paper position record (in `_positions`, in `paper_positions.json` after persistence, and in `paper_trades.jsonl` close events) SHALL include an `entry_method` field with one of the values: `'market'`, `'limit_filled'`, `'limit_unfilled'`. Records produced before this change SHALL be treated as `entry_method='market'` by any downstream reader (fail-safe default).

#### Scenario: Market open writes entry_method=market
- **WHEN** a position is opened via the immediate-fill path
- **THEN** `_positions[symbol]['entry_method']` SHALL equal `'market'`
- **AND** the eventual `paper_trades.jsonl` close record SHALL include `entry_method='market'`

#### Scenario: Limit fill writes entry_method=limit_filled
- **WHEN** a position is opened via tick-triggered limit fill
- **THEN** `_positions[symbol]['entry_method']` SHALL equal `'limit_filled'`
- **AND** the eventual `paper_trades.jsonl` close record SHALL include `entry_method='limit_filled'`

#### Scenario: Limit unfilled rejection writes entry_method=limit_unfilled in rejected log
- **WHEN** a pending limit times out with `limit_no_fallback=True`
- **THEN** the `_rejected_log` entry SHALL include `entry_method='limit_unfilled'` (rejection records do not write to `paper_trades.jsonl`)

#### Scenario: Legacy record without entry_method
- **WHEN** a downstream reader (e.g., future paper reviewer) loads a `paper_trades.jsonl` record produced before this change
- **THEN** the absence of `entry_method` SHALL be treated as `entry_method='market'` and processing SHALL continue normally

### Requirement: Paper Executor SHALL prevent duplicate opens during pending limit window

Once a `_pending_limits[symbol]` entry exists, subsequent `trade_decision` events for the same symbol with `action ∈ {open_long, open_short}` SHALL be treated as duplicates and ignored (logged but not stacked). The same protection that exists for `_positions[symbol]` SHALL extend to pending limits.

#### Scenario: Duplicate open_short during pending limit
- **WHEN** `_pending_limits['WLD-USDT']` already holds a pending limit
- **AND** a new `trade_decision{action:'open_short', symbol:'WLD-USDT'}` arrives
- **THEN** the new decision SHALL be skipped with an info log `[PAPER] WLD-USDT open_short 跳过：已有 pending limit`
- **AND** no second pending entry SHALL be added

#### Scenario: Close arrives during pending limit
- **WHEN** `_pending_limits['WLD-USDT']` exists and a `trade_decision{action:'close'}` arrives
- **THEN** the pending limit SHALL be cancelled (removed from `_pending_limits`)
- **AND** no position close SHALL be attempted (since no position exists yet)
- **AND** an info log SHALL note `[PAPER] WLD-USDT close cancelled pending limit`

### Requirement: Pending limits SHALL NOT persist across paper executor restarts

`_pending_limits` is in-memory only. On paper executor startup, no pending limit SHALL be reconstructed from disk. This matches CLAUDE.md fail-closed posture and avoids ghost limits triggering after long downtime.

#### Scenario: Restart drops pending limits
- **WHEN** paper executor shuts down with a non-empty `_pending_limits`
- **AND** the executor restarts and loads `paper_positions.json`
- **THEN** `_pending_limits` SHALL be empty
- **AND** no `paper_unfilled` risk_alert SHALL be published for the dropped limits (silent drop is acceptable; the original `trade_decision` is the source of truth and replays via signal flow)

#### Scenario: save_state does not serialize pending limits
- **WHEN** `_save_state` is invoked with a non-empty `_pending_limits`
- **THEN** the written `paper_positions.json` SHALL NOT contain any `pending_limits` field or equivalent representation
- **AND** no other persistence file SHALL contain pending limit state (the in-memory dict is the only source of truth during runtime)

### Requirement: Paper Executor SHALL gate market fallback by tick freshness

When `_wait_paper_limit_fill` reaches timeout with `limit_no_fallback=False`, before using `_latest_price[symbol]` as the fallback fill price, the system SHALL verify the latest tick is fresh. The freshness threshold is configurable via `paper_limit_tick_staleness_sec` in the agent config (default 60 seconds). When the latest tick is stale (or absent), the system SHALL fall through to the `paper_unfilled_no_tick` rejection path instead of filling at a stale price.

#### Scenario: Stale tick blocks fallback
- **WHEN** a pending limit times out with `limit_no_fallback=False`
- **AND** `_latest_price[symbol]` exists but the last tick was received more than `paper_limit_tick_staleness_sec` ago
- **THEN** the position SHALL NOT be created via market fallback
- **AND** a `paper_unfilled_no_tick` rejection SHALL be recorded with `entry_method='limit_unfilled'`
- **AND** a `risk_alert{type='paper_unfilled', source='paper_executor'}` SHALL be published with a `subtype='no_tick'` or `reason='paper_unfilled_no_tick'` field for downstream filtering

#### Scenario: Fresh tick allows fallback
- **WHEN** a pending limit times out with `limit_no_fallback=False`
- **AND** the last tick was received within `paper_limit_tick_staleness_sec` seconds
- **THEN** the position SHALL be created at `_latest_price[symbol]` with `entry_method='market'` (fallback path)

#### Scenario: Custom staleness threshold honored
- **WHEN** the paper executor is initialized with `config={paper_limit_tick_staleness_sec: 120, ...}`
- **THEN** `self._tick_staleness_sec` SHALL equal 120
- **AND** subsequent fallback gating SHALL use 120s instead of the default

### Requirement: Cleanup loop SHALL run at least every 30 seconds

`_pending_limits` timeout detection runs in the periodic `tick()` loop. When `_pending_limits` is non-empty, the paper executor SHALL ensure timeout-eligible entries are processed within 30 seconds of their `deadline`. The total detection error (between actual timeout and processing) SHALL NOT exceed one full cleanup cycle.

#### Scenario: Cleanup runs each tick cycle
- **WHEN** `_pending_limits` contains an entry with `deadline = now`
- **AND** `tick()` is invoked at `now + 1`
- **THEN** the entry SHALL be processed (resolved or rejected per its `limit_no_fallback`)
- **AND** the entry SHALL NOT remain in `_pending_limits` after the tick

#### Scenario: Empty pending limits is no-op
- **WHEN** `tick()` is invoked with empty `_pending_limits`
- **THEN** `_scan_pending_limits` SHALL be a fast no-op (no I/O, no publishes)
