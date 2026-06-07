# Research Liquidity Hard Filter Design

Date: 2026-06-07

## Problem

BABY-USDT exposed a portfolio-selection bug: the research layer allowed a low-liquidity symbol into the initial candidate pool, then Judge found a live long setup and Executor opened it. The position moved against the entry quickly enough that risk control took over and force-closed it. The later Censor comments correctly identified liquidity risk, but that was too late and too dependent on LLM judgment.

Low liquidity is not a subjective research disagreement. It is a deterministic live-trading safety constraint and must be enforced before LLM initial selection.

## Goal

Prevent low-liquidity symbols from appearing in the initial research candidate list that `ResearchSynthesizer` sees.

The filter should remove symbols before:

- `ResearchSynthesizer._build_research_summary()`
- the initial LLM synthesis prompt
- `research_preliminary`
- `research_result`
- `SymbolRouter` active-symbol rotation

## Non-Goals

- Do not loosen Judge, RiskGuard, or Executor gates.
- Do not rely on Censor or prompt wording as the primary liquidity filter.
- Do not change position sizing or leverage logic in this change.
- Do not backfill or edit historical BABY records.

## Design

Implement the hard filter in `MarketScanner`, after enrichment has fetched `open_interest_usd` and before `research_market_data` is published.

The scanner already applies a coarse `min_volume_24h` filter before enrichment. That stays as a cheap first pass. The new filter is the live-trading liquidity gate and uses both 24h quote volume and open interest:

- `volume_24h >= research_min_volume_24h_usdt`
- `open_interest_usd >= research_min_open_interest_usd`

Default values:

- `research_min_volume_24h_usdt = 50_000_000`
- `research_min_open_interest_usd = 10_000_000`

If `open_interest_usd` is missing or cannot be fetched, fail closed and remove the symbol. The reasoning is operational: if the system cannot prove depth, it should not allocate live candidate slots to that symbol.

The filter should emit a summary in the published payload:

```json
{
  "liquidity_filter": {
    "min_volume_24h_usdt": 50000000,
    "min_open_interest_usd": 10000000,
    "removed": 7,
    "kept": 43,
    "examples": [
      {
        "symbol": "BABY-USDT",
        "volume_24h": 62000000,
        "open_interest_usd": 2000000,
        "reason": "open_interest_below_min"
      }
    ]
  }
}
```

Examples should be capped to keep payloads small. The logs should include the same aggregate count and a short sample of removed symbols.

## Config

Add config loader defaults and environment mappings:

- `RESEARCH_MIN_VOLUME_24H_USDT` -> `research_min_volume_24h_usdt`
- `RESEARCH_MIN_OPEN_INTEREST_USD` -> `research_min_open_interest_usd`

These are separate from the existing scanner coarse filter so operations can tune the live safety gate without changing the early exchange scan breadth.

## Data Flow

Current:

```text
fetch_tickers -> coarse volume filter -> top_n -> age filter -> enrichment
  -> publish research_market_data.candidates
  -> Synthesizer initial LLM selection
```

New:

```text
fetch_tickers -> coarse volume filter -> top_n -> age filter -> enrichment
  -> liquidity hard filter
  -> publish research_market_data.candidates + liquidity_filter summary
  -> Synthesizer initial LLM selection
```

## Edge Cases

- If all symbols are filtered out, publish `research_market_data` with an empty `candidates` list and a `liquidity_filter` summary. Existing Synthesizer behavior already avoids publishing preliminary output when candidates are empty.
- Degraded `last_good` market data should preserve the already-filtered candidate list from the last successful scan.
- If the OKX open-interest endpoint fails for many symbols, those symbols are removed rather than passed to LLM. This is intentionally conservative for live trading.
- `volume_24h` calculation continues to use the existing quote-volume fallback logic.

## Testing

Use TDD and add focused tests around `MarketScanner`:

- A symbol with high 24h volume but low open interest is removed before `research_market_data`.
- A symbol with sufficient volume and open interest remains.
- A symbol with missing `open_interest_usd` is removed.
- The published payload includes `liquidity_filter` counts and example reasons.
- The fallback `last_good` path reuses the already-filtered candidates and carries the previous filter summary when appropriate.

Run at minimum:

```bash
python3 -m pytest -q test_research_market_scanner_failover.py
python3 -m pytest -q
```

## Rollout

This change takes effect on the next research cycle after restart. It will reduce candidate diversity but should reduce low-depth forced exits and noisy symbol rotation. If too few candidates remain, tune the two new env vars rather than bypassing Censor or Judge.
