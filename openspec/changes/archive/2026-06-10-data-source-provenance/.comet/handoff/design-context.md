# Comet Design Handoff

- Change: data-source-provenance
- Phase: design
- Mode: compact
- Context hash: 9315f4de310883326fbf6169609e2eb99ed9984a11ff37e4d35c1bd65e344977

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/data-source-provenance/proposal.md

- Source: openspec/changes/data-source-provenance/proposal.md
- Lines: 1-32
- SHA256: fe64173b7caf8da71cb7da486d048a2cf94b5884113076b32d8bcf447daec7a6

```md
## Why

The trading system fuses nine market-data dimensions, but several of the highest-influence cross-source signals are presented to downstream agents stripped of their provenance. Open interest, taker buy/sell ratio, and the long/short account ratio are all fetched from **Binance** (`fapi.binance.com`) and fed into an **OKX**-primary trading system, yet nothing downstream records that cross-exchange origin. Worse, the Binance taker and long/short feeds are sampled at `period=1h&limit=1` — the data can be up to an hour stale — and the API item's own timestamp is discarded, so a one-hour-old foreign-exchange sample is presented identically to a fresh OKX orderbook. Reviewer cannot bucket outcomes by data quality, and there is no machine-readable basis for treating a stale, cross-exchange signal as weaker than a fresh native one.

The freshness data already exists in the raw API responses; this change captures what is currently thrown away, attaches source/freshness/confidence per dimension, and propagates it so consumers can finally see it.

### Evidence from exploration

- **Discarded timestamps:** `_fetch_taker_ratio` / `_fetch_long_short_ratio` use Binance `period=1h&limit=1`; each item carries a `timestamp` that is dropped. `_fetch_oi_delta` uses `period=5m`. Freshness is recoverable, not invented.
- **Hidden cross-exchange basis:** OI, taker, and long/short come from Binance; big_trades and funding from OKX. Downstream sees only flat values, never the origin.
- **Propagation gap (critical):** Judge reads `tech_analysis` (the derived signals from `tech_analyst`), not raw `market_data`. `tech_analyst` collapses raw fields (`oi_data`, `taker_ratio`, `long_short_account`) into derived signals. Provenance added only at the collector would be lost at that collapse — it must ride through `tech_analyst` into `tech_analysis` to reach Judge/Reviewer.
- **Existing precedent:** news ticker mentions already carry `confidence/match_rule/source/freshness_sec` via `utils/symbol_mentions.py` (third-pass audit FR-3D). This change extends the same pattern to the market dimensions.

## What Changes

- Add a reusable provenance triple — `source`, `freshness_sec`, `confidence` — for each cross-source market dimension, modeled on the `symbol_mentions` precedent.
- In `multi_data_collector`, capture `source` (e.g. `binance_fapi`, `okx`) and `freshness_sec` (derived from the API item timestamp that is currently discarded) for `oi_data`, `taker_ratio`, `long_short_account`, `big_trades`, and `funding_rate`; derive `confidence` from freshness + cross-exchange origin + degraded state.
- Emit provenance as a **non-breaking parallel `provenance` block** in the `market_data` payload — existing flat field values are unchanged, so current consumers are unaffected.
- Propagate the relevant provenance through `tech_analyst` into the `tech_analysis` payload, so Judge and Reviewer can actually observe it (not lost at the analysis collapse).
- Enable Reviewer to bucket/segment outcomes by data-source quality (source / freshness / confidence).

## Capabilities

### New Capabilities
- `data-source-provenance`: A provenance contract (source/freshness_sec/confidence) for cross-source market dimensions, captured at the collector, propagated through tech analysis, and consumable by Reviewer.

## Impact

- **Code:** `agents/trading/multi_data_collector.py` (capture provenance in `_full_collect` and the `_fetch_*` helpers; emit `provenance` block); `agents/trading/tech_analyst.py` (forward provenance into `tech_analysis`); `agents/trading/reviewer.py` (optional bucketing by provenance); possibly a small `utils/` helper for confidence derivation, mirroring `utils/symbol_mentions.py`.
- **Contracts:** `market_data` and `tech_analysis` gain an additive `provenance` block; flat field values unchanged (non-breaking; legacy payloads without `provenance` treated as unknown-source/zero-confidence by readers).
- **Out of scope (separate follow-up change):** Judge behavioral down-weighting of stale/cross-exchange/low-confidence signals. That is a strategy change requiring event-backtest validation per the CLAUDE.md red line and depends on this change first accumulating provenance data. This change is observability + propagation only — Judge decision behavior is unchanged.
- **Not touched:** live executor, order placement, risk gates.
```

## openspec/changes/data-source-provenance/design.md

- Source: openspec/changes/data-source-provenance/design.md
- Lines: 1-79
- SHA256: f430fe342cf65995c0bc5341da1ae3779379bd97ccabd3aba839e07dab6178dd

```md
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
```

## openspec/changes/data-source-provenance/tasks.md

- Source: openspec/changes/data-source-provenance/tasks.md
- Lines: 1-28
- SHA256: 56d5ead2415c66739ca5dfd8eff599323ce0540f57cce69c9fbd4b402b36a576

```md
## 1. Provenance helper

- [ ] 1.1 Create `utils/data_provenance.py` with a `derive_confidence(source, freshness_sec, *, native_venue='okx', period_sec=None, degraded=False) -> float` (0.0–1.0; monotonic decay with staleness, cross-exchange penalty, 0 when degraded/missing) and a small provenance-entry builder.
- [ ] 1.2 Unit tests: fresh-native high; stale cross-exchange low; degraded → 0; single-function consistency across dimensions.

## 2. Collector capture + emit

- [ ] 2.1 Extend `_fetch_oi_delta`, `_fetch_taker_ratio`, `_fetch_long_short_ratio` (Binance) and `_fetch_big_trades` (OKX) to surface `source` + the underlying item timestamp (currently discarded), without changing the existing value-dict contents.
- [ ] 2.2 Capture `source`/item-ts for `funding_rate` (OKX via ccxt).
- [ ] 2.3 In `_full_collect`, assemble a `provenance` block keyed by dimension (`source`, `freshness_sec` from item ts with fetch-time fallback, `confidence` via `derive_confidence`) and add it to the `market_data` payload; flat values unchanged.
- [ ] 2.4 Tests: provenance present + shape; flat values byte-identical to pre-change; hourly feed reports realistic `freshness_sec` (≈ up to 3600); cross-exchange `source=binance_fapi`; missing dimension → confidence 0 / consistent absence; legacy fallback to fetch-time when no item ts.

## 3. Propagate through tech_analyst

- [ ] 3.1 `tech_analyst` reads `market_data["provenance"]` and forwards the entries for dimensions it used into `tech_analysis["provenance"]` (pass-through, no re-derivation).
- [ ] 3.2 Tests: provenance survives the analysis collapse into `tech_analysis`; legacy `market_data` without provenance → no crash, no provenance block; a consumer reading only `tech_analysis` can observe provenance.

## 4. Reviewer bucketing

- [ ] 4.1 Add Reviewer segmentation by a provenance attribute (confidence band and/or native-vs-cross-exchange), read-only.
- [ ] 4.2 Tests: Reviewer aggregates split by at least one provenance dimension; tolerates missing provenance.

## 5. Observability-only guard + verification

- [ ] 5.1 Test/guard that Judge `trade_decision` output is unchanged by the presence of the provenance block (no gating/ranking/veto driven by provenance this change).
- [ ] 5.2 Run targeted suite (provenance helper, collector, tech_analyst propagation, reviewer, judge-unchanged).
- [ ] 5.3 Run full `python3 -m pytest -q`; record new baseline.
- [ ] 5.4 Compileall check.
```

## openspec/changes/data-source-provenance/specs/data-source-provenance/spec.md

- Source: openspec/changes/data-source-provenance/specs/data-source-provenance/spec.md
- Lines: 1-102
- SHA256: 7941d49ecef684bd08bf61a820d5e9e0e67211ccd3937d1febeae55ba09616ae

[TRUNCATED]

```md
## ADDED Requirements

### Requirement: Cross-source market dimensions SHALL carry a provenance triple

The data collector SHALL attach a provenance triple — `source` (string origin identifier), `freshness_sec` (age in seconds of the underlying datum), and `confidence` (0.0–1.0) — to each cross-source market dimension it publishes: `oi_data`, `taker_ratio`, `long_short_account`, `big_trades`, and `funding_rate`. The provenance SHALL be emitted as a parallel `provenance` block in the `market_data` payload, keyed by dimension name. The existing flat field values SHALL remain unchanged.

#### Scenario: Provenance present for a fetched dimension
- **WHEN** the collector successfully fetches `taker_ratio` from Binance
- **THEN** `market_data.provenance["taker_ratio"]` SHALL include `source`, `freshness_sec`, and `confidence`
- **AND** `market_data.taker_ratio` SHALL still contain the same flat fields (`buy_sell_ratio`, etc.) as before this change

#### Scenario: Missing dimension yields zero-confidence provenance
- **WHEN** a dimension fetch fails or returns empty
- **THEN** its `provenance` entry SHALL report `confidence = 0.0` (or the dimension SHALL be absent from both the value map and the provenance block, consistently)
- **AND** no flat field value SHALL be fabricated

### Requirement: Source SHALL identify the originating exchange/feed

Each provenance `source` SHALL identify the actual origin feed (e.g. `binance_fapi`, `okx`), so a cross-exchange origin (Binance data in an OKX-primary system) is visible downstream rather than implicit.

#### Scenario: Cross-exchange origin is explicit
- **WHEN** `oi_data`, `taker_ratio`, or `long_short_account` is fetched from Binance fapi
- **THEN** its provenance `source` SHALL be `binance_fapi` (not the OKX trading venue)

#### Scenario: Native origin is labeled
- **WHEN** `big_trades` is fetched from OKX
- **THEN** its provenance `source` SHALL be `okx`

### Requirement: freshness_sec SHALL be derived from the datum timestamp, not fetch time alone

`freshness_sec` SHALL reflect the age of the underlying datum, derived from the API item's own timestamp where available (the timestamp currently discarded by the fetchers), not merely the time the HTTP request completed. For periodic feeds (e.g. Binance `period=1h`), `freshness_sec` SHALL account for the sampling period so an up-to-one-hour-old sample is reported as such.

#### Scenario: Hourly-sampled feed reports realistic age
- **WHEN** `taker_ratio` is fetched from a `period=1h&limit=1` Binance endpoint whose item timestamp is 50 minutes old
- **THEN** `provenance["taker_ratio"].freshness_sec` SHALL be approximately 3000 (≈50 min), not ≈0

#### Scenario: Missing item timestamp falls back to fetch time
- **WHEN** an API item carries no usable timestamp
- **THEN** `freshness_sec` SHALL fall back to time-since-fetch and the source SHALL still be recorded (fail-safe, never crash)

### Requirement: confidence SHALL be derived from freshness, cross-exchange origin, and degraded state

`confidence` SHALL be a deterministic function of `freshness_sec`, whether the source is cross-exchange relative to the trading venue, and the collector's degraded state — decaying toward 0 as data becomes stale, cross-venue, or degraded. The derivation SHALL be centralized in a single function (mirroring `utils/symbol_mentions.py`) so all dimensions score consistently.

#### Scenario: Fresh native data scores high
- **WHEN** a dimension is fresh and from the native trading venue
- **THEN** its `confidence` SHALL be high (near 1.0)

#### Scenario: Stale cross-exchange data scores low
- **WHEN** a dimension is an hour-old Binance sample feeding an OKX system
- **THEN** its `confidence` SHALL be materially reduced relative to a fresh native datum

#### Scenario: Single derivation function
- **WHEN** confidence is computed for any dimension
- **THEN** all dimensions SHALL route through the same confidence-derivation function (no per-call-site bespoke scoring)

### Requirement: Provenance SHALL propagate through tech analysis to downstream consumers

Because Judge and Reviewer consume the derived `tech_analysis` payload rather than raw `market_data`, `tech_analyst` SHALL forward the relevant provenance into `tech_analysis` so it survives the collapse of raw dimensions into derived signals. Provenance SHALL NOT be lost at the analysis layer.

#### Scenario: Provenance survives the analysis collapse
- **WHEN** `tech_analyst` derives signals from `oi_data` / `taker_ratio` / `long_short_account`
- **THEN** the published `tech_analysis` SHALL include a `provenance` block carrying source/freshness_sec/confidence for those dimensions
- **AND** a Judge/Reviewer reading only `tech_analysis` SHALL be able to observe the provenance

#### Scenario: Legacy tech_analysis without provenance is tolerated
- **WHEN** a consumer reads a `tech_analysis` payload that predates this change (no `provenance` block)
- **THEN** the consumer SHALL treat provenance as unknown (e.g. zero confidence / unknown source) and continue without error

### Requirement: A per-decision provenance summary SHALL reach trade records via attribution

Because the Reviewer consumes trade outcome records (`execution_result` → trade history), not `tech_analysis`, the Judge SHALL attach a per-decision provenance summary to `trade_decision.attribution` so the data-source quality at decision time travels with the trade. This attribution write is metadata-only and SHALL NOT gate, rank, or veto any decision (consistent with the observability-only scope). The summary SHALL capture at least the weakest contributing-signal confidence and whether any contributing signal was cross-exchange.

#### Scenario: Provenance summary attached to a decision
- **WHEN** the Judge produces a `trade_decision` from a `tech_analysis` payload that carries a `provenance` block
- **THEN** `trade_decision.attribution` SHALL include a provenance summary with at least a weakest-signal confidence and a cross-exchange flag
- **AND** the decision action/ranking SHALL be identical to what it would be without the provenance summary (metadata-only)

#### Scenario: Missing provenance yields an unknown summary
- **WHEN** the Judge produces a decision from a `tech_analysis` payload with no `provenance` block (legacy)
```

Full source: openspec/changes/data-source-provenance/specs/data-source-provenance/spec.md

