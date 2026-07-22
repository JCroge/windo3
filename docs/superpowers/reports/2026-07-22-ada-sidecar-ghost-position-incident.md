# 2026-07-22 ADA Sidecar Ghost Position Incident

## Status

Open for root-cause follow-up. Immediate operator decision: stop cloud bot processes only, leave OKX positions/orders untouched, then manually place SL/TP protection from OKX.

## Incident Summary

At around 2026-07-22 06:37-06:39 CST, manual ADA-USDT-SWAP TP/SL algo orders were canceled by the running cloud system after the sidecar ADA position lost local position metadata.

Observed state before process shutdown:

- OKX ADA-USDT-SWAP long position remained open: 57.6 contracts, average entry about 0.173633.
- `data/shadow_tactical_live_owners.json` still had two open ADA sidecar owner rows:
  - `025ba541`, ADA-USDT-SWAP long, 100 USDT, `sl_algo_id=3763589842492735488`
  - `aaaa4c74`, ADA-USDT-SWAP long, 100 USDT, `sl_algo_id=3763592600969035776`
- `data/shadow_tactical_live_positions.json` was empty.
- OKX pending algo orders for ADA were empty after cleanup.
- Sidecar audit repeatedly emitted `monitor_skipped_unproven` for those ADA owners with `exchange_state=present`.
- Main logs repeatedly emitted `ADA-USDT-SWAP ignored as sidecar-owned`, but also removed manual algos as residuals:
  - `2026-07-21 22:37:38 ... [Migrate] ADA-USDT-SWAP 无本地仓位,撤残留 algo 3763835495831584768 (ordType=oco)`
  - `2026-07-21 22:39:10 ... [Migrate] ADA-USDT-SWAP 无本地仓位,撤残留 algo 3763839065956040704 (ordType=conditional)`

## Working Assessment

This is a sidecar/main ownership and local-metadata split-brain:

- Sidecar ownership ledger still claims ADA ownership.
- Sidecar executable position metadata is missing, so sidecar monitor cannot safely apply Tactical TP/SL/max-hold logic.
- Main avoids taking over because ADA is sidecar-owned.
- Main migration cleanup still treats manually placed ADA algos as residual orders when it sees no local main position, so manual TP/SL protection can be canceled.

The result is effectively an unmanaged/ghost ADA position unless the bot processes are stopped or the cleanup/reconcile logic is fixed.

## Follow-Up Items

- Reproduce why `shadow_tactical_live_positions.json` lost ADA entries while owners and lifecycle stayed open.
- Fix main migration cleanup so sidecar-owned symbols are never used as a reason to cancel user/foreign/sidecar algos when main has no local position.
- Fix sidecar monitor to reconcile `owners + lifecycle + exchange position` into a proven local sidecar position or to fail closed without leaving a naked position.
- Add an operational guard that alerts and/or halts when `owners.open > 0`, exchange position is present, but sidecar positions are empty and pending TP/SL is absent.
- Add a test around manual reduce-only OCO/conditional protection surviving while a symbol is sidecar-owned.
