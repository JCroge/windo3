# Verification Report: research-liquidity-hard-filter

Date: 2026-06-10 (retroactive closeout; change shipped 2026-06-07)
Change: `2026-06-07-research-liquidity-hard-filter`
Ship commit: `2047187` (`feat: filter low-liquidity research candidates`)
Base ref: `2d287e1597b78096401757f8766da41e53cbd9b3`

## Summary

| Dimension | Status |
|---|---|
| Completeness | 11/11 tasks done; 5/5 spec requirements implemented |
| Correctness | 5/5 requirements covered; all declared test scenarios covered |
| Coherence | Design doc, OpenSpec design.md, delta spec, master spec, and code aligned |

Full pytest baseline: `1010 passed / 4 deselected / 1 warning` (152.45s). Targeted suite `test_research_market_scanner_failover.py` 8/8 PASS (2.21s). Compileall PASS.

> Note: This change shipped 2026-06-07 directly on `main` as commit `2047187`, outside the normal OpenSpec/comet flow. The OpenSpec change artifacts, master spec, and this report were reconstructed and verified 2026-06-10. No code was modified during closeout; verification is against the already-merged implementation.

## Completeness

- `tasks.md`: 11/11 marked `[x]`.
- Delta spec requirements mapped to code (`agents/research/market_scanner.py`, `utils/config_loader.py`):
  1. Pre-LLM Liquidity Hard Filter — `_apply_liquidity_hard_filter` at `market_scanner.py:181`, invoked at `market_scanner.py:127` after enrichment and before `publish("research_market_data", ...)` at `market_scanner.py:131`.
  2. Fail-Closed on Missing Depth — `_liquidity_rejection_reason` at `market_scanner.py:169`; `open_interest_missing` when `None` (`market_scanner.py:175-176`), volume gate checked first (`market_scanner.py:171`).
  3. Liquidity Filter Observability — summary dict at `market_scanner.py:199-205`, capped examples (≤5) at `market_scanner.py:189-195`, payload field at `market_scanner.py:135`.
  4. Degraded Fallback Preserves Filtering — `_last_good_liquidity_filter` stored at `market_scanner.py:222`, reused at `market_scanner.py:230`, attached to degraded payload at `market_scanner.py:255-256`.
  5. Operator-Tunable Thresholds — defaults `config_loader.py:166-167`, hard limits `config_loader.py:70-71`, env overrides `config_loader.py:293-294`.

## Correctness

| Scenario | Evidence |
|---|---|
| High volume but low OI removed | `test_research_market_scanner_failover.py::test_market_scanner_filters_low_liquidity_before_publish` (removed=3, reason `open_interest_below_min`) |
| Sufficient volume + OI kept | same test asserts `liquidity_filter.kept == 1` |
| Missing OI removed (fail closed) | same test asserts an `open_interest_missing` example reason |
| Below-min volume removed first | `_liquidity_rejection_reason` order (`market_scanner.py:171` before `:174`), reason `volume_below_min` |
| Payload carries filter summary | same test asserts `min_volume_24h_usdt == 50_000_000`, `min_open_interest_usd == 10_000_000`, `removed`, `kept`, `examples` |
| Degraded payload carries filtered last_good + summary | `test_market_scanner_degraded_payload_carries_liquidity_filter` (both first and degraded payloads assert `kept == 1`) |
| Defaults / env overrides resolve | `config_loader.py` DEFAULTS + `_read_env_overrides` mapping; bounded by `HARD_LIMITS` |

## Coherence

- OpenSpec design.md decisions D1–D7 are upheld:
  - D1 single filter function post-enrichment (`_apply_liquidity_hard_filter` / `_liquidity_rejection_reason`).
  - D2 both-signal gate (volume AND open interest).
  - D3 fail closed on missing OI.
  - D4 machine reasons `volume_below_min` / `open_interest_missing` / `open_interest_below_min` in fixed order.
  - D5 observable summary with capped examples.
  - D6 fallback parity via `_last_good_liquidity_filter`.
  - D7 operator-tunable config separate from coarse `min_volume_24h`.
- Superpowers design doc (`docs/superpowers/specs/2026-06-07-research-liquidity-hard-filter-design.md`) Problem/Goal/Design/Edge Cases match the implementation and the OpenSpec delta/master specs.
- Non-goals respected: no changes to Judge, RiskGuard, Executor, Censor, position sizing, or leverage; the coarse pre-enrichment volume filter remains a separate first pass.

## Issues

- CRITICAL: none.
- WARNING: none.
- SUGGESTION (out of scope): the two thresholds (50M volume / 10M OI) are defaults from the BABY incident, not yet tuned against live candidate-pool breadth. Once the filter accumulates `liquidity_filter.removed` samples in production logs, review whether the defaults are too aggressive and reducing candidate diversity more than intended.

## Final Assessment

All checks passed. Implementation is complete, correct, and coherent with the design and specs. Ready for archive.
