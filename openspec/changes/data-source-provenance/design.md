## Context

`multi_data_collector._full_collect` fuses 9 dimensions into a flat `market_data` payload. Three of the most influential cross-source signals — `oi_data`, `taker_ratio`, `long_short_account` — are fetched from Binance fapi and fed into an OKX-primary system; `big_trades` and `funding_rate` come from OKX. The Binance taker/long-short feeds are `period=1h&limit=1` (up to an hour stale) and the API item timestamp is discarded. The payload has an aggregate `data_quality` block (dimensions_ok, degraded, last_success) but **no per-dimension provenance**.

Critically, the consumption path is `collector → market_data → tech_analyst → tech_analysis → Judge/Reviewer`. `tech_analyst` collapses raw dimensions into derived signals (`_analyze_crowd`, oi_delta, taker); Judge reads only `tech_analysis`. So provenance must be captured at the collector AND forwarded through tech_analyst, or it never reaches the consumers that matter.

The `utils/symbol_mentions.py` provenance pattern (source/confidence/match_rule/freshness_sec) is the established precedent to mirror.

## Goals / Non-Goals

**Goals**
- Capture `source` + `freshness_sec` (from the discarded API item timestamps) + derived `confidence` for `oi_data`, `taker_ratio`, `long_short_account`, `big_trades`, `funding_rate`.
- Emit a non-breaking parallel `provenance` block in `market_data` (flat values unchanged).
- Propagate provenance through `tech_analyst` into `tech_analysis`.
- Let Reviewer bucket outcomes by provenance.
- Centralize confidence derivation in one function.

**Non-Goals**
- No Judge behavioral change (gating/down-weighting deferred to a separate, backtest-gated change).
- No change to flat field values or existing consumers.
- No new data sources; no change to fetch cadence.

## Decisions

### D1 — Non-breaking parallel `provenance` block (not value-wrapping)
Add `market_data["provenance"] = { "<dimension>": {source, freshness_sec, confidence}, ... }`, leaving `market_data["taker_ratio"]` etc. as flat dicts. Rejected alternative: wrapping each value as `{value, source, freshness_sec, confidence}` — that breaks every reader (`tech_analyst` does `oi_data.get('delta_1h_pct')`). The parallel block mirrors how `symbol_mentions` provenance rides alongside, and lets unaware consumers ignore it.

### D2 — freshness from the datum timestamp, with fetch-time fallback
The `_fetch_*` helpers return the value dict today. Extend each to also surface the underlying item timestamp (Binance items carry `timestamp`; OKX trades carry `ts`). `freshness_sec = now - item_ts/1000`. For `period=1h` feeds this naturally yields up-to-3600s ages. When no item timestamp exists, fall back to time-since-fetch (fail-safe, never crash). The helper return shape changes from `dict` to `(value_dict, meta)` or a value dict plus a sibling meta — chosen at build to minimize churn; the collector assembles the provenance block from the metas.

### D3 — confidence derived in one function
A single helper (e.g. `utils/data_provenance.py::derive_confidence(source, freshness_sec, *, native_venue, period_sec, degraded)`) returns 0.0–1.0. Shape: start from 1.0, apply a freshness decay (relative to the feed's sampling period), apply a cross-exchange penalty when `source` venue ≠ native trading venue, floor to 0 when degraded or missing. Centralized so all dimensions score consistently and the policy is testable in isolation (mirrors `symbol_mentions`). Exact decay curve/penalty constants are a build detail; the spec only requires monotonic decay with staleness/cross-venue/degraded.

### D4 — propagate through tech_analyst as a pass-through block
`tech_analyst` reads `market_data["provenance"]` and copies the entries for the dimensions it actually used into `tech_analysis["provenance"]`. It does not re-derive confidence; it forwards. This keeps tech_analyst dumb about scoring and guarantees the number Judge/Reviewer sees is the one the collector computed. Legacy `market_data` without a provenance block → tech_analyst emits no provenance (consumers treat as unknown).

### D5 — Reviewer bucketing is read-only segmentation
Reviewer gains the ability to segment outcomes by a provenance attribute (confidence band and/or native-vs-cross-exchange). This is additive reporting; it does not feed any live gate. Form (new segmented metric vs extension of existing segmentation) chosen at build.

### D6 — native venue is configurable, defaulting to OKX
The cross-exchange penalty needs to know the native venue. Default `okx` (current live/testnet venue), overridable, so the penalty correctly flags Binance-sourced dimensions as cross-venue.

## Data Flow

```
_fetch_oi_delta / _fetch_taker_ratio / _fetch_long_short_ratio (Binance) ─┐
_fetch_big_trades / funding (OKX) ────────────────────────────────────────┤
                                                                          ▼
            _full_collect: value dicts (unchanged) + per-dim meta {source,item_ts}
                                                                          │
                          derive_confidence(source, freshness_sec, ...)   │
                                                                          ▼
            market_data{ ...flat values..., provenance:{dim:{source,freshness_sec,confidence}} }
                                                                          ▼
            tech_analyst: forwards used-dim provenance ─▶ tech_analysis{ ...derived..., provenance:{...} }
                                                                          ▼
                                   Judge (observes, no behavior change) / Reviewer (buckets)
```

## Risks / Trade-offs

- **Helper return-shape change** (`_fetch_*` now also returns meta) → mitigation: keep the value dict identical; add meta as a second return or sibling, update only the collector call sites; existing tests on value content unaffected.
- **Confidence policy is a judgment call** → mitigation: centralize + unit-test the curve; it's observability-only this change, so a wrong constant has no trading impact, only reporting.
- **Provenance bloat in payloads** → small fixed dict per dimension; negligible.
- **Legacy payloads** → all readers treat missing `provenance` as unknown/zero-confidence (spec scenarios cover this).

## Migration Plan

1. Add `utils/data_provenance.py` (`derive_confidence` + a `Provenance` shape) with unit tests.
2. Extend `_fetch_*` helpers to surface source + item timestamp; assemble `provenance` in `_full_collect`; emit in `market_data`. Tests assert flat values unchanged + provenance present/shape + realistic freshness for hourly feeds.
3. Forward provenance through `tech_analyst` into `tech_analysis`; test survival + legacy tolerance.
4. Add Reviewer bucketing + test.
- **Rollback**: provenance is additive; removing the block leaves flat values and all existing behavior intact.

## Open Questions

- Exact confidence decay curve + cross-exchange penalty constants (build detail; spec requires only monotonic decay).
- Helper return shape: tuple `(value, meta)` vs sibling meta dict (build detail).
- Reviewer surface: new segmented metric vs extend existing segmentation (build detail).
