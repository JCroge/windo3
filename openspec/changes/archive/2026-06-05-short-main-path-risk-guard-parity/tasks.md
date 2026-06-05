## 1. Regression Fixtures

- [x] 1.1 Add NEAR 2026-06-05 09:01 main-path fixture with `daily_bias=bullish`, low range position, deep pre-move, LLM parse-failure/default hold, and expected structural short rejection.
- [x] 1.2 Add NEAR 2026-06-05 09:23 main-path fixture with parsed LLM `hold` / `禁止做空` reasoning and expected structural short rejection plus LLM risk attribution.
- [x] 1.3 Add route-parity fixture proving the same short candidate rejects consistently through main and deferred entry routes.

## 2. Short Gate Implementation

- [x] 2.1 Introduce or normalize a single Judge short risk gate helper covering daily bearish requirement, 24h range position, 12h pre-move, `short_live_min_rsi`, minimum score, and HTF votes.
- [x] 2.2 Route main-path `open_short` candidates through the short gate before ranking/main publication.
- [x] 2.3 Route deferred short candidates through the same gate semantics without duplicating call-site if/else branches.
- [x] 2.4 Preserve existing `RSI <= 30` hard no-short/pending-pullback behavior unchanged.

## 3. Attribution and Observability

- [x] 3.1 Add short gate metadata (`short_gate_version`, `short_gate_decision`, `short_gate_reason`) to accepted and rejected short attribution.
- [x] 3.2 Add LLM short reversal-risk detection for parsed hold reasoning/key factors/risk warnings and expose `llm_short_reversal_risk`.
- [x] 3.3 Ensure LLM parse failure cannot hide structural gate failures.

## 4. Verification

- [x] 4.1 Run targeted Judge unit tests for route parity, NEAR regressions, RSI hard-threshold preservation, and LLM reversal-risk attribution.
- [x] 4.2 Run event replay or equivalent fixture scan for recent risk-text `open_short` decisions to confirm executed NEAR/HYPE-style cases no longer enter `main_direct` when structural gates fail.
- [x] 4.3 Run the full pytest baseline or document any environment-limited exclusions.
