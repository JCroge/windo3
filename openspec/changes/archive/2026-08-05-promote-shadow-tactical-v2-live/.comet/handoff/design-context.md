# Comet Design Handoff

- Change: promote-shadow-tactical-v2-live
- Phase: design
- Mode: compact
- Context hash: 2b7c81edf968e29b07d8c71a4ff25b94ca452e7f2ed74985994899ecf5025a80

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/promote-shadow-tactical-v2-live/proposal.md

- Source: openspec/changes/promote-shadow-tactical-v2-live/proposal.md
- Lines: 1-38
- SHA256: 2b1991ccf398570a457310e17d56cbac6d70098b6d8a25991ed83c6ca71e5407

```md
## Why

Main Tactical live previously underperformed its Shadow Tactical evidence because the two paths did not execute the same population or lifecycle: shadow rows were duplicated and assumed entry fills, shadow TP1 ended the full trade, while live TP1 only reduced 50% and remained exposed to Main-adjacent invalidation and position-management exits. Promoting the existing shadow plans safely requires one canonical intent and one entry/exit state machine, rather than re-enabling the legacy `TACTICAL_SHADOW_ONLY=false` path or continuing a separate sidecar owner model.

## What Changes

- Add a durable Tactical V2 intent and episode lifecycle inside the Main process. A qualifying Shadow Tactical plan becomes an immutable intent with a deterministic episode identity, one live attempt, persistent state transitions, and deterministic exchange client-order identity.
- Replace percentage-only stale-entry recalculation for Tactical V2 with an R-based entry state machine: immediate entry only within `0.10R`, otherwise wait at the original entry for at most 15 minutes; never chase after the target, backfill a capacity-skipped episode, or retry the same episode after restart.
- Make shadow and live consume the same entry and exit lifecycle. Shadow counts a fill only after executable-price touch; live and shadow both use full-position TP1, full-position SL, and a 90-minute max hold.
- Isolate Tactical positions from Main Position Analyst, Main add/reduce decisions, Main break-even/profit trailing, and live-only thesis invalidation after fill. Shared system-integrity and account-level risk exits remain authoritative.
- Change Tactical sizing and admission to fixed `100U` margin with three independent Tactical slots. Pending entries consume slots; same-symbol Main, Tactical, or pending exposure remains prohibited.
- Replace the legacy Tactical natural-day risk limits with a persistent governor based on rolling 24-hour final PnL `<= -15U`, three consecutive final losses pausing new opens for 60 minutes, and non-expiring integrity halt until protection/ownership reconciliation succeeds. Existing positions continue under their original exits while admission is paused.
- Add owner-tagged exchange TP+SL OCO protection for Tactical V2 and idempotent reconciliation across concurrent or crash-interrupted exit paths.
- Extend Telegram `/status` with a compact, freshness-aware Tactical V2 snapshot covering mode/version, fixed sizing, active/pending/free slots, rolling PnL, loss streak, circuit state, episode outcomes, protection/reconciliation health, and shadow/live mismatch counts.
- Retire the live sidecar through an explicit drain: stop new sidecar admissions, reconcile all proven sidecar owners and protective orders to flat, archive state, then enable Tactical V2. The old sidecar must never be adopted as a Tactical V2 position source.
- Keep live deployment gated by deterministic historical replay, failure-injection tests, a 24-hour V2 shadow-only cloud observation, and a verified sidecar drain. The first V2 live cohort starts directly at `100U x 3` after those gates pass.

## Capabilities

### New Capabilities

- `tactical-intent-lifecycle`: Canonical Tactical V2 intents, episode reset/deduplication, R-based pending entry, crash-safe order idempotency, shared shadow/live lifecycle, and persisted operational status.

### Modified Capabilities

- `tactical-exit-track`: Replace legacy sizing, partial TP, thesis exits, concurrency, and daily-loss semantics with the approved Tactical V2 behavior while preserving Main/Tactical classification and accounting separation.
- `entry-drift-policy`: Exempt Tactical V2 from Main percentage drift recalculation and require immutable R-based entry handling without SL/TP recomputation.
- `protective-sl-owner-tag`: Extend owner identity and reconciliation from protective SL to Tactical V2 exchange TP+SL OCO ownership.
- `tg-status-enhancement`: Display Tactical V2 lifecycle, circuit, protection, freshness, and shadow/live parity state in `/status` without making Telegram a risk-state authority.
- `shadow-tactical-sidecar-exit-monitoring`: Add safe drain and retirement semantics before Tactical V2 live cutover, while preserving owner-bound management of any remaining sidecar exposure.

## Impact

- Affects Tactical classification and dispatch in `agents/trading/judge.py`, execution and position monitoring in `agents/trading/executor.py` and `executor.py`, Tactical risk state in `agents/trading/portfolio_risk_guard.py`, and Main interference paths in `agents/trading/position_analyst.py`.
- Adds a Tactical V2 intent/episode/state module and namespaced durable event/snapshot files under existing state-path conventions.
- Extends OKX attached protection ownership, startup/restart reconciliation, final-PnL consumption, and Telegram status formatting/tests.
- Changes Tactical live behavior and risk limits but does not change Main position sizing, Main strategy exits, global `MAX_TRADE_AMOUNT`, or the global emergency-close authority.
- Retains existing sidecar code and historical files for drain/audit; production sidecar admission is disabled only after verified cutover readiness.
```

## openspec/changes/promote-shadow-tactical-v2-live/design.md

- Source: openspec/changes/promote-shadow-tactical-v2-live/design.md
- Lines: 1-105
- SHA256: aa1defcefdd79d979dcf0b32897029303e82876c434486906bdefcd405ef28ee

[TRUNCATED]

```md
## Context

The existing Shadow Tactical and Main Tactical live paths share a name but not a population or lifecycle. In the reproduced window, seven live Tactical closes totaled `-1.4437U`, while 143 eligible-looking shadow rows reduced to only 14 repeated plan clusters. Live accepted the earliest candidate before its single slot filled, while shadow continued recording later candidates. Shadow assumed the recorded entry had filled and ended the entire trade at TP1; live reduced 50% at TP1 and allowed the remainder to close through thesis invalidation, weakened/no-progress, max hold, or SL. Three live `tactical_invalidated` closes accounted for `-3.2773U` while the other four closes totaled `+1.8336U`.

The user selected promotion approach B: preserve the exact Shadow Tactical plan at the point it is emitted, but prevent Main strategy logic from modifying it after that point. The first live cohort uses fixed `100U` margin and three Tactical slots, full close at TP1, one attempt per structural episode, rolling 24-hour final-PnL admission stop at `-15U`, and a three-loss 60-minute pause. Existing sidecar exposure must be drained and reconciled before cutover. Telegram `/status` must expose the same persisted Tactical state used operationally without becoming a risk authority.

Constraints include OKX position aggregation, existing one-position-per-symbol local state, attached-order ownership, asynchronous final PnL correction, crash windows around order submission, and an already dirty worktree containing separately approved sidecar resident-run changes that must be preserved.

## Goals / Non-Goals

**Goals:**

- Give shadow and live one canonical Tactical intent, episode identity, entry state machine, and exit state machine.
- Prevent stale price chasing, slot-release backfill, repeated attempts within one market episode, and restart-driven duplicate orders.
- Isolate Tactical strategy exits from Main Position Analyst, Main trailing, and Main add/reduce behavior while preserving global safety authority.
- Make Tactical sizing, slots, rolling loss, loss streak, protection integrity, and operational status explicit and persistent.
- Drain and retire sidecar live admission without adopting ambiguous sidecar account objects into Tactical V2.
- Produce replay, failure-injection, cloud shadow, and cutover evidence before live enablement.

**Non-Goals:**

- Designing a new standalone Tactical signal or SL/TP algorithm. V2 freezes the existing Shadow Tactical plan; an independent plan calculator is a later shadow experiment.
- Changing Main sizing, Main exits, global `MAX_TRADE_AMOUNT`, or global emergency risk behavior.
- Supporting Main and Tactical stacking on the same symbol or per-lot ownership inside an aggregated OKX net position.
- Treating a 24-hour observation or 30 trades as proof of durable strategy edge.
- Deleting sidecar code or historical state during cutover.

## Decisions

### Canonical immutable Tactical intent inside the Main process

Judge remains the upstream producer of Shadow Tactical plan values. A new intent factory validates and freezes those values into `tactical_intent.v2`; it does not recalculate SL/TP or re-run Main strategy admission later. The Tactical engine runs under `run_agents.py` and owns entry, position, exit, risk, and status state through a narrow interface. This preserves historical strategy meaning while removing post-classification Main interference.

Alternative rejected: re-enable legacy Main Tactical with `TACTICAL_SHADOW_ONLY=false`. It preserves the exact parity defects being fixed. Alternative rejected for V2: build an independent 15m/ATR Tactical strategy, because old shadow evidence would no longer describe the promoted strategy.

### Structural episode registry instead of exact-plan deduplication

`episode_id` is stable across repeated rows in the same symbol, direction, and 15m structure epoch. Exact entry/SL/TP values form a separate `plan_hash` for audit, not identity. An episode resets only after an opposing 15m block, a return to neutral followed by renewed direction, or a newly confirmed pivot/structure break after the prior episode terminates. Attempt, capacity skip, miss, or close all make the episode ineligible for later retry.

This prevents repeated plans with slightly changed prices from bypassing an exact hash and prevents a released slot from opening an old episode.

### R-based entry state machine with no Tactical drift recalculation

The engine uses executable ask for longs and bid for shorts. It may execute immediately only when the entry price is no more than `0.10R` worse than the frozen entry, where `R=abs(entry_ref-stop_loss)`. Otherwise it places a limit at the original entry for at most 900 seconds. It never shifts SL/TP to current price.

Pending entry is canceled permanently if TP is reached first, SL is reached, the 15m thesis invalidates, the episode resets, or the TTL expires. Capacity-full episodes are skipped, not queued. A partial fill cancels the remainder and protects only confirmed filled size. This is stricter and more comparable than the Main percentage drift policy.

### One post-fill strategy lifecycle

After fill, Tactical V2 has only full-position TP1, full-position SL, and a 90-minute max hold. Post-fill thesis invalidation and weakened/no-progress exits are removed from V2 because current shadow accounting never modeled them and they drove the largest reproduced live loss bucket. Main Position Analyst, Main break-even/profit trailing, and add/reduce actions must ignore Tactical V2.

Global drawdown, flash-move, protection-integrity, manual emergency, and exchange safety paths retain authority. Their closes are attributed as `risk_forced` and remain separate from normal strategy outcome buckets.

### Exchange-owned Tactical OCO and serialized local exits

Full TP makes exchange-owned TP+SL OCO practical and avoids missing TP while the process is unavailable. The OCO and entry order carry deterministic, owner-tagged identities derived from the intent and are persisted on the position. A fill is not considered safely open until both protection legs can be verified. Failure closes confirmed exposure when possible and enters a non-expiring integrity halt.

Max-hold and global close paths use the existing symbol exit lock and owner-bound cleanup. Exchange fills remain authoritative; concurrent local observations reconcile rather than submit a second close.

### Fixed Tactical capacity with shared account safety

Tactical uses `TACTICAL_MARGIN_USDT=100` and `TACTICAL_MAX_CONCURRENT=3`; it does not change `MAX_TRADE_AMOUNT`. Pending entries count toward the three slots. Tactical slots are independent of the three Main slots, but exchange free balance, one-position-per-symbol ownership, and global account exposure can still reject an intent. No additional Tactical correlation gate is introduced in the first cohort so the live sample is not silently narrowed.

### One persistent Tactical risk governor

The governor is the only Tactical admission authority. It reconstructs a rolling 24-hour window from final PnL ledger events, keyed by `resolution_id`. A correction applies only its delta. At `<= -15U`, new opens pause until the rolling sum recovers; existing positions continue. Three consecutive final losing episodes pause opens for 60 minutes and consume/reset the streak at pause start. A non-loss resets the active streak. Protection or ownership ambiguity enters an integrity halt that cannot expire on a timer.

This replaces the unused/duplicated `can_open_tactical()` and Judge file-read implementations with one persisted state model.

### Append-only lifecycle plus atomic read model

Intent and circuit transitions append to a namespaced Tactical V2 event ledger. Atomic snapshots accelerate recovery but are not the sole source of truth. Before exchange submission, state advances to `submitting` with a deterministic client order id. Restart recovery queries the exchange by that id and reconciles position/protection state before any retry.

A compact atomic Tactical status snapshot is the only Tactical data source for Telegram formatting. It includes `updated_at`, mode/version, sizing, slot occupancy, rolling PnL, streak/pause, episode aggregates, active/pending symbols, protection/reconciliation state, and shadow/live parity counts. Telegram is read-only; stale, missing, malformed, or non-finite data is shown as `STALE` or unknown rather than healthy.

### Sidecar drain before cutover

Sidecar admission is stopped first, but its monitor remains running until all proven sidecar owners are exchange-flat, all OCO/protection ownership is reconciled, and pending final PnL is resolved or explicitly documented. Sidecar state is archived, not deleted. Tactical V2 refuses to adopt old sidecar positions or owner rows. Only then can V2 live admission be enabled.

## Risks / Trade-offs
```

Full source: openspec/changes/promote-shadow-tactical-v2-live/design.md

## openspec/changes/promote-shadow-tactical-v2-live/tasks.md

- Source: openspec/changes/promote-shadow-tactical-v2-live/tasks.md
- Lines: 1-77
- SHA256: 5728791a4dd54d828566cc5348dc77913374c4c112d88f77ce06269d593ab7cf

```md
## 1. Baseline And Fixtures

- [ ] 1.1 Capture the reproduced live-versus-shadow window as deterministic intent, market-tick, fill, exit, and final-PnL fixtures.
- [ ] 1.2 Add episode fixtures covering repeated rows, opposing-block reset, neutral-then-renewed direction, confirmed new structure, and slot-release non-backfill.
- [ ] 1.3 Add executable bid/ask fixtures for immediate `0.10R`, original-entry wait, TP-before-entry, SL-before-entry, expiry, and partial-fill cases.

## 2. Tactical V2 Durable Model

- [ ] 2.1 Implement the immutable versioned Tactical intent schema, validation, canonical symbol handling, plan hash, and deterministic intent/order identities.
- [ ] 2.2 Implement the persisted structural episode registry, reset evidence, one-attempt terminal outcomes, and restart recovery.
- [ ] 2.3 Implement a namespaced append-only Tactical lifecycle/PnL event ledger with atomic recovery snapshots and correction-safe `resolution_id` deduplication.
- [ ] 2.4 Implement the atomic Tactical operational status snapshot derived from durable state, including transition-triggered and periodic refresh.

## 3. Signal And Entry Integration

- [ ] 3.1 Change Judge Tactical promotion to emit the exact eligible Shadow plan into the canonical V2 intent factory without later Main mutation.
- [ ] 3.2 Integrate a Tactical V2 engine into the Main process lifecycle with explicit shadow-only, live, admission-disabled, and integrity-halted modes.
- [ ] 3.3 Implement fixed `100U`, maximum 5x leverage, three active-or-pending Tactical slots, and independent Main slot accounting.
- [ ] 3.4 Implement same-symbol Main/Tactical/pending exposure rejection and terminal capacity skips without queueing or slot-release backfill.
- [ ] 3.5 Implement executable ask/bid `0.10R` immediate admission and one original-entry limit with a 900-second terminal TTL and no market fallback.
- [ ] 3.6 Implement pre-fill TP/SL/structure/expiry cancellation, partial-fill remainder cancellation, and terminal no-retry episode outcomes.
- [ ] 3.7 Persist `submitting` before exchange I/O and reconcile deterministic client ids after crash before permitting any retry or new admission.

## 4. Tactical Risk Governor

- [ ] 4.1 Replace natural-day Tactical accounting with one persistent rolling 24-hour final-PnL governor using a `-15U` new-admission threshold.
- [ ] 4.2 Implement three consecutive final losses as a consumed/reset streak with a persisted 60-minute new-admission pause.
- [ ] 4.3 Implement non-expiring execution/protection/ownership integrity halt and proof-based reconciliation clearing.
- [ ] 4.4 Route all Tactical admission decisions through the new governor while preserving management and exits for already filled positions.
- [ ] 4.5 Remove or disable legacy quality-window, volatility-dependent concurrency, and duplicate Judge/file-read Tactical admission authorities for V2.

## 5. Protection And Exit Ownership

- [ ] 5.1 Extend OKX owner-tag generation to deterministic Tactical entry, TP, and SL client identities within exchange length/format constraints.
- [ ] 5.2 Install and verify full-quantity exchange TP plus SL after every confirmed fill, supporting combined-OCO and separate-algo response shapes.
- [ ] 5.3 Fail closed on incomplete protection by cleaning only proven orders, safely closing confirmed unprotected exposure, and activating integrity halt.
- [ ] 5.4 Implement Tactical V2 full TP1, full SL, and 90-minute max-hold exits through the normalized-symbol exit lock.
- [ ] 5.5 Reconcile exchange fills, max-hold, global safety, cleanup, restart, and final-PnL publication idempotently under Tactical ownership.

## 6. Main Strategy Isolation

- [ ] 6.1 Guard Position Analyst close/reduce/add paths so they cannot act on `strategy_owner=tactical_v2` positions.
- [ ] 6.2 Guard Main break-even, profit trailing, partial-TP, thesis invalidation, and weakened/no-progress paths from Tactical V2 positions.
- [ ] 6.3 Preserve global drawdown, flash-move, protection-integrity, manual emergency, and exchange safety authority with `risk_forced` attribution.
- [ ] 6.4 Propagate Tactical V2 owner, intent, episode, plan, admission, protection, and final close metadata through execution, Reviewer, and PnL events.

## 7. Shared Shadow And Replay Parity

- [ ] 7.1 Make shadow and live adapters consume the same episode, entry, and exit state machine, differing only at exchange I/O boundaries.
- [ ] 7.2 Require executable-price touch for shadow fills and report non-filled terminal intents separately from filled performance.
- [ ] 7.3 Add per-intent shadow/live transition comparison with attributed mismatch categories and deduplicated episode metrics.
- [ ] 7.4 Replay the historical fixture and verify zero duplicate attempts, zero stale chase fills, full-TP1 parity, and classified execution variance.

## 8. Telegram Operational Status

- [ ] 8.1 Extend `/status` to render Tactical V2 mode/version, `100U x 3`, slots/symbols, rolling PnL, streak/circuit, episode outcomes, protection, and parity.
- [ ] 8.2 Enforce snapshot-only Tactical status reads, 90-second default freshness, and safe `STALE`/unknown rendering for missing, malformed, or non-finite data.
- [ ] 8.3 Keep global halt, per-symbol halt, and Tactical admission/integrity circuits visually and semantically distinct in Telegram tests.

## 9. Sidecar Drain And Cutover

- [ ] 9.1 Preserve and verify the existing resident-until-manual-stop sidecar CLI changes and their tests without rewriting unrelated behavior.
- [ ] 9.2 Add an admission-stop mode that leaves owner-bound sidecar monitoring and proven-exposure exits running during drain.
- [ ] 9.3 Implement a drain report covering pending entries, owners, exchange exposure, protection ambiguity, final PnL, and documented exceptions.
- [ ] 9.4 Block Tactical V2 live admission until the drain barrier passes and prevent legacy sidecar rows or positions from being adopted as V2.
- [ ] 9.5 Archive the final sidecar state and cutover evidence, disable sidecar admission, and ensure V2 rollback cannot auto-reactivate it.

## 10. Verification And Rollout

- [ ] 10.1 Add unit tests for intent immutability, episode identity/reset, R drift boundaries, capacity terminality, rolling/corrected PnL, streak consumption, and integrity halt.
- [ ] 10.2 Add integration tests for message-bus ownership isolation, three pending/active slots, Main-action rejection, full exits, and final metadata propagation.
- [ ] 10.3 Add failure-injection tests for each crash window around entry, partial fill, protection install, exchange TP/SL, local close, cleanup, and PnL correction.
- [ ] 10.4 Run the focused Tactical, sidecar, owner-isolation, PnL, TG status, and replay suites plus the repository regression suite.
- [ ] 10.5 Deploy V2 in cloud shadow-only mode and collect at least 24 hours of executable-price lifecycle, freshness, protection simulation, and parity evidence.
- [ ] 10.6 Stop sidecar admission, complete and archive the proven-owner drain, then enable live Tactical V2 at fixed `100U x 3` only after every cutover gate passes.
- [ ] 10.7 Verify the first live cohort has no duplicate orders, stale chase, Main strategy exits, or unprotected fills and that every shadow/live mismatch is classified.
- [ ] 10.8 Reconcile the full dirty worktree, update operational documentation/configuration, and submit the preserved sidecar changes together with the completed Comet implementation.
```

## openspec/changes/promote-shadow-tactical-v2-live/specs/entry-drift-policy/spec.md

- Source: openspec/changes/promote-shadow-tactical-v2-live/specs/entry-drift-policy/spec.md
- Lines: 1-37
- SHA256: 1c847c1a62fb87b205aee2aa95f654ff23356a45f1aef541849b6cfb347255e3

```md
## ADDED Requirements

### Requirement: Tactical V2 entry drift SHALL use the frozen R anchor
Plans with `exit_profile=tactical_v2` SHALL bypass the Main percentage drift classification, plan-field fail-safe, and limit-to-market fallback. Tactical V2 SHALL calculate `R=abs(entry_ref-stop_loss)` from the immutable intent and calculate worse-side drift from executable ask for longs or executable bid for shorts. An immediate entry MAY occur only when worse-side drift is at most `0.10R` and price has not reached a pre-fill terminal boundary. Otherwise Tactical V2 SHALL keep one limit order at the frozen entry for at most 900 seconds and MUST NOT translate entry, SL, or TP to the current price.

#### Scenario: Main drift behavior remains unchanged
- **WHEN** an executable plan does not have `exit_profile=tactical_v2`
- **THEN** the existing percentage drift classification and two-gate Main execution policy SHALL apply
- **AND** this Tactical override SHALL NOT change its entry, SL, or TP handling

#### Scenario: Worse-side drift within point one R enters without mutation
- **WHEN** a long executable ask is no more than `0.10R` above its frozen entry, or a short executable bid is no more than `0.10R` below its frozen entry
- **AND** neither frozen TP nor frozen SL has been reached
- **THEN** Tactical V2 MAY submit the immediate entry
- **AND** it SHALL preserve the frozen entry reference, SL, and TP

#### Scenario: Price near the target is not chased
- **WHEN** a long executable ask is more than `0.10R` above its frozen entry, or a short executable bid is more than `0.10R` below its frozen entry
- **AND** the price has not yet reached the frozen TP
- **THEN** Tactical V2 SHALL place or retain a limit only at the frozen entry
- **AND** it SHALL NOT submit a market order at the current price

#### Scenario: Target already reached terminates the episode
- **WHEN** a Tactical V2 intent has not filled
- **AND** executable price reaches or crosses its frozen TP
- **THEN** Tactical V2 SHALL cancel any remaining entry order and mark the episode `missed_after_target`
- **AND** a later return to the frozen entry SHALL NOT permit another attempt in the same episode

#### Scenario: Invalid R fails closed
- **WHEN** a Tactical V2 intent lacks finite entry or stop values, has `R<=0`, or cannot obtain the required executable side price
- **THEN** Tactical V2 SHALL record an explicit terminal rejection or integrity reason before exchange submission
- **AND** it SHALL NOT fall back to Main drift handling or silently accept the plan

#### Scenario: Tactical limit expiry has no market fallback
- **WHEN** a Tactical V2 original-entry limit remains unfilled for 900 seconds
- **THEN** the system SHALL cancel its remainder and mark the episode expired
- **AND** the Main 30-second fallback market path SHALL NOT run for that intent
```

## openspec/changes/promote-shadow-tactical-v2-live/specs/protective-sl-owner-tag/spec.md

- Source: openspec/changes/promote-shadow-tactical-v2-live/specs/protective-sl-owner-tag/spec.md
- Lines: 1-55
- SHA256: 7fd85dc42340d4d461d21cc5745f5efbbcf9be49a633fca116051f66941d54dd

```md
## ADDED Requirements

### Requirement: Tactical V2 SHALL use deterministic owner-tagged TP and SL protection
Every filled Tactical V2 position SHALL have full-quantity exchange-owned TP and SL protection whose client identities are deterministic derivatives of the intent id and satisfy the existing bot owner-tag format. The position state SHALL persist the entry client id, each protection client id, returned exchange algo ids, protected quantity, trigger prices, and reconciliation state. The ownership model MUST support exchanges that expose the TP and SL under one OCO algo id or separate algo ids without changing the Tactical lifecycle contract.

#### Scenario: Tactical fill installs identifiable full protection
- **WHEN** a Tactical V2 entry is confirmed filled for a quantity
- **THEN** the system SHALL submit full-quantity TP and SL protection with deterministic Tactical V2 owner-tagged client identities
- **AND** the persisted position SHALL contain enough identity to prove ownership after restart

#### Scenario: Partial fill protects only confirmed quantity
- **WHEN** a Tactical V2 entry partially fills and its remainder is canceled
- **THEN** TP and SL protection SHALL cover the confirmed filled quantity only
- **AND** the system SHALL NOT place protection for or chase the unfilled quantity

#### Scenario: Combined and separate algo representations reconcile equivalently
- **WHEN** OKX returns one parent algo id for attached TP and SL or returns independently addressable TP and SL algo ids
- **THEN** the reconciler SHALL persist the observed representation
- **AND** it SHALL prove both required protection legs without assuming a fixed exchange response shape

### Requirement: Unverified Tactical protection SHALL fail closed
A Tactical fill SHALL NOT be considered safely open until both its full-quantity TP and SL legs are verified against exchange state. If verification or ownership proof fails, the system SHALL stop new Tactical admission, cancel any provably owned residual entry or protection orders, and attempt to close confirmed unprotected exposure through the owner-bound safety path. The integrity halt MUST remain until reconciliation proves account, position, and protection state.

#### Scenario: One missing protection leg triggers integrity handling
- **WHEN** a filled Tactical V2 position has a verifiable SL but no verifiable full-quantity TP, or a verifiable TP but no verifiable full-quantity SL
- **THEN** the system SHALL mark protection incomplete and activate the non-expiring Tactical integrity halt
- **AND** it SHALL attempt an owner-bound safe close of confirmed exposure rather than continue normal admission

#### Scenario: Unknown ownership does not cause broad cancellation
- **WHEN** an exchange protection order or position could belong to Main, legacy sidecar, another bot, or a manual operator
- **THEN** Tactical V2 SHALL preserve the ambiguous object and halt new Tactical admission for reconciliation
- **AND** it SHALL NOT cancel or close the object without ownership proof

#### Scenario: Reconciliation clears halt only after proof
- **WHEN** an integrity halt is active because protection or exposure was ambiguous
- **THEN** elapsed time alone SHALL NOT clear the halt
- **AND** admission MAY resume only after reconciliation proves every affected owner flat or fully protected

### Requirement: Tactical exit paths SHALL serialize and reconcile idempotently
Exchange TP/SL fills, local max-hold closes, global safety closes, and restart recovery SHALL coordinate through the existing normalized-symbol exit lock and owner identity. Exchange fills SHALL be authoritative. Before a local close, the system MUST reconcile remaining exchange quantity and cancel or amend only proven Tactical protection. Repeated observations of one close MUST converge on one final resolution rather than submit duplicate reduce-only closes.

#### Scenario: Exchange TP races max hold
- **WHEN** exchange TP fills while the local max-hold path is waiting for the same symbol exit lock
- **THEN** the local path SHALL reconcile the remaining quantity after acquiring the lock
- **AND** it SHALL NOT submit a second close when the exchange position is already flat

#### Scenario: Restart during close does not duplicate resolution
- **WHEN** the process restarts after an exchange close but before local close state is final
- **THEN** recovery SHALL reconcile exchange position, protection orders, and the deterministic intent identity
- **AND** it SHALL publish or retain one final PnL resolution for that close

#### Scenario: Shared safety close retains Tactical attribution
- **WHEN** a global safety path closes a proven Tactical V2 position
- **THEN** owner-bound protection cleanup SHALL run under the serialized exit path
- **AND** the close SHALL be attributed as Tactical `risk_forced` rather than as a Main strategy exit
```

## openspec/changes/promote-shadow-tactical-v2-live/specs/shadow-tactical-sidecar-exit-monitoring/spec.md

- Source: openspec/changes/promote-shadow-tactical-v2-live/specs/shadow-tactical-sidecar-exit-monitoring/spec.md
- Lines: 1-65
- SHA256: 4a9fcae69040448659809ad557e6a133dce21553f17f60d8e5e8e197f3caad19

```md
## ADDED Requirements

### Requirement: Sidecar retirement SHALL use an admission-stop and drain barrier
Tactical V2 live admission SHALL remain disabled until sidecar new admissions are stopped and the sidecar drain barrier is satisfied. During drain, the sidecar monitor SHALL remain resident and continue owner-bound management of existing proven sidecar positions and orders. The barrier SHALL require no sidecar pending entries, no proven open sidecar exposure, no unresolved sidecar protection ownership, and no undocumented pending final-PnL resolution.

#### Scenario: Admission stops before monitoring stops
- **WHEN** the operator begins Tactical V2 cutover
- **THEN** the sidecar SHALL reject new live opens before its position monitor is stopped
- **AND** proven existing sidecar exposure SHALL continue to be managed until reconciled flat

#### Scenario: Unresolved owner blocks V2 live cutover
- **WHEN** a sidecar owner row or exchange object has present or unknown exposure or protection state that cannot be reconciled
- **THEN** the sidecar drain barrier SHALL remain unsatisfied
- **AND** Tactical V2 live admission SHALL remain disabled

#### Scenario: Pending final PnL requires resolution or documentation
- **WHEN** all sidecar exchange exposure is flat but an external-close ledger item has no final PnL resolution
- **THEN** cutover SHALL remain blocked until the item is resolved or explicitly recorded as an accepted reconciliation exception
- **AND** the exception SHALL remain visible in the archived drain evidence

### Requirement: Tactical V2 SHALL not adopt legacy sidecar ownership
Legacy sidecar owner rows, local positions, pending orders, protection orders, and PnL records SHALL NOT be reclassified or imported as Tactical V2 live positions. Same-symbol legacy or ambiguous exposure SHALL block a Tactical V2 episode through the shared exposure/integrity gate. Tactical V2 SHALL create live exposure only from a new canonical V2 intent after the drain barrier passes.

#### Scenario: Legacy sidecar position is not adopted
- **WHEN** startup finds an open sidecar owner row for a symbol
- **THEN** Tactical V2 SHALL NOT create a V2 position from that row or manage it as a V2 strategy position
- **AND** V2 live admission SHALL remain blocked until the sidecar path reconciles the row

#### Scenario: Archived sidecar record cannot consume a V2 slot
- **WHEN** the sidecar drain is complete and historical owner records are archived closed
- **THEN** those records SHALL remain available for audit
- **AND** they SHALL NOT appear as active or pending Tactical V2 slots

### Requirement: Sidecar retirement evidence SHALL be archived and reversible only by explicit rollout action
After the drain barrier passes, the system SHALL atomically archive the sidecar admission state, final owner/protection reconciliation summary, final PnL exceptions, and cutover timestamp before enabling Tactical V2 live. Disabling Tactical V2 later SHALL stop new V2 intents and preserve management of filled V2 positions, but MUST NOT automatically restart sidecar admission.

#### Scenario: Successful drain produces auditable cutover evidence
- **WHEN** all drain-barrier conditions are proven satisfied
- **THEN** the system SHALL persist an immutable or append-only sidecar retirement record before V2 live enablement
- **AND** the record SHALL identify the reconciled owners, protection result, PnL result, and cutover time

#### Scenario: V2 rollback does not reactivate sidecar
- **WHEN** Tactical V2 new admission is disabled after cutover
- **THEN** filled V2 positions SHALL remain under their verified protection and V2 exit controller until flat
- **AND** legacy sidecar admission SHALL remain disabled unless a separate explicit rollout action enables it

## MODIFIED Requirements

### Requirement: Sidecar Tactical exits SHALL reuse Tactical exit semantics
Legacy sidecar positions SHALL retain the legacy sidecar exit semantics captured when they opened: TP1 reduces 50 percent, legacy invalidated or weakened-without-progress conditions may close the remainder, max hold closes the remainder, and protective stop handling remains authoritative. During retirement drain, these legacy positions MUST NOT be converted to Tactical V2 full-TP1 or V2 post-fill semantics. No new sidecar position may open after admission stop, and every drain exit SHALL continue to use owner-bound reduce/close handling.

#### Scenario: Existing sidecar TP1 retains legacy partial reduce
- **WHEN** a proven sidecar-owned position opened before admission stop reaches TP1 during drain
- **THEN** the sidecar SHALL trigger its legacy 50 percent reduce action
- **AND** it SHALL preserve and protect the remaining legacy position according to its captured state

#### Scenario: Existing sidecar invalidation retains captured behavior
- **WHEN** a proven sidecar-owned position opened before admission stop receives its legacy invalidation condition during drain
- **THEN** the sidecar SHALL request an owner-bound close of the remainder
- **AND** it SHALL record the legacy invalidation reason without classifying the close as Tactical V2

#### Scenario: New V2 position uses no legacy sidecar exit
- **WHEN** a canonical Tactical V2 intent fills after cutover
- **THEN** the sidecar SHALL NOT monitor, reduce, or close that V2 position
- **AND** the Tactical V2 full-TP1, full-SL, and 90-minute controller SHALL be its strategy owner
```

## openspec/changes/promote-shadow-tactical-v2-live/specs/tactical-exit-track/spec.md

- Source: openspec/changes/promote-shadow-tactical-v2-live/specs/tactical-exit-track/spec.md
- Lines: 1-151
- SHA256: a0a5a92089a5d1e8760591def4ee8e9c83321b1487922d0da866503bf76f9477

[TRUNCATED]

```md
## MODIFIED Requirements

### Requirement: Main and Tactical track classification
The system SHALL classify every executable open candidate as `track=main` or a Shadow Tactical candidate before final track-specific acceptance. Main Trend SHALL be selected only when the trade direction is aligned with higher-timeframe bias and daily bias, the 15m timing signal is not opposing the trade, and the candidate passes the Main Trend quality gate. Directionally valid weak or mixed-environment candidates that do not qualify for Main Trend, or an explicitly allowed subset of structure-backed hold/reject candidates, MAY produce an immutable Tactical V2 intent. After intent creation, Main strategy logic MUST NOT participate in Tactical admission, plan mutation, or position exit decisions.

#### Scenario: Strong trend remains Main
- **WHEN** a candidate has trade-direction aligned higher-timeframe bias and daily bias
- **AND** the 15m signal is not opposing the trade
- **AND** the candidate passes the Main Trend quality gate
- **THEN** the system marks the plan with `track=main` and `exit_profile=trend_runner`
- **AND** the candidate uses existing Main Trend TP/SL and R:R semantics

#### Scenario: Weak environment candidate becomes Tactical V2 intent
- **WHEN** a candidate is directionally valid but fails Main Trend protection because the environment is weak or mixed
- **AND** no hard Tactical veto is present
- **THEN** the system MAY create an immutable plan with `track=tactical` and `exit_profile=tactical_v2`
- **AND** the candidate MUST use Tactical V2 intent, entry, sizing, risk, and exit lifecycle semantics

#### Scenario: Hold or reject promotion is narrow
- **WHEN** a hold or rejected candidate has a compatible reason such as `rr_below_floor`, confidence in the 40-60 range with strong structure, or light score-below-threshold
- **AND** the candidate has explicit structure support for the trade direction
- **THEN** the system MAY create a Tactical candidate
- **AND** the source reason MUST be recorded in `tactical_source`

### Requirement: Tactical sizing and leverage limits
The system SHALL size Tactical V2 independently from Main with fixed configured margin `TACTICAL_MARGIN_USDT=100` and a maximum of three active-or-pending Tactical slots. Changing Tactical margin MUST NOT change global `MAX_TRADE_AMOUNT` or Main sizing. Tactical leverage MUST NOT exceed 5x. Tactical positions MUST NOT be eligible for add-to-position actions, and a partial entry fill MUST NOT be chased to reach the configured margin.

#### Scenario: Fixed Tactical sizing does not resize Main
- **WHEN** a Tactical V2 candidate is admitted
- **THEN** its requested margin SHALL be `100U` regardless of the equivalent Main margin
- **AND** Main `MAX_TRADE_AMOUNT` and Main plan sizing SHALL remain unchanged

#### Scenario: Three Tactical slots include pending entries
- **WHEN** the combined number of active Tactical positions and pending Tactical entries is three
- **THEN** no additional Tactical intent SHALL enter pending or filled state
- **AND** Main slots SHALL remain independently governed

#### Scenario: No Tactical add
- **WHEN** a `position_analyst` or other source proposes adding to an open Tactical position
- **THEN** the system SHALL reject the add request
- **AND** it SHALL preserve the existing Tactical position lifecycle

### Requirement: Tactical R:R and net EV are isolated from Main
The system SHALL calculate Tactical R:R and EV from the frozen Tactical stop, full-position Tactical TP1, fees, funding approximation, and slippage assumptions. Tactical acceptance MUST require the configured Tactical R:R, EV, and cost-coverage gates. Tactical MUST NOT use Main Trend ladder TP2/TP3 assumptions or expected partial exits to pass acceptance gates.

#### Scenario: Tactical cannot pass using Main ladder R:R
- **WHEN** a candidate has Main ladder `effective_risk_reward_ratio` above the Main floor
- **BUT** its frozen Tactical full-TP1 net EV does not pass the configured Tactical gate
- **THEN** the Tactical candidate SHALL be rejected
- **AND** the rejection SHALL reference Tactical EV or cost coverage, not Main R:R

#### Scenario: Cost coverage gate blocks low net target
- **WHEN** Tactical TP1 gross distance is too small to cover configured fee plus slippage by the configured coverage multiple
- **THEN** the system SHALL reject the Tactical plan
- **AND** it SHALL record `tactical_cost_gate=fail`

### Requirement: Tactical local exit lifecycle
The system SHALL manage each filled Tactical V2 position with exactly one strategy lifecycle: full-position TP1, full-position protective SL, and full-position close at a 90-minute maximum hold. Post-fill 15m thesis invalidation, weakened/no-progress, partial TP, Main break-even/profit trailing, Main Position Analyst close/reduce, and Main add behavior MUST NOT modify or close a Tactical V2 position. System-wide safety exits SHALL retain authority and MUST be attributed separately as risk-forced exits.

#### Scenario: Tactical TP1 closes the full position
- **WHEN** a filled Tactical V2 position reaches frozen TP1
- **THEN** the system SHALL close the full remaining position
- **AND** it SHALL NOT leave a partial Tactical remainder

#### Scenario: Post-fill 15m invalidation does not alter V2 exit
- **WHEN** a filled Tactical V2 position later receives a 15m opposing block or weakened thesis signal
- **THEN** the system SHALL retain the original TP, SL, and max-hold lifecycle
- **AND** it SHALL NOT request `tactical_invalidated` or `tactical_weakened_no_progress`

#### Scenario: Tactical max hold closes position
- **WHEN** a Tactical V2 position reaches 90 minutes of age without an authoritative TP or SL close
- **THEN** the system SHALL close the full remaining position
- **AND** the close reason SHALL be `tactical_max_hold`

#### Scenario: System safety remains authoritative
- **WHEN** a global drawdown, flash-move, protection-integrity, or manual emergency close applies to a Tactical V2 position
- **THEN** the system MAY close the Tactical position through the shared safety path
- **AND** the outcome SHALL be attributed as risk-forced rather than a normal Tactical strategy exit

### Requirement: Tactical risk governor and circuit breakers
```

Full source: openspec/changes/promote-shadow-tactical-v2-live/specs/tactical-exit-track/spec.md

## openspec/changes/promote-shadow-tactical-v2-live/specs/tactical-intent-lifecycle/spec.md

- Source: openspec/changes/promote-shadow-tactical-v2-live/specs/tactical-intent-lifecycle/spec.md
- Lines: 1-99
- SHA256: df6631a7fa7ffa4e8d91e4a18dea97422a3e3626988c0368ee7a827fe070fc86

[TRUNCATED]

```md
## ADDED Requirements

### Requirement: Tactical V2 SHALL create one immutable canonical intent
The system SHALL convert each eligible Shadow Tactical plan into a versioned `tactical_intent.v2` before live admission. The intent MUST freeze symbol, side, entry reference, stop loss, full-position TP1, leverage, fixed margin, maximum hold, source shadow id, episode id, plan hash, creation time, and expiry time. Main strategy logic MUST NOT recompute or mutate these fields after intent creation.

#### Scenario: Shadow plan becomes an immutable intent
- **WHEN** Judge emits an eligible Shadow Tactical plan
- **THEN** Tactical V2 SHALL persist a canonical intent containing the exact emitted entry, SL, and TP values
- **AND** later Main analysis or price drift SHALL NOT rewrite those values

#### Scenario: Main strategy cannot mutate a filled Tactical plan
- **WHEN** a Tactical V2 position is open
- **AND** Main Position Analyst, Main trailing, or a Main add/reduce decision evaluates the symbol
- **THEN** the Main strategy action SHALL be ignored for that Tactical position
- **AND** the frozen Tactical intent SHALL remain unchanged

### Requirement: Tactical episodes SHALL deduplicate one structural market opportunity
The system SHALL assign a durable `episode_id` by symbol, direction, and active 15m structure epoch. Exact plan prices SHALL be represented by a separate `plan_hash` and MUST NOT define episode identity. An attempted, missed, invalidated, capacity-skipped, or closed episode MUST NOT become eligible for another live attempt until a reset condition creates a new episode.

#### Scenario: Repeated plans remain one episode
- **WHEN** repeated Tactical rows have the same symbol, direction, and active 15m structure but slightly different entry, SL, or TP values
- **THEN** they SHALL share one episode id
- **AND** at most one live attempt SHALL occur

#### Scenario: Structure reset creates a new episode
- **WHEN** an opposing 15m block occurs, direction returns to neutral before reforming, or a new confirmed pivot/structure break appears after the prior episode terminates
- **THEN** the system SHALL create a new episode id for a later compatible signal
- **AND** the reset evidence SHALL be persisted

### Requirement: Tactical V2 SHALL use an R-based non-chasing entry lifecycle
The system SHALL evaluate long entry against executable ask and short entry against executable bid. With `R=abs(entry_ref-stop_loss)`, an immediate order MAY be submitted only when executable price is no more than `0.10R` worse than the frozen entry. Otherwise the system SHALL place or maintain a limit at the original entry for no more than 900 seconds. Tactical V2 MUST NOT recalculate or translate SL/TP to current price.

#### Scenario: Tight executable price enters immediately
- **WHEN** executable price is no more than `0.10R` worse than the frozen Tactical entry
- **AND** a Tactical slot and risk admission are available
- **THEN** the system MAY submit the live entry
- **AND** it SHALL preserve the frozen SL and TP

#### Scenario: Adverse entry drift waits instead of chasing
- **WHEN** executable price is more than `0.10R` worse than the frozen entry
- **AND** price has not reached TP or SL
- **THEN** the system SHALL wait at the original entry for at most 900 seconds
- **AND** it SHALL NOT submit a market order at the drifted price

#### Scenario: Target reached before entry permanently misses the episode
- **WHEN** a pending entry has not filled
- **AND** market price reaches or crosses the frozen TP
- **THEN** the system SHALL cancel the pending entry and mark `missed_after_target`
- **AND** a later return to entry SHALL NOT reopen the same episode

#### Scenario: Pre-fill invalidation cancels entry
- **WHEN** a pending entry reaches SL, receives an opposing 15m block, resets its structure episode, or exceeds 900 seconds
- **THEN** the system SHALL cancel any remaining entry order
- **AND** it SHALL mark the terminal pre-fill reason without creating exposure

#### Scenario: Partial entry fill does not chase remainder
- **WHEN** a pending Tactical entry partially fills
- **THEN** the system SHALL cancel the unfilled remainder
- **AND** it SHALL protect and manage only the confirmed filled quantity

### Requirement: Tactical capacity skips SHALL be terminal for the episode
Tactical V2 SHALL count both active positions and pending entry orders against three Tactical slots. A candidate presented while all slots are occupied SHALL be marked `capacity_skipped` and MUST NOT be queued for later entry. Any Main, Tactical, or pending exposure for the same normalized symbol SHALL also make the episode terminally ineligible.

#### Scenario: Released slot does not backfill old episode
- **WHEN** a Tactical episode is skipped because all three slots are occupied
- **AND** a slot later becomes free
- **THEN** the skipped episode SHALL remain skipped
- **AND** only a newly created episode MAY use the free slot

#### Scenario: Same-symbol exposure blocks the episode
- **WHEN** Main, Tactical, or pending exposure already exists for the normalized symbol
- **THEN** the new Tactical episode SHALL be marked with a same-symbol skip reason
- **AND** it SHALL NOT be retried after that exposure closes

### Requirement: Tactical order submission SHALL recover idempotently across restart
The system SHALL persist `submitting` before exchange I/O and derive a deterministic entry client-order id from the intent id. On restart, any non-terminal `submitting`, `filled`, or `closing` state SHALL be reconciled against exchange orders, positions, and owner-tagged protection before another action is submitted. The system MUST NOT blindly retry an unknown submission.

#### Scenario: Crash after exchange accepted entry does not duplicate order
- **WHEN** the exchange accepts an entry but the process stops before persisting the response
- **THEN** restart recovery SHALL find the order or position using deterministic identity
```

Full source: openspec/changes/promote-shadow-tactical-v2-live/specs/tactical-intent-lifecycle/spec.md

## openspec/changes/promote-shadow-tactical-v2-live/specs/tg-status-enhancement/spec.md

- Source: openspec/changes/promote-shadow-tactical-v2-live/specs/tg-status-enhancement/spec.md
- Lines: 1-69
- SHA256: cf56ae0306f291eb1a262d9f25c143ace402d8c457e5c8d7e8f21af3f51345df

```md
## ADDED Requirements

### Requirement: Tactical V2 SHALL publish one atomic operational status snapshot
The Tactical V2 engine SHALL atomically write a namespace-aware status snapshot at least every 30 seconds and after material lifecycle or governor transitions. The snapshot SHALL include `updated_at`, mode and version, configured margin and slot limit, active/pending/free slot counts and symbols, rolling 24-hour final PnL, active loss streak, timed pause and integrity-halt state, episode outcome counts, protection and reconciliation health, and shadow/live parity mismatch counts. This snapshot SHALL be a read model only and MUST NOT become an admission or exit authority.

#### Scenario: Material transition updates the snapshot
- **WHEN** an intent enters pending, fills, terminates, closes, changes a circuit state, or changes protection integrity
- **THEN** Tactical V2 SHALL atomically refresh the operational snapshot
- **AND** the snapshot SHALL describe state derived from the durable Tactical ledger and current reconciliation result

#### Scenario: Telegram status cannot change risk state
- **WHEN** Telegram reads or formats the Tactical V2 snapshot
- **THEN** it SHALL NOT mutate a slot, episode, PnL record, pause, or integrity halt
- **AND** Tactical admission SHALL continue to use the persistent governor rather than Telegram data

### Requirement: `/status` SHALL display compact Tactical V2 execution state
Telegram `/status` SHALL render the Tactical V2 snapshot as a compact section containing mode/version, `100U x 3` configuration, active/pending/free slots, rolling 24-hour final PnL versus the `-15U` admission threshold, loss streak and 60-minute circuit state, episode outcomes, active/pending symbols, protection/reconciliation health, and shadow/live parity mismatch count. Admission pauses SHALL be labeled as blocking new Tactical opens while existing positions remain managed.

#### Scenario: Healthy Tactical V2 state is fully visible
- **WHEN** a fresh snapshot reports live V2 mode, one active slot, one pending slot, no circuit, and verified protection
- **THEN** `/status` SHALL show Tactical V2 live, `100U x 3`, `1 active / 1 pending / 1 free`, rolling PnL, streak, and circuit clear
- **AND** it SHALL show the active and pending symbols plus protection and parity state

#### Scenario: Rolling loss pause is distinguished from forced close
- **WHEN** rolling 24-hour final Tactical PnL is at or below `-15U`
- **THEN** `/status` SHALL show new Tactical admission paused by rolling loss
- **AND** it SHALL state or clearly imply that existing Tactical positions remain managed rather than force-closed by this threshold

#### Scenario: Integrity halt is visible and non-timed
- **WHEN** the snapshot reports unresolved ownership or protection ambiguity
- **THEN** `/status` SHALL show Tactical integrity halt with the affected symbols or count
- **AND** it SHALL NOT display an automatic expiry time for that halt

### Requirement: Tactical status freshness SHALL fail visibly
Telegram SHALL treat the Tactical snapshot as stale when `updated_at` is older than the configured freshness threshold, defaulting to 90 seconds. A missing, malformed, stale, or non-finite Tactical snapshot SHALL render `STALE` or unknown values and MUST NOT be presented as healthy. Failure to read Tactical data MUST NOT prevent existing global halt, per-symbol halt, agent, or DLQ status from rendering.

#### Scenario: Stale snapshot is not shown as healthy
- **WHEN** the Tactical snapshot is older than 90 seconds under the default configuration
- **THEN** `/status` SHALL label the Tactical section `STALE`
- **AND** it SHALL NOT claim that slots, circuit, protection, or parity are current

#### Scenario: Non-finite PnL degrades safely
- **WHEN** the Tactical snapshot contains a non-finite rolling PnL value
- **THEN** `/status` SHALL render Tactical PnL as unknown or invalid
- **AND** the Telegram command SHALL continue rendering other status sections

## MODIFIED Requirements

### Requirement: `/status` SHALL distinguish global halt, per-symbol halt, and Tactical circuit

Telegram `/status` SHALL display global halt state, per-symbol halt state, and Tactical V2 circuit state as separate status lines. A global protection halt MUST NOT be presented in a way that implies the Tactical circuit is paused. Tactical circuit state SHALL be read only from the freshness-checked Tactical V2 operational snapshot; missing legacy risk-guard circuit data MUST NOT be interpreted as a healthy V2 circuit.

#### Scenario: global protection halt while Tactical circuit is not paused
- **WHEN** `halt_state.halted == true` with reason `okx_sl_algo_unresolved:WLD-USDT-SWAP`
- **AND** a fresh Tactical V2 snapshot reports no timed pause or integrity halt
- **THEN** `/status` MUST show global halt as active with the OKX protection reason
- **AND** `/status` MUST show Tactical circuit as not paused
- **AND** the message MUST NOT imply Tactical loss circuit caused the halt

#### Scenario: Tactical circuit paused while global halt is clear
- **WHEN** `halt_state.halted == false`
- **AND** a fresh Tactical V2 snapshot reports a future pause deadline or active integrity halt
- **THEN** `/status` MUST show global halt as inactive
- **AND** `/status` MUST show Tactical circuit as paused with its pause reason

#### Scenario: status data missing degrades safely
- **WHEN** the Tactical V2 snapshot is missing, unreadable, malformed, or stale
- **THEN** `/status` MUST still show global halt and per-symbol halt state
- **AND** Tactical circuit line MUST degrade to an unknown or `STALE` marker rather than failing the command
```
