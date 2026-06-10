## Context

BABY-USDT demonstrated that the research candidate pipeline can surface a low-liquidity symbol all the way to a live position. The coarse `min_volume_24h` filter that runs before enrichment is a cheap breadth control for the exchange scan; it is not a live-trading safety gate and does not consider open interest. By the time Censor raises liquidity concerns, the symbol has already entered `research_preliminary`, `research_result`, and `SymbolRouter` rotation, and Judge may already have a setup.

The canonical project pattern is to enforce deterministic live-trading safety constraints as rule gates, not as LLM judgment. This change applies that pattern to liquidity at the earliest deterministic point where both depth signals are available.

## Goals / Non-Goals

**Goals:**

- Prevent low-liquidity symbols from appearing in the initial candidate list `ResearchSynthesizer` sees.
- Enforce the gate before `_build_research_summary()`, the initial LLM synthesis prompt, `research_preliminary`, `research_result`, and `SymbolRouter` rotation.
- Use both 24h quote volume and open interest, failing closed when depth cannot be proven.
- Make thresholds operator-tunable via config/env without touching the coarse scan breadth.
- Keep the filter observable through a payload/log summary that survives the degraded fallback.

**Non-Goals:**

- Do not loosen Judge, RiskGuard, or Executor gates.
- Do not rely on Censor or prompt wording as the primary liquidity filter.
- Do not change position sizing or leverage logic.
- Do not backfill or edit historical BABY records.

## Decisions

- **D1 — Single filter function, post-enrichment placement.** Implement `_apply_liquidity_hard_filter(candidates) -> (kept, summary)` in `MarketScanner`, called after `_enrich` has populated `open_interest_usd` and before `publish("research_market_data", ...)`. Per-candidate verdict is centralized in `_liquidity_rejection_reason(candidate)` returning `None` (keep) or a machine reason string.
- **D2 — Both-signal gate.** A candidate is kept only if `volume_24h >= research_min_volume_24h_usdt` AND `open_interest_usd >= research_min_open_interest_usd`. The coarse pre-enrichment volume filter stays as a separate cheap first pass.
- **D3 — Fail closed on missing depth.** Missing/None `open_interest_usd` yields `open_interest_missing` and removes the symbol. Rationale: if depth cannot be proven, the symbol must not consume a live candidate slot.
- **D4 — Machine reasons.** `_liquidity_rejection_reason` returns one of `volume_below_min` / `open_interest_missing` / `open_interest_below_min`, checked in that order, so the summary and logs carry a deterministic provenance.
- **D5 — Observable summary, capped examples.** The published payload carries a `liquidity_filter` summary with thresholds, `removed`, `kept`, and up to 5 example `{symbol, volume_24h, open_interest_usd, reason}` entries; the scan log line reports the removed count and a short sample.
- **D6 — Fallback parity.** `_remember_last_good` stores `_last_good_liquidity_filter`; `_publish_degraded_market_data` reuses the already-filtered `last_good` candidates and attaches the stored summary, so the degraded path never reintroduces filtered-out symbols.
- **D7 — Operator-tunable config.** Add `research_min_volume_24h_usdt` / `research_min_open_interest_usd` to `DEFAULTS`, `HARD_LIMITS`, and env overrides (`RESEARCH_MIN_VOLUME_24H_USDT` / `RESEARCH_MIN_OPEN_INTEREST_USD`), separate from the coarse `min_volume_24h`.

## Data Flow

Before:

```text
fetch_tickers -> coarse volume filter -> top_n -> age filter -> enrichment
  -> publish research_market_data.candidates
  -> Synthesizer initial LLM selection
```

After:

```text
fetch_tickers -> coarse volume filter -> top_n -> age filter -> enrichment
  -> liquidity hard filter
  -> publish research_market_data.candidates + liquidity_filter summary
  -> Synthesizer initial LLM selection
```

## Edge Cases

- All symbols filtered out: publish `research_market_data` with empty `candidates` plus the `liquidity_filter` summary. Synthesizer already avoids preliminary output on empty candidates.
- Degraded `last_good`: reuse the previously filtered candidate list and carry the previous filter summary.
- OKX open-interest endpoint failing for many symbols: those symbols are removed (fail closed), intentionally conservative for live trading.
- `volume_24h` continues to use the existing quote-volume fallback logic.

## Risks / Trade-offs

- Reduced candidate diversity. Mitigation: thresholds are env-tunable; the answer to too-few candidates is tuning the two vars, not bypassing Censor/Judge.
- Aggressive fail-closed on missing OI could drop a viable symbol during a transient endpoint failure. Accepted deliberately: live safety outranks breadth, and the degraded path preserves the last good set.
