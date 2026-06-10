## ADDED Requirements

### Requirement: Idealized book SHALL open a market-immediate position for every open decision

When the idealized book is enabled and the paper executor receives a `trade_decision` with `action ∈ {open_long, open_short}`, it SHALL open an idealized-book position immediately at the latest available tick price, regardless of `plan.order_type`. The idealized book SHALL NOT use the limit queue, SHALL NOT defer to `entry_zone`, and SHALL NOT ever produce an `unfilled` outcome. The realistic book SHALL continue to process the same decision through its existing limit/market path unchanged.

#### Scenario: Limit decision still fills the idealized book at market
- **WHEN** a `trade_decision{action:'open_long', plan:{order_type:'limit', entry_zone:[100,101], ...}}` arrives and `_latest_price[symbol]=102`
- **THEN** the idealized book SHALL hold a position with `entry_price=102` and `entry_method='market'` and `book='idealized'`
- **AND** the realistic book SHALL queue a pending limit (no realistic position yet), exactly as it does today

#### Scenario: Unfilled realistic limit still leaves an idealized position
- **WHEN** a realistic pending limit times out with `limit_no_fallback=True` (realistic outcome `limit_unfilled`, no realistic position)
- **THEN** the idealized-book position opened at decision time SHALL remain open and continue its own SL/TP lifecycle
- **AND** the realistic book SHALL record `paper_unfilled` as it does today

#### Scenario: Missing tick price skips the idealized open
- **WHEN** an open decision arrives but `_latest_price[symbol]` is missing
- **THEN** the idealized book SHALL NOT open a position (fail-safe — never fabricate an entry price)
- **AND** the realistic book SHALL proceed on its own path independently

### Requirement: Idealized book SHALL mirror strategy lifecycle decisions to isolate the entry effect

So that `limit_discipline_value = realistic_total − idealized_total` measures only the entry difference (limit vs market) under an identical exit policy, the idealized book SHALL apply the same strategy lifecycle decisions (`close`, `reduce`, `add`) that the realistic book receives, in addition to its own SL/TP. When a `close`/`reduce`/`add` `trade_decision` is processed for a symbol and the idealized book holds a position for that symbol, the idealized book SHALL apply the same action. When the realistic book is `unfilled` (no realistic position, so no strategy close is generated for it), the idealized position SHALL run autonomously and exit solely on its own SL/TP — this case is what quantifies a trade the realistic book missed entirely.

#### Scenario: Strategy close is applied to both books
- **WHEN** both books hold a position for a symbol and a `trade_decision{action:'close'}` is processed
- **THEN** the realistic book SHALL close as it does today
- **AND** the idealized book SHALL also close its position for that symbol on the same decision
- **AND** each book's close record SHALL reflect its own entry price and `book` tag

#### Scenario: Strategy reduce/add is mirrored when idealized holds a position
- **WHEN** the idealized book holds a position for a symbol and a `trade_decision{action:'reduce'}` or `{action:'add'}` is processed
- **THEN** the idealized book SHALL apply the same reduce/add to its own position
- **AND** the realistic book SHALL apply it independently to its own position

#### Scenario: Unfilled realistic leaves idealized to exit on its own SL/TP
- **WHEN** the realistic book is `unfilled` for a decision (no realistic position) and the idealized book opened a position
- **AND** no strategy `close` is ever generated for that symbol's realistic position (because none exists)
- **THEN** the idealized position SHALL exit only when its own SL or TP is hit
- **AND** the resulting idealized closed trade SHALL represent the missed-trade outcome for the gap computation

#### Scenario: Close for a symbol the idealized book does not hold is a no-op for idealized
- **WHEN** a `trade_decision{action:'close'}` is processed for a symbol the idealized book has no position for
- **THEN** the idealized book SHALL take no action
- **AND** the realistic book SHALL process the close normally

### Requirement: The two books SHALL maintain independent position, equity, and trade state

The realistic and idealized books SHALL each maintain their own positions, equity, locked margin, and closed-trade records. An open, close, SL/TP hit, add, or reduce in one book SHALL NOT mutate the other book's state. Each book SHALL apply the same fee model and the same plan-derived SL/TP levels.

#### Scenario: Realistic close does not touch idealized position
- **WHEN** the realistic position for a symbol closes on an SL hit
- **THEN** the idealized position for that symbol SHALL remain open until its own SL/TP/close condition is met
- **AND** the idealized equity SHALL be unchanged by the realistic close

#### Scenario: Divergent entry prices yield divergent PnL
- **WHEN** the realistic book filled a limit at midpoint 100.5 and the idealized book filled at market 102 for the same long decision
- **AND** both close at exit price 110
- **THEN** the two closed-trade records SHALL show different `net_pnl` reflecting their different entry prices
- **AND** each record SHALL carry its own `book` tag

### Requirement: Paper records SHALL carry a book dimension with realistic default

Every paper position record and every `paper_trades.jsonl` close record SHALL include a `book` field with value `'realistic'` or `'idealized'`. Any record produced before this change (lacking `book`) SHALL be treated as `book='realistic'` by all downstream readers (fail-safe default). The idealized book's records SHALL never be counted in live Reviewer metrics, preserving paper/live isolation.

#### Scenario: Realistic record tagged realistic
- **WHEN** the realistic book closes a trade
- **THEN** the appended `paper_trades.jsonl` record SHALL include `book='realistic'`

#### Scenario: Idealized record tagged idealized
- **WHEN** the idealized book closes a trade
- **THEN** the appended `paper_trades.jsonl` record SHALL include `book='idealized'`

#### Scenario: Legacy record without book treated as realistic
- **WHEN** a downstream consumer loads a `paper_trades.jsonl` record produced before this change
- **THEN** the absence of `book` SHALL be treated as `book='realistic'` and processing SHALL continue normally

### Requirement: A comparison consumer SHALL compute and surface the realistic-vs-idealized gap

The system SHALL provide a comparison consumer that joins the two books' closed trades over a configurable window and computes, per book, win%, average net PnL (EV proxy), total net PnL, and max drawdown, plus the signed gap `realistic − idealized` for total net PnL (the "limit discipline value"). The result SHALL be surfaced to logs and to a Telegram-readable summary. The consumer SHALL operate only on paper data and SHALL NOT publish into any live Reviewer metric.

#### Scenario: Gap computed when both books have trades
- **WHEN** the comparison window contains closed trades in both books
- **THEN** the consumer SHALL emit per-book win%, avg net PnL, total net PnL, and max drawdown
- **AND** SHALL emit `limit_discipline_value = realistic_total_net_pnl − idealized_total_net_pnl`

#### Scenario: Positive gap indicates limit discipline helps
- **WHEN** `realistic_total_net_pnl > idealized_total_net_pnl` over the window
- **THEN** the summary SHALL report the limit discipline as net-positive (the realistic book outperformed naive market entry)

#### Scenario: Insufficient sample is reported, not hidden
- **WHEN** either book has fewer than the configured minimum number of closed trades in the window
- **THEN** the summary SHALL explicitly report low-sample / wide-error-bars rather than presenting a misleading point estimate

### Requirement: The idealized book SHALL be toggleable by configuration

The idealized book and its comparison consumer SHALL be enabled/disabled via configuration, defaulting in a way that does not change production live behavior. When disabled, the paper executor SHALL behave exactly as it does today (realistic book only, no idealized positions, no comparison output).

#### Scenario: Disabled flag preserves single-book behavior
- **WHEN** the idealized book is disabled by config
- **THEN** no idealized positions SHALL be opened
- **AND** no `book='idealized'` records SHALL be written
- **AND** the realistic book and all existing paper behavior SHALL be byte-for-byte unchanged in outcome

#### Scenario: Enabled flag activates dual-track
- **WHEN** the idealized book is enabled by config
- **THEN** every open decision SHALL additionally open an idealized market position (subject to tick availability)
- **AND** the comparison consumer SHALL produce gap output
