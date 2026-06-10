## Why

BABY-USDT exposed a portfolio-selection bug: the research layer allowed a low-liquidity symbol into the initial candidate pool, Judge found a live long setup, and Executor opened it. The position moved against the entry fast enough that risk control force-closed it. The later Censor comments correctly flagged liquidity risk, but that was too late and too dependent on LLM judgment.

Low liquidity is not a subjective research disagreement. It is a deterministic live-trading safety constraint and must be enforced before LLM initial selection, not relitigated downstream by Censor or prompt wording.

## What Changes

- Add a deterministic liquidity hard filter in `MarketScanner`, applied after enrichment fetches `open_interest_usd` and before `research_market_data` is published.
- Gate candidates on both 24h quote volume (`research_min_volume_24h_usdt`, default 50M) and open interest (`research_min_open_interest_usd`, default 10M); a symbol must pass both.
- Fail closed: a symbol with missing or unfetchable `open_interest_usd` is removed rather than passed to the LLM.
- Emit a `liquidity_filter` summary (thresholds, removed/kept counts, capped example reasons) in the published `research_market_data` payload and logs.
- Carry the filter summary through the degraded `last_good` fallback so the already-filtered candidate list and its provenance survive a failed scan.
- Add config loader defaults, hard limits, and `RESEARCH_MIN_VOLUME_24H_USDT` / `RESEARCH_MIN_OPEN_INTEREST_USD` env overrides so operations can tune the live safety gate independently of the coarse scanner volume filter.

## Capabilities

### New Capabilities
- `research-liquidity-filter`: Deterministic pre-LLM liquidity hard filter that removes low-depth symbols from the research candidate pool before `ResearchSynthesizer` sees them.

## Impact

- Affected code: `agents/research/market_scanner.py`, `utils/config_loader.py`.
- Affected tests: `test_research_market_scanner_failover.py`.
- Affected docs: design doc `docs/superpowers/specs/2026-06-07-research-liquidity-hard-filter-design.md`.
- No change to Judge, RiskGuard, Executor, Censor, position sizing, or leverage logic. Reduces candidate diversity by design; if too few candidates remain, tune the two env vars rather than bypassing downstream gates.
