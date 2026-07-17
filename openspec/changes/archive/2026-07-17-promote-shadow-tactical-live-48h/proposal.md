## Why

Recent Tactical shadow records show strong positive PnL, but the user does not want another Tactical admission-policy tweak. The required experiment is a 24-hour live mirror of the existing shadow Tactical event stream: when the shadow ledger records a Tactical plan, a separate live sidecar should place the same symbol/side/entry/SL/TP/leverage/hold-profile trade without routing through Main Judge, CandidateRanker, RR/EV, cost, or slot gates.

The experiment must not disrupt the running Main agent logic. The user accepts same-account deployment, so the design must make same-account coupling explicit and guard the dangerous paths: Main must not backfill sidecar-owned positions, Main must not cancel or migrate sidecar-owned SL algos, and the sidecar must keep mechanical hard limits active while bypassing strategy admission gates.

## What Changes

- Add a 24-hour Shadow Tactical live mirror sidecar that tails `data/rejected_signal_events.jsonl`.
- Mirror only new `rejected_plan_created` records whose record payload is Tactical (`track=tactical` or `exit_profile=tactical_v1`).
- Construct a live execution plan directly from the shadow record fields: `symbol`, `side`, `entry_price`, `stop_loss`, `take_profit`, `leverage`, `tactical_max_hold_minutes`, `exit_profile`, `tactical_source`, and attribution fields.
- Bypass strategy admission gates for the mirror: no Main Judge rerun, no CandidateRanker slot selection, no RR/EV/cost promotion check, no Tactical quality/loss-streak/daily-loss admission gate.
- Keep mechanical execution integrity: valid symbol/side/price fields, max trade amount, effective balance cap, free-balance check, exchange amount precision/min-size checks, orderbook slippage/depth check, OKX posMode fail-closed, and protective SL creation/verification.
- Persist sidecar state and audit files separately from Main state, and publish sidecar ownership so Main can ignore sidecar-owned account objects.
- Time-box the process to 24 hours and provide an explicit stop procedure for sidecar-owned orders/positions.

## Capabilities

### New Capabilities

- `tactical-exit-track`: add an operational Shadow Tactical live mirror sidecar for a 24-hour exact-shadow experiment.

### Modified Capabilities

None. The existing Main Tactical admission path remains unchanged for this experiment.

## Impact

- Affected code: new sidecar/runner code, executor extension for sidecar-owned plan opens if needed, focused tests, and a cloud run command/service for the sidecar.
- Affected systems: live OKX execution under the selected API credentials, shadow counterfactual ledger, sidecar audit ledger, sidecar state files.
- Main process impact target: no changes to Main Judge/Ranker admission settings. Same-account mode requires a small Main executor safety patch so account-level position/algo sync does not take ownership of sidecar objects.
