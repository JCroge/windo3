## 1. Liquidity Filter Implementation

- [x] 1.1 Add `_liquidity_rejection_reason(candidate)` returning `None` / `volume_below_min` / `open_interest_missing` / `open_interest_below_min`, checked volume-then-OI.
- [x] 1.2 Add `_apply_liquidity_hard_filter(candidates) -> (kept, summary)` applied after enrichment and before `publish("research_market_data", ...)`.
- [x] 1.3 Fail closed: remove candidates with missing/None `open_interest_usd`.
- [x] 1.4 Emit a `liquidity_filter` summary (thresholds, removed, kept, capped examples) in the published payload and a scan log line with the removed count and sample.

## 2. Degraded Fallback Parity

- [x] 2.1 Store `_last_good_liquidity_filter` in `_remember_last_good`.
- [x] 2.2 Reuse already-filtered `last_good` candidates in `_publish_degraded_market_data` and attach the stored `liquidity_filter` summary.

## 3. Config

- [x] 3.1 Add `research_min_volume_24h_usdt` (default 50M) and `research_min_open_interest_usd` (default 10M) to `DEFAULTS`.
- [x] 3.2 Add both keys to `HARD_LIMITS` with `(0.0, 10_000_000_000.0)` bounds.
- [x] 3.3 Add `RESEARCH_MIN_VOLUME_24H_USDT` / `RESEARCH_MIN_OPEN_INTEREST_USD` env overrides.

## 4. Verification

- [x] 4.1 Add targeted `MarketScanner` tests: high-volume/low-OI removed, sufficient volume+OI kept, missing OI removed, payload carries `liquidity_filter` counts and reasons.
- [x] 4.2 Add degraded `last_good` test proving the already-filtered candidates and filter summary are carried.
- [x] 4.3 Run the full pytest baseline or document any environment-limited exclusions.
