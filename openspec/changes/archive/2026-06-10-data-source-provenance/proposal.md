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
