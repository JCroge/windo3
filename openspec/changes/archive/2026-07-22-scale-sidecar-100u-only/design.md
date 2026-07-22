# Design

This is an operational tweak, not a code capability change.

The sidecar will be restarted with process-local environment variables and explicit CLI size parameters. `ContractExecutor` loads `.env` with `override=False`, so environment variables exported on the sidecar process take precedence over cloud `.env` without affecting the already running main process. The explicit `--size-usdt 100` is required because argparse reads the default before `.env` is loaded; relying on `.env` alone would leave the sidecar at the script default or current runtime default.

Main isolation is achieved by not modifying `.env` and not restarting `run_agents.py`. Main will keep its existing process environment and configuration values.

Verification is limited to live-safe checks:

- Process list shows the main PID unchanged.
- Sidecar process command includes `--size-usdt 100 --max-active 3`.
- `/proc/<sidecar_pid>/environ` includes `MAX_TRADE_AMOUNT=100` and `EFFECTIVE_BALANCE_CAP=1000`.
- A sidecar-only config load from the process environment resolves cap=1000 and max_trade_amount=100.
- A normal cloud config load still resolves main defaults from `.env` as cap=300 and max_trade_amount=30.
- `python3 scripts/shadow_tactical_live_sidecar.py status` reports no state corruption.
