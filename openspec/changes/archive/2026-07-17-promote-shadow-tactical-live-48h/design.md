## Context

Tactical shadow records are created by `CounterfactualLedger.record_rejection()` in `data/rejected_signal_events.jsonl`. For each rejected planned signal, the event payload already includes the fields needed to replay a Tactical plan live: symbol, side, entry price, stop loss, take profit levels, leverage, track, exit profile, Tactical source, Tactical gate metadata, and max-hold minutes.

The user explicitly rejected the prior "relax Tactical live admission gates" design. The new requirement is operationally different: mirror the shadow Tactical stream live for 24 hours, "exactly the same as shadow Tactical", without changing or restarting the Main process. This means the experiment should consume the shadow ledger after Main writes it, not route candidates back through Main Judge, CandidateRanker, Tactical RR/EV/cost gates, slot gates, or Tactical circuit admission gates.

The hard constraint is account-level visibility. A separate OS process and separate state files avoid local record pollution, but a sidecar using the same OKX account still shares margin, equity, exchange positions, and the Main executor's `sync_positions()` view. The user accepts same-account deployment. Therefore this design must add explicit owner isolation around Main's account-level sync and migration paths before starting the sidecar.

Code review found existing hard limits that the sidecar can reuse: config hard limits, `RiskManager.check_can_trade()`, `MAX_TRADE_AMOUNT=30`, `EFFECTIVE_BALANCE_CAP=300`, free-balance >= required margin * 1.1, `OrderCapabilities.precheck_order()`, orderbook spread/depth slippage checks, OKX posMode fail-closed, and attached SL verification. These are mechanical execution limits, not strategy admission gates.

## Goals / Non-Goals

**Goals:**
- Run a 24-hour live sidecar that mirrors new shadow Tactical records directly.
- Keep the Main process running unchanged.
- Avoid Main Judge/Ranker/Tactical admission gates entirely for sidecar admission.
- Preserve mechanical hard limits: max trade amount, effective balance cap, free-balance check, order precision/min-size, slippage/depth, OKX posMode, and protective SL verification.
- Keep sidecar state, order tags, logs, audit ledgers, and ownership registry separate from Main files.
- Prevent Main account sync/migration from taking ownership of sidecar-owned positions and SL algos.
- Provide a stop procedure that can cancel/close sidecar-owned exposure.

**Non-Goals:**
- Do not change Main Trend or Main Tactical admission behavior.
- Do not add `TACTICAL_SLOT`, lower RR/EV thresholds, or reconfigure Main `.env` as the primary mechanism.
- Do not attempt to make same-account exchange exposure invisible to Main; OKX positions are account-level.
- Do not backfill old shadow records into live unless explicitly requested.
- Do not make Main strategy accounting treat sidecar positions as Main-owned positions.

## Decisions

### Decision 1: Use a sidecar that tails the shadow event log

The sidecar will tail `data/rejected_signal_events.jsonl`, maintain a durable watermark, and process only new `rejected_plan_created` events. A record is eligible when it is Tactical by payload (`track=tactical` or `exit_profile=tactical_v1`) and has valid symbol, side, entry, SL, TP, and leverage fields.

Rationale: this is the closest live equivalent of the shadow ledger. It uses the actual shadow plan artifacts instead of trying to reconstruct the same outcome through Main policy knobs.

Rejected alternative: relax Tactical RR/EV/cost/slot gates in Main. That still would not be "same as shadow" because it remains subject to ranking, live slot occupancy, hard vetoes, and Main process timing.

### Decision 2: Bypass strategy admission, keep mechanical hard limits

The sidecar will not run Main Judge, CandidateRanker, Tactical RR/EV/cost checks, Tactical loss-streak/daily-loss admission gates, or Tactical slot rules. It will still validate that the record is mechanically executable and will fail closed on malformed plan fields, invalid SL side, missing TP/SL, insufficient free balance, unknown OKX posMode, min-size/precision rejection, orderbook spread/depth failure, or failed protective SL verification.

Rationale: "什么都不管" can apply to strategy admission, but it cannot safely apply to basic exchange correctness. Removing protective SL or OKX mode checks risks naked positions or undefined exchange behavior rather than an honest shadow mirror.

### Decision 3: Keep Main process isolation explicit

The sidecar runs as a separate command/service and writes separate files, for example:

- `data/shadow_tactical_live_state.json`
- `data/shadow_tactical_live_events.jsonl`
- `data/shadow_tactical_live_positions.json`
- `data/shadow_tactical_live_position_lifecycle.json`

It should use a distinct `BOT_INSTANCE_ID`/client order prefix for sidecar orders. The sidecar also writes an ownership registry, for example `data/shadow_tactical_live_owners.json`, keyed by shadow id and containing symbol, side, intended margin, order ids, `clOrdId`, `sl_algo_id`, and `sl_algo_clord_id`.

Rationale: this prevents local file and process interference with Main. It also creates a clear audit path for the 24-hour result.

### Decision 4: Same-account mode requires Main owner-ignore patches

Same-account mode is allowed for this 24-hour run, but it is not safe with the current Main sync/migration behavior. Main `sync_positions()` currently sees every account-level OKX position and can backfill sidecar positions into Main state. Main `_migrate_all_symbols_algos()` also scans account-level pending algos and can cancel SL algos that are not in Main local state.

The mitigation is to add a small ownership interface used by Main executor:

- Main `sync_positions()` consults the sidecar owner registry and skips backfilling sidecar-owned exchange positions.
- Main algo migration treats foreign owner-tag algos as foreign and never cancels, replaces, or adopts them.
- Main close/cleanup paths only cancel known Main-owned algos or algos matching Main's owner prefix.

This keeps Main strategy state from owning sidecar exposure while still acknowledging shared margin/equity at the OKX account level.

### Decision 5: Same-symbol aggregation is the remaining hard account risk

If sidecar and Main trade the same symbol in the same OKX account, OKX may aggregate or net account-level exposure. Code cannot reliably split a single account-level same-symbol position into "Main amount" and "sidecar amount" after the fact. Therefore same-account mode will use a hard guard: do not mirror a shadow record when the OKX account already has a non-sidecar position for the same symbol/side bucket.

Rationale: this is the only practical same-account guard that prevents Main and sidecar position ownership from becoming inseparable. It slightly reduces "exact shadow" coverage only for positions that cannot be represented independently in one account.

### Decision 6: Timebox and stop semantics are part of the runner

The runner takes a 24-hour duration and records `started_at`, `stop_at`, and the last processed shadow event. At stop time it stops accepting new events. A separate stop command should cancel sidecar-owned pending orders and close sidecar-owned open exposure when ownership can be proven by local sidecar state and client order tags.

Rationale: the experiment should not become a permanent parallel strategy accidentally, and the user needs a deterministic way to end the run.

## Risks / Trade-offs

- [Risk] Shadow ledger treats overlapping same-symbol events as independent, while an OKX account may net or merge real positions by symbol/side. -> Mitigation: record one sidecar intent per shadow id and audit any exchange aggregation; exact one-shadow-record to one-position mapping may require subaccount/portfolio isolation.
- [Risk] Same-account sidecar can affect Main through margin, equity, positions, and `sync_positions()`. -> Mitigation: Main owner-ignore for sidecar positions, foreign-owner algo migration safety, and same-symbol account guard.
- [Risk] Bypassing strategy gates can open many low-quality or clustered trades. -> Mitigation: this is the user's requested hypothesis; preserve only mechanical execution fail-closed checks and keep the run to 24 hours.
- [Risk] Protective SL failure creates naked exposure. -> Mitigation: sidecar must fail closed or immediately close if a protective SL cannot be verified after fill.
- [Risk] Duplicate tail processing could duplicate orders. -> Mitigation: durable watermark plus per shadow `id` idempotency state.

## Migration Plan

1. Add a sidecar module/runner that tails `data/rejected_signal_events.jsonl` with a durable watermark.
2. Add a mapper from shadow Tactical record to live execution plan.
3. Add sidecar state/audit/ownership persistence with per-shadow id idempotency.
4. Add supported sidecar state namespace or explicit state path injection so positions, risk state, live ledger, lifecycle, and halt state do not use Main files.
5. Add an executor path or wrapper that can open sidecar plans while reusing mechanical hard limits but bypassing strategy admission gates.
6. Add Main owner-ignore safety for same-account mode: skip sidecar-owned positions during backfill and never cancel/adopt foreign owner-tag SL algos during migration.
7. Add same-account same-symbol guard so sidecar does not create exposure that cannot be split from Main-owned exposure.
8. Add focused tests for event filtering, plan mapping, idempotency/watermark behavior, missing-field fail-closed behavior, hard-limit behavior, owner-ignore sync behavior, and foreign-owner algo migration behavior.
9. Deploy as a separate cloud process for 24 hours after Main safety patch is loaded.
10. On stop, stop accepting new events and run sidecar-owned cancel/close procedure according to sidecar ownership registry.

## Open Questions

- Should the 24-hour run start from "new events after sidecar start" only, or backfill a short recent window of already-created active shadow Tactical records?
