## ADDED Requirements

### Requirement: Paper open/close paths SHALL be book-parameterized

The paper executor's shared open path (`_open_paper_at_price`), close path (`_close_paper`), SL/TP check (`_check_sl_tp`), add/reduce paths, and unrealized-PnL computation SHALL operate on a specified book rather than a single global `_positions`/`_equity`. The realistic book SHALL be the default and SHALL preserve today's externally observable behavior exactly; the idealized book SHALL reuse the same SL/TP and fee logic and differ only in its entry path (market-immediate). Pending limits (`_pending_limits`) SHALL belong to the realistic book only.

#### Scenario: Realistic book is the default and unchanged
- **WHEN** the idealized book is disabled
- **THEN** all paper opens, closes, SL/TP hits, adds, and reduces SHALL apply to the realistic book
- **AND** the resulting `paper_trades.jsonl` and `paper_positions.json` outcomes SHALL match pre-change behavior (with an added `book='realistic'` tag)

#### Scenario: SL/TP check evaluates each book independently
- **WHEN** a `price_tick` arrives and both books hold a position for the symbol with different entry prices
- **THEN** `_check_sl_tp` SHALL evaluate each book's position against its own SL/TP
- **AND** one book MAY close while the other remains open

#### Scenario: Pending limits never create an idealized entry
- **WHEN** a realistic pending limit fills or times out
- **THEN** only the realistic book SHALL be affected
- **AND** the idealized book position (if any) SHALL be governed solely by its own market entry and SL/TP

### Requirement: paper_execution_result SHALL carry the book dimension

Every `paper_execution_result` event SHALL include a `book` field (`'realistic'` or `'idealized'`). Consumers SHALL be able to filter the stream by book. Events lacking `book` (legacy) SHALL be treated as `book='realistic'`. The realistic-book event payload SHALL otherwise preserve its current field contract.

#### Scenario: Realistic execution result tagged realistic
- **WHEN** the realistic book publishes a `paper_execution_result` for an open or close
- **THEN** the payload SHALL include `book='realistic'`
- **AND** all previously specified payload fields SHALL remain present and unchanged

#### Scenario: Idealized execution result tagged idealized
- **WHEN** the idealized book publishes a `paper_execution_result`
- **THEN** the payload SHALL include `book='idealized'`
- **AND** the event SHALL NOT be consumed by any live Reviewer metric

### Requirement: Paper persistence SHALL separate the two books without breaking legacy load

Paper state persistence SHALL store realistic-book and idealized-book positions and equity such that the two are distinguishable on reload, while a legacy `paper_positions.json` written before this change (a flat symbol→position map with no book separation) SHALL load as the realistic book. Pending limits SHALL remain in-memory only and SHALL NOT be serialized for either book.

#### Scenario: Legacy positions file loads as realistic book
- **WHEN** the paper executor starts and finds a pre-change `paper_positions.json` (flat map, no book structure)
- **THEN** every loaded position SHALL be assigned to the realistic book
- **AND** the idealized book SHALL start empty

#### Scenario: Round-trip preserves book separation
- **WHEN** both books hold positions and the executor persists then reloads state
- **THEN** each position SHALL be restored to the same book it was saved under
- **AND** no pending-limit state SHALL be present after reload
