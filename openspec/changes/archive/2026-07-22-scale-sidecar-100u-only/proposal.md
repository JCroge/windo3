# Scale Sidecar To 100u Only

## Why

Cloud CF replay showed the main Judge path performs poorly under a 1000u cap / 100u single-trade expansion, while the sidecar Tactical path remains modestly positive over the full available sample and has a materially smaller drawdown. The next live step should therefore expand only the sidecar path and leave main live sizing unchanged.

## What

- Restart only `scripts/shadow_tactical_live_sidecar.py` with process-local risk overrides:
  - `MAX_TRADE_AMOUNT=100`
  - `EFFECTIVE_BALANCE_CAP=1000`
  - explicit `--size-usdt 100`
  - keep `--max-active 3`
- Leave `run_agents.py` running unchanged.
- Do not edit cloud `.env`, local strategy code, or GitHub remote state.
- Verify the running sidecar process receives the expanded environment and that main still loads `MAX_TRADE_AMOUNT=30` / `EFFECTIVE_BALANCE_CAP=300`.

## Out Of Scope

- No main live expansion.
- No strategy formula changes.
- No new configuration keys.
- No GitHub push.
