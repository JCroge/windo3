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
