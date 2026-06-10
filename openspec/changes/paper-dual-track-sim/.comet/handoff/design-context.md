# Comet Design Handoff

- Change: paper-dual-track-sim
- Phase: design
- Mode: compact
- Context hash: 5757aa852b0609e5d7bcb31a16c42bf7c516f793e4945c3119a9f319edef1e5c

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/paper-dual-track-sim/proposal.md

- Source: openspec/changes/paper-dual-track-sim/proposal.md
- Lines: 1-36
- SHA256: d52c9fe25c66f7ccf1724e1f793525d08e323c8a890d76b2e3b795902edbf105

```md
## Why

The paper executor's limit-fill discipline drops roughly half of intended entries (`limit_unfilled`), but the system cannot currently measure whether missing those entries helps or hurts. The closed-trade ledger structurally cannot hold unfilled events (an unfilled limit never opens, so it never closes and never lands in `paper_trades.jsonl`), and the historical price data needed to reconstruct the counterfactual does not exist locally. A dual-track shadow account — running the real limit-based book alongside an idealized market-immediate baseline — is the only instrument that can generate this missing data going forward.

### Evidence from historical exploration (2026-06-04 → 06-10, limit-fill era)

- **Unfilled is high-frequency:** 7 `limit_filled` vs 8 `limit_unfilled` vs 4 `market` → limit unfilled rate ≈ 53%. About half of all limit entries never become positions.
- **Filled limits are the only profitable bucket:** `limit_filled` n=7, sum +9.04, win 43%; `market` entries n=5, sum −33.30, avg −6.66, win 40%. This weakly suggests the limit-wait discipline is *filtering out bad entries*, not merely costing missed upside.
- **The counterfactual is unrecoverable retroactively:** all 8 unfilled decisions were on low-cap symbols (XLM/NEAR/WLD/H/HYPE) with no local kline history and no per-symbol persisted tick stream. "Would the missed trades have won or lost?" cannot be answered from existing data — only generated prospectively.
- **Sample is tiny (n≈19 over one week):** any gap estimate has wide error bars, so the value is in *accumulating* the comparison continuously, not in a one-shot report.

These findings drive two settled decisions: the idealized baseline is **market-immediate** (answers "is our limit discipline net-positive vs naive market entry?", the open question the data cannot close), and a **minimal comparison consumer is in-scope** (the current paper stream is consumed by no Reviewer — emitting more unread data would repeat that trap).

## What Changes

- Add an **idealized book** to `PaperExecutor`: for every `open_*` decision, in addition to the existing realistic (limit/market) path, immediately open a market-fill position at the latest tick price. Both books run independent position/SL-TP/close/equity lifecycles.
- Introduce a **`book ∈ {realistic, idealized}` dimension** across paper positions, equity, and trade records. The realistic book preserves today's exact behavior (limit queue, `limit_filled`/`limit_unfilled`, market fallback); the idealized book always fills.
- Tag every `paper_execution_result` and trade-ledger record with `book`, so the two streams are separable downstream.
- Add a **minimal comparison consumer** (`PaperDualTrackReviewer` agent or reporting function) that joins the two books and computes the idealized-vs-realistic gap: win%, EV, max drawdown, and signed `realistic − idealized` net effect (the "limit discipline value"), surfaced to logs and Telegram `/status` / a dedicated command.
- Add config to enable/disable the idealized book and tune the comparison window, so the dual-track overhead can be turned off in production if undesired.
- Preserve backward compatibility: existing `paper_trades.jsonl` / `paper_positions.json` records without a `book` field are treated as `realistic`.

## Capabilities

### New Capabilities
- `paper-dual-track`: Dual-book (realistic + idealized) paper simulation and the comparison/reporting layer that quantifies the limit-discipline gap.

### Modified Capabilities
- `paper-executor`: Open/close/SL-TP/persistence and the `paper_execution_result` contract gain a `book` dimension; the realistic book's externally observable behavior is unchanged.

## Impact

- **Code:** `agents/trading/paper_executor.py` (book dimension across `_open_paper`, `_open_paper_at_price`, `_close_paper`, `_check_sl_tp`, `_add_paper`, `_reduce_paper`, `_persist_state`, `_load_state`, `_unrealized_pnl`, `tick`); new comparison consumer under `agents/trading/`; `agents/orchestrator.py` (register new consumer if agent-based); `utils/config_loader.py` (new config keys + env overrides); `agents/trading/telegram_notifier.py` (surface gap; it currently reads `paper_positions.json` directly).
- **State files:** `paper_positions.json` / `paper_equity.json` / `paper_trades.jsonl` schema extended with `book`; may add an idealized-book state file or namespace within existing files (design decision).
- **Tests:** new dual-track open/close/comparison tests; existing paper tests updated for the `book` field while asserting realistic-book behavior is unchanged.
- **Out of scope:** cross-source data provenance (`source`/`freshness_sec`/`confidence`) is a separate planned change; live executor and live Reviewer are untouched; paper/live isolation is preserved (idealized data must never reach live Reviewer metrics).
```

## openspec/changes/paper-dual-track-sim/design.md

- Source: openspec/changes/paper-dual-track-sim/design.md
- Lines: 1-102
- SHA256: 850102b34198afdb7956b1d2f792706d929fba2112fb362e619ec8c927f503d3

[TRUNCATED]

```md
## Context

`PaperExecutor` (`agents/trading/paper_executor.py`, 659 lines) is today a **single-book** shadow account: one `self._positions` map, one `self._equity`, one `_pending_limits` queue, one trade ledger (`paper_trades.jsonl`). It already implements the realistic limit-fill contract (`limit_filled` / `limit_unfilled` / `market` fallback, tick-driven, see the `paper-executor` master spec). Its output is consumed by **nobody analytic** — only `telegram_notifier.py` reads `paper_positions.json` for `/status` display. The Reviewer never touches paper data.

Historical exploration (proposal §Evidence) established: limit unfilled rate ≈ 53%, `limit_filled` is the only profitable entry bucket, `market` entries bleed, and the unfilled counterfactual cannot be reconstructed locally (no kline history for the affected low-cap symbols, no persisted per-symbol tick stream). The change therefore must (a) generate the counterfactual prospectively via an idealized market-immediate book, and (b) ship a comparison consumer, because unread paper data is the failure mode we are already in.

Constraints from CLAUDE.md: paper/live isolation is a red line (idealized data must never enter live Reviewer metrics); `_pending_limits` is in-memory only and must not be serialized; the realistic book's observable behavior must not regress; single-function-collapse discipline (no per-call-site re-implementation of book logic).

## Goals / Non-Goals

**Goals**
- Run an idealized (market-immediate) book alongside the realistic book for every open decision, with fully independent position/SL-TP/close/equity lifecycles.
- Tag all paper positions, `paper_trades.jsonl` records, and `paper_execution_result` events with `book ∈ {realistic, idealized}`.
- Ship a minimal comparison consumer that computes per-book win%/EV/total-PnL/max-drawdown and the signed `realistic − idealized` gap, surfaced to logs + Telegram.
- Make the idealized book toggleable, defaulting so production live behavior is unchanged; full backward compatibility with legacy paper state.

**Non-Goals**
- No change to the live executor, live Reviewer, or any live trading path.
- No "limit-always-filled" baseline (rejected below — see Decision 2).
- No cross-source data provenance work (separate change).
- No historical backfill of the idealized book (the counterfactual is only generated going forward).

## Decisions

### Decision 1 — Single agent, `book` dimension (not two agents)

Run both books inside the existing `PaperExecutor`, parameterizing the shared paths by `book`. Positions become `self._books[book].positions`, equity `self._books[book].equity`; `_open_paper_at_price`, `_close_paper`, `_check_sl_tp`, `_add_paper`, `_reduce_paper`, `_unrealized_pnl`, `_persist_state`, `_load_state` take/iterate a book.

- **Alternative A — two `PaperExecutor` instances** (one realistic, one idealized) via orchestrator. Rejected: doubles agent wiring, duplicates state-file namespaces, doubles bus traffic, and the comparison still has to re-join two independent streams. The price feed and tick loop are naturally shared by one agent.
- **Alternative B — single book + shadow-fill annotation** (record an idealized fill price on each realistic trade, reconstruct idealized PnL at close). Rejected outright: it cannot represent the `limit_unfilled` case, where there is no realistic position to annotate but the idealized book must still run a full independent lifecycle. That case is the entire point.

Rationale: one agent already owns `_latest_price` and the `tick()` cleanup loop; the only true divergence between books is the **entry path**, so a book parameter on the shared helpers is the smallest change that fully supports the unfilled case.

### Decision 2 — Idealized baseline is market-immediate (not limit-always-filled)

The idealized book fills at `_latest_price` at decision time. Rejected alternative: assume the limit always fills at its intended price (`entry_zone` midpoint), isolating *pure* miss-cost.

Rationale (from the data): `limit_filled` is the only profitable bucket while `market` entries bleed, which suggests the limit-wait may be *filtering out bad entries* rather than merely costing missed upside. The limit-always-filled baseline assumes you would always get your ideal price and therefore systematically overstates the value of limits; it answers a question the data does not leave open. Market-immediate answers the open question — "is our limit discipline net-positive versus naive market entry?" — via the sign of `realistic − idealized`. This is captured as the `limit_discipline_value` metric.

### Decision 3 — Comparison consumer is a thin paper-only reader

Add a comparison consumer that reads the two books' closed trades over a configurable window and emits per-book win%/avg-net-PnL/total-net-PnL/max-drawdown plus `limit_discipline_value = realistic_total − idealized_total`. It surfaces to logs and a Telegram-readable summary; it never writes into a live Reviewer metric.

- **Form**: prefer a small dedicated consumer (a `PaperDualTrackReviewer` agent subscribing to `paper_execution_result`, or a reporting helper invoked on a timer / Telegram command). Final form is a build-phase detail; the spec only requires the computation and surfacing. Whichever form, it must reuse the `book` tag and must report low-sample explicitly (n≈19 today → wide error bars).
- **Alternative — extend the existing Reviewer**. Rejected: the Reviewer is live-metric owner; mixing paper/idealized data there risks violating the paper/live isolation red line. A separate consumer keeps the boundary crisp.

### Decision 4 — State layout: nested books, legacy loads as realistic

`paper_positions.json` / `paper_equity.json` move from a flat symbol→position map to a book-keyed structure (e.g. `{realistic: {...}, idealized: {...}}`), written atomically as today. On load, a legacy flat file (no book keys) is assigned wholesale to the realistic book; the idealized book starts empty. `paper_trades.jsonl` records simply gain a `book` field; legacy records without it read as `realistic`. `_pending_limits` stays in-memory and realistic-only, never serialized.

- **Alternative — separate idealized state files** (`paper_positions_idealized.json`). Viable and slightly simpler for backward compat, but spreads paper state across more files and complicates the telegram reader. Nested layout keeps one file per concern. Either is acceptable; build phase picks one and the spec covers both via the "legacy loads as realistic" + "round-trip preserves book separation" scenarios.

### Decision 5 — Toggle defaults to safe

A config flag (e.g. `paper_dual_track_enabled`) gates the idealized book and the comparison consumer, with env override, bounded in `HARD_LIMITS` where numeric. When disabled, the executor is byte-for-byte equivalent in outcome to today (realistic only). Default value chosen in build phase; because paper is non-production-risk, enabling by default in paper namespace is acceptable, but the flag must exist so it can be turned off.

## Data Flow

```
trade_decision(open_*) ──▶ PaperExecutor._execute_decision
        │
        ├─▶ REALISTIC book  (unchanged)
        │     order_type=limit → _pending_limits → _wait_paper_limit_fill
        │                          ├─ hit  → limit_filled
        │                          └─ timeout → limit_unfilled / market fallback
        │     order_type=market → immediate fill
        │
        └─▶ IDEALIZED book  (new, if enabled & tick fresh)
              immediate market fill @ _latest_price, entry_method='market'

price_tick ─▶ _check_sl_tp(book) for EACH book independently  ─▶ _close_paper(book)
                                                                   │
                          paper_trades.jsonl (record.book) ◀───────┘
                          paper_execution_result(book) ──▶ PaperDualTrackReviewer
                                                              └─▶ per-book metrics + limit_discipline_value
                                                                  └─▶ logs + Telegram (paper-only)
```

## Risks / Trade-offs

```

Full source: openspec/changes/paper-dual-track-sim/design.md

## openspec/changes/paper-dual-track-sim/tasks.md

- Source: openspec/changes/paper-dual-track-sim/tasks.md
- Lines: 1-34
- SHA256: 13e28e99883539927d64a7ec6c120bd59c4580c696f95a727c1e56698389a287

```md
## 1. Book Abstraction (realistic-only, behavior-preserving)

- [ ] 1.1 Introduce a per-book state container (positions + equity + locked margin) and route all access through it; default/single book = realistic.
- [ ] 1.2 Parameterize `_open_paper_at_price`, `_close_paper`, `_check_sl_tp`, `_add_paper`, `_reduce_paper`, `_unrealized_pnl` by `book`; keep `_pending_limits` realistic-only.
- [ ] 1.3 Tag every position record, `paper_trades.jsonl` close record, and `paper_execution_result` event with `book` (`'realistic'` default).
- [ ] 1.4 Update existing paper tests to assert the realistic book's outcomes are unchanged (only the `book='realistic'` tag added).

## 2. State Persistence + Backward Compatibility

- [ ] 2.1 Move `paper_positions.json` / `paper_equity.json` to a book-keyed layout, written atomically.
- [ ] 2.2 Legacy-load shim: a pre-change flat `paper_positions.json` loads wholesale into the realistic book; idealized starts empty; never deserialize pending limits.
- [ ] 2.3 Update `telegram_notifier.py` `/status` reader to the new layout while keeping legacy files readable.
- [ ] 2.4 Round-trip + legacy-load tests.

## 3. Idealized Book

- [ ] 3.1 Config flag `paper_dual_track_enabled` (DEFAULTS + HARD_LIMITS where numeric + env override); disabled path must be outcome-equivalent to today.
- [ ] 3.2 On every `open_*` decision, when enabled and tick is fresh, open an idealized market position at `_latest_price` with `entry_method='market'`, `book='idealized'`; skip on missing tick (fail-safe).
- [ ] 3.3 Mirror `close`/`add`/`reduce` lifecycle actions into the idealized book; run idealized SL/TP independently in `_check_sl_tp`.
- [ ] 3.4 Tests: limit decision still fills idealized at market; unfilled realistic still leaves idealized open + closes on its own SL/TP; missing tick skips idealized open; divergent entry → divergent PnL.

## 4. Comparison Consumer

- [ ] 4.1 Implement the paper-only comparison consumer (dedicated `PaperDualTrackReviewer` agent or timer/command helper) reading both books' closed trades over a configurable window.
- [ ] 4.2 Compute per-book win% / avg net PnL / total net PnL / max drawdown + `limit_discipline_value = realistic_total − idealized_total`.
- [ ] 4.3 Report low-sample / wide-error-bars explicitly when either book is under the configured minimum trade count.
- [ ] 4.4 Surface to logs + Telegram-readable summary; assert no idealized data reaches any live Reviewer metric (paper/live isolation test).
- [ ] 4.5 Register the consumer in `agents/orchestrator.py` if agent-based.

## 5. Verification

- [ ] 5.1 Run targeted dual-track suite (book abstraction, idealized open/close, unfilled-but-idealized, comparison math, isolation).
- [ ] 5.2 Run full `python3 -m pytest -q`; confirm realistic-book regression-free and capture the new baseline count.
- [ ] 5.3 Compileall check.
```

## openspec/changes/paper-dual-track-sim/specs/paper-dual-track/spec.md

- Source: openspec/changes/paper-dual-track-sim/specs/paper-dual-track/spec.md
- Lines: 1-109
- SHA256: c6d60b32aac2364650f7d8d93becb738cc5331971892c6c5582a45042291d8fe

[TRUNCATED]

```md
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

```

Full source: openspec/changes/paper-dual-track-sim/specs/paper-dual-track/spec.md

## openspec/changes/paper-dual-track-sim/specs/paper-executor/spec.md

- Source: openspec/changes/paper-dual-track-sim/specs/paper-executor/spec.md
- Lines: 1-48
- SHA256: a6b54a75e7cd0c8e90282fa0a9967451d34a182cdef51970fe448492a5f1c323

```md
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
```

