---
comet_change: paper-dual-track-sim
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-10-paper-dual-track-sim
status: final
---

# Paper Dual-Track Simulation — Technical Design

Date: 2026-06-10

> Requirements are owned by the OpenSpec delta specs
> (`openspec/changes/paper-dual-track-sim/specs/{paper-dual-track,paper-executor}/spec.md`).
> This document is the HOW: implementation approach, technical decisions, risks, test strategy, and edge cases.

## Problem (from exploration)

`PaperExecutor` is a single-book shadow account whose analytic output nobody consumes. Limit-fill discipline drops ≈53% of intended entries (`limit_unfilled`), but the system cannot tell whether missing them helps or hurts: the closed-trade ledger structurally cannot contain unfilled events, and the counterfactual is unrecoverable locally (no kline history for the affected low-cap symbols, no persisted per-symbol tick stream). The 1-week sample (n≈19) further shows `limit_filled` is the only profitable bucket while `market` entries bleed — a weak signal that the limit-wait may be *filtering out bad entries*. We therefore generate the counterfactual prospectively via an idealized market-immediate book and ship a paper-only comparison reader.

## Confirmed Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Single `PaperExecutor`, `book ∈ {realistic, idealized}` dimension** | One agent already owns `_latest_price` + the `tick()` loop; the only true divergence is the entry path. Two-agent and shadow-annotation alternatives rejected (proposal/OpenSpec design Decision 1). |
| D2 | **Idealized baseline = market-immediate** at decision-time `_latest_price` | Answers the open question "is the limit discipline net-positive vs naive market entry?" via the sign of `realistic − idealized`. Limit-always-filled baseline rejected — it assumes ideal fills and overstates limit value. |
| D3 | **Idealized mirrors strategy lifecycle exits (close/reduce/add) + its own SL/TP** | Isolates the *entry* effect under an identical exit policy. When realistic is unfilled, no strategy close exists for it, so idealized runs autonomously on SL/TP — which is exactly the missed-trade measurement. (Delta spec: "Idealized book SHALL mirror strategy lifecycle decisions".) |
| D4 | **Comparison = paper-only reader helper, not an agent** | The gap is a pure slice-statistic over already-persisted closed trades. A `paper_trades.jsonl` reader fed by a Telegram command (+ optional periodic log) is the smallest, most testable surface. Upgradeable to an agent later without breaking the data contract. |
| D5 | **Separate state files** — realistic keeps the existing `paper_positions.json` / `paper_equity.json` **unchanged**; idealized writes new `paper_positions_idealized.json` / `paper_equity_idealized.json` | Zero format change on the realistic side → `telegram_notifier.py` `/status` reader untouched, legacy files trivially compatible. Avoids the nested-file + legacy-shim risk. |
| D6 | **Toggle `paper_dual_track_enabled`, default ON in paper namespace** | Paper carries no production risk and the point is to accumulate data. live/testnet unaffected. Env `PAPER_DUAL_TRACK_ENABLED` overrides. Disabled path is outcome-equivalent to today. |

## Architecture

### Book container

Replace the three single-book fields (`_positions`, `_equity`, `_pending_limits`) with a small per-book structure. `_pending_limits` is **realistic-only** (idealized never queues).

```python
class _Book:
    positions: Dict[str, dict]   # symbol -> position
    equity: float
    # locked margin derived from positions, as today

self._books = {"realistic": _Book(...), "idealized": _Book(...)}
self._pending_limits: Dict[str, dict]   # realistic only, unchanged, in-memory
```

The shared helpers gain a `book` parameter and operate on `self._books[book]`:
`_open_paper_at_price`, `_close_paper`, `_check_sl_tp`, `_add_paper`, `_reduce_paper`, `_unrealized_pnl`, `_persist_state`, `_load_state`. Position records and `paper_trades.jsonl` close records carry `book`; `paper_execution_result` events carry `book`.

### Decision fan-out

```
trade_decision ──▶ _execute_decision
   │
   ├─ open_long/open_short
   │     ├─ realistic: existing path (limit queue → limit_filled/unfilled/market fallback)
   │     └─ idealized (if enabled & tick fresh): _open_paper_at_price(book='idealized',
   │                                              fill_price=_latest_price, entry_method='market')
   │
   ├─ close / reduce / add
   │     ├─ realistic: existing path
   │     └─ idealized: SAME action IFF idealized holds that symbol (else no-op)   ← D3
   │
price_tick ─▶ _check_sl_tp('realistic', p)  AND  _check_sl_tp('idealized', p)   ← independent
```

The idealized open reuses the realistic margin/free-equity guard against its **own** equity, so the two books are independently solvent. Idealized uses the same `_fee` model and the same plan-derived SL/TP (`plan.stop_loss` / `tp_levels`), so only the entry price differs when both fill.

### Comparison reader (D4)

A pure function in a new small module (e.g. `agents/trading/paper_dual_track_report.py`) — no agent, no bus:

```
compute_gap(trades, window) -> {
   realistic: {n, win_pct, avg_net_pnl, total_net_pnl, max_drawdown},
   idealized: {n, win_pct, avg_net_pnl, total_net_pnl, max_drawdown},
   limit_discipline_value: realistic.total_net_pnl - idealized.total_net_pnl,
   low_sample: bool   # true when either n < min_trades
}
```

Reads `paper_trades.jsonl`, groups by `book` (missing → `realistic`), filters by window. Surfaced via a new Telegram command (`/paper_gap [days]`) and an optional periodic log line in `tick()`. `low_sample` is always reported so n≈19 never reads as a confident estimate.

## Edge Cases

- **Missing tick at decision time** → idealized skips the open (never fabricate an entry price); realistic proceeds independently.
- **Idealized open but realistic later unfilled** → idealized lives on, exits on own SL/TP (the missed-trade case). Must be covered by a dedicated test.
- **Close for a symbol idealized doesn't hold** → idealized no-op; realistic closes normally.
- **Idealized fills but realistic also fills at a different price** → both close on the same strategy decision / same SL-TP; divergent `net_pnl` by entry price.
- **Legacy `paper_positions.json`** (flat map, pre-change) → loads as realistic; idealized file simply absent → idealized starts empty.
- **Paper/live isolation** → idealized records are paper-namespaced; a test asserts no idealized record reaches any live Reviewer metric.
- **Restart** → `_pending_limits` (realistic-only) never serialized, as today; idealized positions persist via their own file.

## Test Strategy

- **Realistic-unchanged regression**: existing paper tests updated only for the `book='realistic'` tag; with the toggle disabled, outcomes are byte-for-byte equivalent.
- **Idealized open**: limit decision still fills idealized at market; missing tick skips idealized.
- **Unfilled-but-idealized**: realistic times out `no_fallback=True` → idealized stays open → exits on own SL/TP → produces a `book='idealized'` closed trade.
- **Mirror exits (D3)**: strategy `close`/`reduce`/`add` applied to both books; close for non-held idealized symbol is a no-op.
- **Independence**: realistic close/SL does not mutate idealized; divergent entry → divergent PnL.
- **Persistence**: separate-file round-trip; legacy flat file loads as realistic.
- **Comparison math**: `compute_gap` over fixtures — win%/EV/total/drawdown per book + `limit_discipline_value` sign; `low_sample` flag.
- **Isolation**: idealized data never enters live Reviewer metrics.

## Migration / Rollback

Land book-parameterization with idealized disabled (prove realistic unchanged) → add separate-file persistence + legacy load → add idealized open + per-book SL/TP + mirror exits → add comparison reader + `/paper_gap` → enable toggle in paper namespace. Rollback = `PAPER_DUAL_TRACK_ENABLED=false`: reverts to single-book behavior, realistic stream intact, idealized simply stops being produced.

## Out of Scope

Cross-source data provenance (`source`/`freshness_sec`/`confidence`) is the next planned change. Live executor / live Reviewer untouched.
