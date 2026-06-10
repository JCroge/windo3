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
