# Comet Design Handoff

- Change: promote-shadow-tactical-live-48h
- Phase: design
- Mode: compact
- Context hash: de7ab8ced16aeb26d96a83083a2b6bbcd870e05642b7eedcd441bcb83975349a

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/promote-shadow-tactical-live-48h/proposal.md

- Source: openspec/changes/promote-shadow-tactical-live-48h/proposal.md
- Lines: 1-31
- SHA256: 33dfe2072efc00921b4fe03fde4b23812ce3320bbef3c8020d58b5f510fd5260

```md
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
```

## openspec/changes/promote-shadow-tactical-live-48h/design.md

- Source: openspec/changes/promote-shadow-tactical-live-48h/design.md
- Lines: 1-105
- SHA256: 2aeb8d597f86b3ad8106772bf4c3d87d8e6629c33758068c788b8df15ca9341d

[TRUNCATED]

```md
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
```

Full source: openspec/changes/promote-shadow-tactical-live-48h/design.md

## openspec/changes/promote-shadow-tactical-live-48h/tasks.md

- Source: openspec/changes/promote-shadow-tactical-live-48h/tasks.md
- Lines: 1-41
- SHA256: 324e3b93af9d3bd22b0e6a646d2d69e96291af9411ccdb43b688a5ace44ffd49

```md
## 1. Shadow Event Mirror Sidecar

- [ ] 1.1 Add a sidecar runner that tails `data/rejected_signal_events.jsonl` and processes only new events after its start watermark by default.
- [ ] 1.2 Filter eligible events to `rejected_plan_created` records with Tactical identity (`track=tactical` or `exit_profile=tactical_v1`).
- [ ] 1.3 Persist durable sidecar state with `started_at`, `stop_at`, last processed event offset/id, and per-shadow id execution status.
- [ ] 1.4 Write sidecar audit events and sidecar ownership records to separate files.

## 2. Plan Mapping and Execution

- [ ] 2.1 Map shadow record fields directly into a live plan: symbol, side, entry, SL, TP, leverage, Tactical max hold, exit profile, source, and attribution metadata.
- [ ] 2.2 Bypass Main Judge, CandidateRanker, Tactical RR/EV/cost gates, Tactical quality gates, slot gates, and Tactical circuit admission gates for sidecar admission.
- [ ] 2.3 Keep mechanical fail-closed checks for malformed fields, invalid SL side, missing SL/TP, OKX posMode unknown, max trade amount, effective balance cap, min-size/precision rejection, balance shortage, slippage/depth failure, and protective SL verification failure.
- [ ] 2.4 Ensure duplicate shadow ids do not create duplicate live orders.
- [ ] 2.5 Add a sidecar active exposure cap using the configured `MAX_CONCURRENT_POSITIONS` default.

## 3. Isolation and Ownership

- [ ] 3.1 Add a supported sidecar namespace or explicit state paths for sidecar positions, risk state, halt state, live order events, and lifecycle files.
- [ ] 3.2 Use a distinct sidecar `BOT_INSTANCE_ID` or client-order prefix for sidecar-owned orders.
- [ ] 3.3 Add a sidecar ownership registry for shadow id, symbol, side, amount, order id, entry clOrdId, SL algo id, and SL algo clOrdId.
- [ ] 3.4 Patch Main `sync_positions()` so it does not backfill sidecar-owned positions into Main state.
- [ ] 3.5 Patch Main OKX algo migration so it never cancels, replaces, or adopts foreign owner-tag SL algos.
- [ ] 3.6 Add a same-account same-symbol guard to avoid sidecar/Main exposure aggregation that cannot be split.

## 4. Stop and 24-Hour Operation

- [ ] 4.1 Add a 24-hour duration/stop time so the sidecar stops accepting new events automatically.
- [ ] 4.2 Add a stop command/runbook that cancels sidecar-owned pending orders and closes sidecar-owned open exposure when ownership can be proven.
- [ ] 4.3 Deploy the Main owner-ignore safety patch, then start the sidecar as a separate cloud process.
- [ ] 4.4 Verify Main does not adopt sidecar positions or cancel sidecar SL algos during sync.

## 5. Verification

- [ ] 5.1 Add tests for Tactical event filtering and non-Tactical event ignore behavior.
- [ ] 5.2 Add tests for shadow-record-to-live-plan mapping fidelity.
- [ ] 5.3 Add tests for watermark/idempotency duplicate prevention.
- [ ] 5.4 Add tests for missing-field and invalid-SL fail-closed behavior.
- [ ] 5.5 Add tests proving mechanical hard limits are enforced by the sidecar open path.
- [ ] 5.6 Add tests proving Main sync skips sidecar-owned positions.
- [ ] 5.7 Add tests proving Main migration does not cancel or adopt foreign owner-tag SL algos.
- [ ] 5.8 Run OpenSpec validation for `promote-shadow-tactical-live-48h`.
```

## openspec/changes/promote-shadow-tactical-live-48h/specs/tactical-exit-track/spec.md

- Source: openspec/changes/promote-shadow-tactical-live-48h/specs/tactical-exit-track/spec.md
- Lines: 1-111
- SHA256: 3c47118118bff6758a1677743a059c8afa9b5dacf30fd865c603514153f3a66b

[TRUNCATED]

```md
## ADDED Requirements

### Requirement: Shadow Tactical live mirror sidecar
The system SHALL provide a separate sidecar runner that can mirror new Tactical shadow records to live execution for a configured 24-hour window.

#### Scenario: Sidecar mirrors new Tactical shadow record
- **WHEN** the sidecar is running
- **AND** `data/rejected_signal_events.jsonl` receives a new `rejected_plan_created` event
- **AND** the event record has `track=tactical` or `exit_profile=tactical_v1`
- **AND** the record contains a valid symbol, side, entry price, stop loss, take profit, and leverage
- **THEN** the sidecar SHALL create a live execution plan from that record
- **AND** it SHALL record the shadow record id in sidecar state before or atomically with execution bookkeeping

#### Scenario: Sidecar ignores non-Tactical records
- **WHEN** the sidecar reads a `rejected_plan_created` event whose record is not Tactical
- **THEN** it SHALL NOT create a live execution plan
- **AND** it SHALL preserve its watermark so the event is not retried as an error

#### Scenario: Sidecar does not backfill by default
- **WHEN** the sidecar starts without an explicit backfill option
- **THEN** it SHALL process only events written after its start watermark
- **AND** it SHALL NOT place live orders for older shadow records already present in the file

### Requirement: Shadow record fields drive live plan mapping
The sidecar SHALL map the live order plan directly from the shadow record payload. The mapped plan SHALL preserve `symbol`, `side`, `entry_price`, `stop_loss`, `take_profit`, `leverage`, `exit_profile`, `tactical_source`, `tactical_max_hold_minutes`, and available attribution fields.

#### Scenario: Tactical fields are preserved
- **WHEN** a Tactical shadow record is mapped to a live sidecar plan
- **THEN** the live plan SHALL use the record's side, SL, TP list, leverage, Tactical max hold, and exit profile
- **AND** it SHALL include the shadow record id as the entry request id or equivalent audit key

#### Scenario: Missing mechanical fields fail closed
- **WHEN** a Tactical shadow record is missing side, entry price, stop loss, take profit, or leverage
- **THEN** the sidecar SHALL reject that record without placing a live order
- **AND** it SHALL write a sidecar audit event with the missing-field reason

### Requirement: Strategy admission gates are bypassed for sidecar admission
The sidecar SHALL NOT use Main Judge, CandidateRanker, Tactical RR/EV/cost gates, Tactical slot gates, Tactical quality gates, Tactical daily-loss admission gates, or Tactical loss-streak admission gates to decide whether a Tactical shadow record is admitted to the sidecar live experiment.

#### Scenario: Low RR or failed Tactical gate metadata does not block sidecar admission
- **WHEN** a Tactical shadow record contains low RR/EV/cost-gate metadata or a Tactical gate failure reason
- **AND** the record has the mechanical fields required for execution
- **THEN** the sidecar SHALL still attempt to mirror the record live
- **AND** it SHALL include the original gate metadata in sidecar audit output

### Requirement: Mechanical execution checks remain fail-closed
The sidecar SHALL preserve mechanical exchange and protection checks needed to avoid malformed orders, unbounded exposure, or naked positions. These checks include valid SL side, valid symbol/side, configured max trade amount, effective balance cap, amount precision/min-size, free balance, orderbook spread/depth, known OKX position mode, order placement result, and protective stop-loss creation or verification.

#### Scenario: Invalid stop side blocks execution
- **WHEN** a mapped sidecar plan has a stop loss on the wrong side of the entry/live execution price
- **THEN** the sidecar SHALL NOT leave a live position open from that plan
- **AND** it SHALL write a sidecar audit event with `invalid_stop_side`

#### Scenario: Protective SL cannot be verified
- **WHEN** a sidecar entry order fills
- **AND** the protective SL cannot be created or verified
- **THEN** the sidecar SHALL fail closed by closing the sidecar-owned exposure or halting further sidecar opens for that symbol
- **AND** it SHALL write a sidecar audit event describing the protection failure

#### Scenario: Configured hard exposure limits are enforced
- **WHEN** a Tactical shadow record maps to a sidecar plan
- **AND** the requested margin would exceed configured max trade amount, effective balance cap, or free-balance requirements
- **THEN** the sidecar SHALL reject the record without placing a live order
- **AND** it SHALL write a sidecar audit event with the hard-limit reason

### Requirement: Sidecar state is separated from Main state
The sidecar SHALL use state and ledger paths separate from the Main process. It SHALL NOT write to Main `data/positions.json`, Main live order events, or Main live lifecycle files unless explicitly configured for a diagnostic-only dry run.

#### Scenario: Sidecar writes separate files
- **WHEN** the sidecar records an attempted, filled, rejected, closed, or skipped mirror event
- **THEN** it SHALL write to sidecar-specific state/audit files
- **AND** it SHALL NOT mutate Main position or ledger files

#### Scenario: Main process is not restarted
- **WHEN** the sidecar starts for the 24-hour run
- **THEN** the existing `run_agents.py` process SHALL remain running
- **AND** the sidecar start procedure SHALL NOT require changing Main Tactical `.env` gates or restarting Main

### Requirement: Same-account owner isolation
The system SHALL support same-account sidecar deployment by recording sidecar ownership and preventing Main from taking ownership of sidecar account objects.
```

Full source: openspec/changes/promote-shadow-tactical-live-48h/specs/tactical-exit-track/spec.md

