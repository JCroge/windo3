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

- **Doubled paper position count / state size** → idealized adds ~1 position per open; bounded by the same margin/slot checks per book; acceptable for a shadow account.
- **Idealized book opens when realistic is unfilled, then both diverge** → this is intended; the divergence *is* the measurement. Tests must cover the unfilled-but-idealized-open case explicitly.
- **Tiny sample (n≈19) → misleading point estimates** → mitigation: the consumer must report low-sample/wide-error-bars rather than a bare number (spec requirement).
- **Paper/live isolation regression** → mitigation: idealized records are paper-namespaced and never published to live Reviewer; a test asserts no idealized data reaches live metrics.
- **Behavior regression in realistic book from refactor** → mitigation: realistic remains the default book; existing paper tests are updated only for the `book` tag and otherwise assert identical outcomes; the disabled-flag path must be outcome-equivalent to today.
- **Telegram reader breakage** (`telegram_notifier.py` reads flat `paper_positions.json`) → mitigation: nested layout requires updating that reader; covered as a task; legacy-load path keeps `/status` working through the transition.

## Migration Plan

1. Land book-parameterized paths with idealized book **disabled by default in code**, so the realistic book is provably unchanged (tests green with `book='realistic'` tags added).
2. Add state nesting + legacy-load shim; verify round-trip and legacy `paper_positions.json` load.
3. Add idealized open path + per-book SL/TP/close; add unfilled-but-idealized-open tests.
4. Add comparison consumer + Telegram surface.
5. Enable the toggle in the paper namespace; observe gap accumulation.
- **Rollback**: flip `paper_dual_track_enabled=false` → executor reverts to single-book behavior with no data loss (idealized records simply stop being produced; realistic stream intact).

## Open Questions

- Comparison consumer form: dedicated agent vs timer/command-driven helper (resolve in build; spec is form-agnostic).
- State layout: nested single file vs separate idealized files (resolve in build; spec covers both).
- Default toggle value in paper namespace (lean enabled for paper, but confirm at build).
- Should the idealized book also mirror `add`/`reduce` decisions, or only `open`/`close`? Leaning yes (mirror all lifecycle actions for a faithful baseline), to confirm in build.
