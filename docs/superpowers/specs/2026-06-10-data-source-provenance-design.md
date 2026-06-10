---
comet_change: data-source-provenance
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-10-data-source-provenance
status: final
---

# Data Source Provenance — Technical Design

Date: 2026-06-10

> Requirements are owned by the OpenSpec delta spec
> (`openspec/changes/data-source-provenance/specs/data-source-provenance/spec.md`).
> This document is the HOW: implementation approach, technical choices, risks, tests, edge cases.

## Problem (from exploration)

`multi_data_collector` fuses 9 dimensions into a flat `market_data` payload. The highest-influence cross-source signals — `oi_data`, `taker_ratio`, `long_short_account` — are fetched from **Binance fapi** and fed into an **OKX**-primary system; `big_trades`/`funding` are OKX. The Binance taker/long-short feeds are `period=1h&limit=1` (up to an hour stale) and the API item timestamp is **discarded**. The payload has aggregate `data_quality` but no per-dimension provenance.

Consumption path: `collector → market_data → tech_analyst → tech_analysis → Judge/Reviewer`. `tech_analyst` collapses raw dimensions into derived signals; Judge reads only `tech_analysis`; Reviewer reads trade records. So provenance must be captured at the collector AND forwarded through tech_analyst AND summarized into trade attribution to reach all consumers.

`utils/symbol_mentions.py` (source/confidence/match_rule/freshness_sec, FR-3D) is the precedent to mirror.

## Confirmed Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Non-breaking parallel `provenance` block (not value-wrapping) | Wrapping values breaks every reader (`oi_data.get('delta_1h_pct')`); a sibling block lets unaware consumers ignore it. Mirrors `symbol_mentions`. |
| D2 | `freshness_sec` from the datum timestamp, fetch-time fallback | Binance items carry `timestamp`, OKX trades carry `ts` — currently discarded. `period=1h` feeds then report up-to-3600s honestly. No timestamp → time-since-fetch, never crash. |
| D3 | `confidence` from a single `derive_confidence` function | Consistent scoring across dimensions, policy testable in isolation, constants in one place. |
| D4 | Propagate through `tech_analyst` as a pass-through block | tech_analyst forwards used-dim provenance into `tech_analysis` without re-deriving; the number Judge/Reviewer sees is the collector's. |
| D5 | Judge attaches a per-decision provenance summary to `attribution` (metadata-only) | Reviewer reads trade records, not tech_analysis. A summary in `attribution` rides the trade to the Reviewer. **No gating/ranking/veto** — like the existing `short_gate` attribution. Keeps the observability-only scope. |
| D6 | Reviewer buckets read-only by the trade-record summary | Additive segmentation; feeds no live gate. |
| D7 | native venue configurable, default `okx` | The cross-exchange penalty needs the native venue to flag Binance-sourced dims as cross-venue. |

## Component Design

### `utils/data_provenance.py` (new)

```
derive_confidence(source, freshness_sec, *, native_venue='okx', period_sec=None, degraded=False) -> float
```

- `confidence = freshness_factor * source_factor`, then `0.0` if `degraded` or the datum is missing.
- `freshness_factor = clamp(1 - freshness_sec / (period_sec * 2), 0.0, 1.0)` — linear decay to 0 at 2× the sampling period.
- `source_factor = 1.0` when the source venue == `native_venue`, else `0.7` (cross-exchange penalty).
- Also a small builder `provenance_entry(source, item_ts, now, *, period_sec, native_venue, degraded) -> {source, freshness_sec, confidence}`.
- Per-dimension `period_sec`: `oi_data=300`, `taker_ratio=3600`, `long_short_account=3600`, `big_trades=60`, `funding_rate` native (period from funding interval; treat as native OKX). Constants live here, adjustable.

### `multi_data_collector` (modify)

- Each `_fetch_*` helper returns `(value_dict, meta)` where `meta = {source, item_ts}` (item_ts in ms, or None). `value_dict` content is byte-identical to today.
  - `_fetch_oi_delta` → `source='binance_fapi'`, `item_ts` = last point's timestamp.
  - `_fetch_taker_ratio` / `_fetch_long_short_ratio` → `source='binance_fapi'`, `item_ts` = item `timestamp`.
  - `_fetch_big_trades` → `source='okx'`, `item_ts` = newest trade `ts`.
  - `funding_rate` (ccxt OKX) → `source='okx'`, `item_ts` = funding `timestamp` if present.
- `_full_collect` unpacks metas, calls `provenance_entry(...)` per dimension (passing `degraded` from the existing `dimensions_ok < 6` check), and assembles `market_data["provenance"] = {dim: {source, freshness_sec, confidence}}`. Missing/failed dim → entry with `confidence=0.0` (or omitted consistently). Flat values unchanged.

### `tech_analyst` (modify)

- Read `market_data.get("provenance", {})`; copy the entries for the dimensions it consumed (`oi_data`, `taker_ratio`, `long_short_account`, `big_trades`) into `tech_analysis["provenance"]`. Pure forward, no re-derivation. Legacy `market_data` without provenance → no `provenance` block emitted.

### `judge` (modify, metadata-only)

- When building `trade_decision.attribution`, read `tech_analysis.get("provenance", {})` and attach a compact summary: `{weakest_confidence: min over contributing dims, has_cross_exchange: bool, quality: 'unknown' if no provenance}`. This is written exactly like the existing attribution fields and **must not** influence action/ranking/veto. Missing provenance → `quality='unknown'`.

### `reviewer` (modify, read-only)

- When segmenting outcomes, read the trade record's attribution provenance summary; add a bucket dimension (e.g. confidence band low/med/high and/or native-vs-cross-exchange). Records without a summary → `unknown` bucket. No live gate.

## Data Flow

```
_fetch_* (value_dict, meta{source,item_ts}) ─▶ _full_collect
        └─ provenance_entry + derive_confidence ─▶ market_data{...flat..., provenance{dim:{source,freshness_sec,confidence}}}
                                                          ▼
        tech_analyst forwards used-dim provenance ─▶ tech_analysis{...derived..., provenance{...}}
                                                          ▼
        judge summarizes into attribution (metadata) ─▶ trade_decision.attribution.provenance{weakest_confidence,has_cross_exchange,quality}
                                                          ▼
                       execution_result → trade history ─▶ reviewer buckets by quality (read-only)
```

## Edge Cases

- API item missing timestamp → `freshness_sec` = time-since-fetch; source still recorded.
- Dimension fetch fails/empty → `confidence=0.0`, no fabricated value.
- Legacy `market_data`/`tech_analysis`/trade record without provenance → consumers treat as `unknown`, never crash.
- `degraded` collector state → all dims floored toward 0 confidence.
- Funding period: treat as OKX-native (no cross-exchange penalty), freshness from funding timestamp when available.

## Test Strategy

- `derive_confidence`: fresh-native high; stale (≥2×period) → 0; cross-exchange penalty applied; degraded → 0; monotonic decay; single-function consistency.
- collector: provenance present + shape; flat values byte-identical (regression); hourly feed `freshness_sec ≈` up to 3600; `source=binance_fapi` for OI/taker/long_short, `okx` for big_trades; missing dim → confidence 0; no-item-ts fallback.
- tech_analyst: provenance survives into `tech_analysis`; legacy market_data tolerated.
- judge: attribution provenance summary present + correct (weakest/has_cross_exchange/unknown); **decision-unchanged guard** (same action/ranking with and without provenance).
- reviewer: buckets split by a provenance dimension; legacy records → `unknown` bucket.

## Risks / Trade-offs

- Helper return-shape change → keep value_dict identical; only collector call sites change; value-content tests unaffected.
- Confidence policy is a judgment call → centralized + unit-tested; observability-only this change, so a wrong constant has no trading impact.
- Judge attribution write near decision code → guarded by the decision-unchanged test; metadata-only, mirrors existing `short_gate` attribution pattern.
- Payload bloat → small fixed dict per dimension; negligible.

## Migration / Rollback

Land `utils/data_provenance.py` (+tests) → collector capture+emit → tech_analyst forward → judge attribution summary → reviewer bucketing. Rollback: provenance is additive; removing the blocks/summary leaves flat values and all behavior intact.

## Out of Scope

Judge behavioral down-weighting of stale/cross-exchange/low-confidence signals — separate follow-up change (strategy change, event-backtest gated, depends on this change accumulating provenance first).
