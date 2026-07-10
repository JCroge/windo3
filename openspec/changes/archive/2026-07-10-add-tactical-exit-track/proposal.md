## Why

The current live exit lifecycle is optimized for clean trend continuation: TP1 is typically near 2R, pre-TP protection only starts at 0.8R/1.0R, and weak/mixed trades can give back early floating profit or reach risk forced exits before the original thesis is invalidated. Recent WLD behavior showed that the entry system can quickly downgrade a symbol to hold/blocked while the open position still waits for the original trend TP/SL model.

This change introduces a separate Tactical track so weak or mixed-environment opportunities can use a shorter-horizon, higher-win-rate exit model without corrupting Main Trend R:R, EV, or review metrics.

## What Changes

- Add a new Tactical trading/exit track distinct from Main Trend.
- Classify candidate trades into Main Trend or Tactical before opening:
  - Main Trend keeps the current trend-runner exit model when HTF and daily bias agree with the trade and 15m is not opposing it.
  - Tactical handles main-strategy signals that are valid directionally but fail Main Trend protection, plus a narrow subset of structure-backed hold/reject signals.
- Add Tactical-specific R:R and EV calculation based on the Tactical exit profile instead of reusing Main Trend 2R/3R assumptions.
- Add Tactical-specific execution lifecycle:
  - structure-bounded stop loss,
  - dynamic TP and partial exits,
  - thesis-health based SL movement and invalidation exits,
  - maximum hold time and no-add policy.
- Add Tactical-specific risk controls:
  - independent daily loss limit,
  - dynamic concurrency cap,
  - cooldowns,
  - quality and execution-failure circuit breakers.
- Add Tactical-specific observability and review metrics so Tactical performance does not pollute Main Trend statistics.
- Preserve current OKX owner model for the first version: exchange-side protective SL only; TP remains locally owned.

## Capabilities

### New Capabilities

- `tactical-exit-track`: Defines Tactical candidate selection, R:R/EV calculation, exit lifecycle, risk controls, execution ownership, and independent performance accounting.

### Modified Capabilities

- `ladder-weighted-rr`: Main Trend R:R assumptions must remain isolated from Tactical R:R assumptions so candidate gates do not overstate expected payoff.
- `low-rr-early-trailing`: Tactical supersedes ad-hoc early protection behavior for non-Main opportunities and must coexist without changing low-RR slot semantics unexpectedly.
- `short-main-path-risk-guard`: Short-side guard outcomes must be available to Tactical as hard vetoes or downgrade signals; Tactical must not bypass explicit regime/15m short blocks.
- `counterfactual-pnl`: Replay and counterfactual accounting must distinguish Tactical vs Main outcomes to measure incremental value.
- `pnl-resolution-bus-events`: Execution and close events must carry track/profile metadata for final PnL attribution.

## Impact

- Affected code areas:
  - `agents/trading/judge.py` candidate classification, R:R/EV calculation, dispatch metadata.
  - `executor.py` and `agents/trading/executor.py` Tactical exit lifecycle, partial reduction, SL movement, close reasons.
  - `agents/trading/portfolio_risk_guard.py` Tactical risk limits and circuit breakers.
  - `agents/trading/position_analyst.py` thesis-health integration or separation from Tactical decisions.
  - Reviewer / ledger / resolver event consumers for independent Tactical metrics.
  - Replay and counterfactual drivers for Tactical-vs-Main comparisons.
- No exchange dependency change in the first version.
- No change to Main Trend TP/SL semantics except explicit isolation from Tactical.
- Requires tests for candidate classification, Tactical R:R honesty, exit-state transitions, circuit breakers, and event attribution.
