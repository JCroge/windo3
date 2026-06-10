## 1. Provenance helper

- [x] 1.1 Create `utils/data_provenance.py` with `derive_confidence(...)` (0.0–1.0; linear decay to 0 at 2× period, ×0.7 cross-exchange penalty, 0 when degraded) + `provenance_entry(...)` builder.
- [x] 1.2 Unit tests (`tests/test_data_provenance.py`): fresh-native high; stale beyond 2× → 0; cross-exchange penalty; degraded → 0; monotonic; entry shape; fetch-time fallback.

## 2. Collector capture + emit

- [x] 2.1 `_fetch_oi_delta`/`_fetch_taker_ratio`/`_fetch_long_short_ratio` (binance_fapi) + `_fetch_big_trades` (okx) now return `(value, meta)` with `source` + item timestamp (from the previously-discarded API `timestamp`/`ts`); value dicts byte-identical.
- [x] 2.2 `funding_rate` provenance captured from `funding.get('timestamp')` (OKX), None on failure.
- [x] 2.3 `_full_collect` assembles a per-dimension `provenance` block (`source`/`freshness_sec`/`confidence` via `provenance_entry`, per-dim `period_sec`) and adds it to `market_data`; flat values unchanged; failed dim → confidence 0.
- [x] 2.4 Tests (`tests/test_data_provenance_collector.py`): (value, meta) shape per helper; provenance block present + shape; flat values byte-identical; 50-min hourly feed → freshness ≈ 3000; cross-exchange `source=binance_fapi`; missing dim → confidence 0; empty/exception paths.

## 3. Propagate through tech_analyst

- [x] 3.1 `tech_analyst` forwards `payload.get('provenance', {})` into `tech_analysis` (pass-through next to `data_quality`).
- [x] 3.2 Tests (`tests/test_data_provenance_propagation.py`): provenance survives into `tech_analysis`; legacy market_data without provenance → `{}`, no crash.

## 3b. Judge attribution summary (from design Spec Patch)

- [x] 3b.1 `MultiJudge._summarize_provenance(tech)` (quality/weakest_confidence/has_cross_exchange); attached to attribution in both `_build_attribution` and `_rejection_attribution`. Metadata-only — write-only, no gate/rank/veto reads it (verified by grep + decision suites green).

## 4. Reviewer bucketing

- [x] 4.1 `ReviewerAgent._provenance_bucket(attribution)` sets `trade_record['provenance_bucket']` = `{native|cross_exchange}/{low|high}` or `unknown`, beside existing `liquidity_bucket`/`rr_bucket`; read-only.
- [x] 4.2 Tests: cross/low, native/high, missing, unknown-quality buckets; tolerates missing provenance.

## 5. Observability-only guard + verification

- [x] 5.1 Judge decision-unchanged guard: existing Judge decision suites (`test_short_main_path_risk_guard.py`, `test_judge_plan_anchor_fields.py`) stay green + grep proves the provenance summary is write-only (no gating/ranking/veto reads it).
- [x] 5.2 Targeted suite: `test_data_provenance.py` + `test_data_provenance_collector.py` + `test_data_provenance_propagation.py` = 31 passed.
- [x] 5.3 Full `python3 -m pytest -q`: 1066 passed / 4 deselected / 1 warning (was 1035; +31).
- [x] 5.4 Compileall: PASS.
