---
comet_change: add-tactical-exit-track
role: technical-design
canonical_spec: openspec
---

# Tactical Exit Track Technical Design

## Context

The current live path is built around Main Trend continuation. `MultiJudge` builds a plan with trend-runner TP levels, ladder-weighted R:R, EV, slot metadata, and attribution. `executor.py` persists the position with local TP levels and an exchange-side protective SL. Reviewer, live ledger, PnL resolution, and counterfactual tooling already consume attribution such as `slot_type`, `entry_type`, `rr_policy`, and resolved PnL.

The WLD July 10 replay exposed the main gap: directional agreement is not enough to justify a Main Runner. WLD had bearish higher-timeframe, daily, and 15m alignment, but also mixed regime, weak volume/OI confirmation, LLM short reversal risk, trend-exhaustion warnings, and weak provenance. That kind of setup should not inherit 24h trend-runner TP/SL assumptions. It should either become a short-horizon Tactical trade with near profit capture and capped stop, or be live-rejected and measured in shadow/replay.

## Goals

- Add `track=main|tactical` and `exit_profile=trend_runner|tactical_v1` as first-class plan, position, event, and metric fields.
- Keep strong Main Trend trades on existing runner behavior.
- Add a Main Trend quality gate so weak aligned trades cannot remain Main solely due to direction alignment.
- Add Tactical plan math: structure stop, capped risk, Tactical TP profile, net EV, cost coverage, and sizing/leverage limits.
- Add Tactical local exit lifecycle: thesis health, early partial/protect decisions, invalidation exits, and max hold.
- Add Tactical risk governor and independent performance accounting.
- Make replay and counterfactual reports separate Main and Tactical outcomes.

## Non-Goals

- No exchange-side TP ownership change in v1. TP remains locally owned; exchange owns protective SL only.
- No same-symbol stacking between Main and Tactical.
- No Main TP/SL semantic change except explicit metadata and Main quality gating.
- No live auto-enablement without flags, replay evidence, and circuit breakers.

## Proposed Architecture

### 1. Track Classification in Judge

Add a bounded classifier around existing Judge plan construction:

```
raw candidate
  -> hard veto precheck
  -> build diagnostic Main plan
  -> classify track
       main: Main quality gate passed
       tactical: direction valid, Main quality failed, Tactical quality passed
       shadow/reject: hard veto or Tactical quality failed
  -> calculate final plan math for chosen track
  -> final slot/risk/EV gates
  -> publish or record rejection
```

The classifier should be implemented as small helpers rather than another inline block in `_handle_symbol`:

- `_classify_track(action, plan, tech, score, llm_result) -> TrackDecision`
- `_passes_main_trend_quality(...) -> MainQualityResult`
- `_passes_tactical_precheck(...) -> TacticalQualityResult`
- `_apply_tactical_profile(plan, tech, quality) -> plan`

`TrackDecision` can be a plain dict initially to match local style:

```python
{
    "track": "main" | "tactical" | "shadow_only" | "reject",
    "exit_profile": "trend_runner" | "tactical_v1" | "none",
    "reason": "...",
    "quality_flags": {...},
}
```

### 2. Main Trend Quality Gate

Main requires all of:

- direction alignment: HTF and daily agree with trade; 15m is not opposing,
- no hard veto,
- regime quality is supportive or a configured high-quality override exists,
- no explicit LLM reversal-risk flag,
- no trend-exhaustion warning above configured severity,
- volume/OI/microstructure confirmation is not weak,
- provenance quality is above configured floor.

Initial v1 should use conservative config defaults:

- `main_quality_gate_enabled=True`
- `main_quality_min_provenance=0.20`
- `main_quality_block_llm_reversal=True`
- `main_quality_allow_mixed_override=False`
- `main_quality_require_volume_or_oi=True`

The WLD-like case is the regression fixture: bearish HTF/daily/15m alignment plus mixed regime, weak volume/OI, LLM reversal risk, trend-exhaustion warning, or very weak provenance must not classify as Main.

### 3. Tactical Plan Profile

Tactical starts from the diagnostic Main plan but recalculates final trade economics:

- `track=tactical`
- `exit_profile=tactical_v1`
- `slot_type=tactical`
- `tactical_source`: e.g. `main_quality_failed`, `rr_below_floor`, `confidence_mid_structure`
- `tactical_stop_loss`: structure stop capped at `<= 0.6R_main`
- `tactical_stop_quality=very_near` when stop is `<= 0.4R_main`
- default margin `0.70 * main_size_usdt`
- max margin `1.00 * main_size_usdt` only for very-near stop and clean quality
- max leverage `5x`
- Tactical TP1 around `0.6R_tactical` or nearest structure
- Tactical max hold `90m`

Tactical must not use Main ladder TP2/TP3 to pass acceptance. Store both:

- `main_diagnostic_effective_rr`
- `tactical_effective_rr`
- `tactical_expected_value`
- `tactical_cost_gate`

Acceptance requires:

- net EV > 0,
- TP1 net return covers fee + slippage by at least 4x,
- liquidity/spread acceptable,
- no hard veto,
- Tactical risk governor allows new open.

### 4. Tactical Exit Controller

Do not create a second executor in v1. Extend existing local lifecycle in `executor.py` and `agents/trading/executor.py` based on `position["track"]`.

State model:

```
opened
  -> healthy      -> keep / staged TP / ratchet SL
  -> weakened     -> tighten SL / partial-or-full exit
  -> invalidated  -> immediate exit
  -> timed_out    -> close
  -> closed
```

Evaluation triggers:

- every 1 minute while position is open,
- price tick events when TP/SL/protection thresholds are crossed,
- heavier evaluation on 15m candle close,
- explicit external events such as risk halt, position danger, protection failure.

Close reasons:

- `tactical_tp1`
- `tactical_tp2`
- `tactical_protect`
- `tactical_invalidated`
- `tactical_weakened_no_progress`
- `tactical_max_hold`
- `tactical_exchange_sl`

The protective SL owner remains exchange-side. Local TP actions use existing reduce/close paths and must only advance local state after confirmed execution, matching the partial TP lifecycle invariant.

### 5. Tactical Risk Governor

Keep this independent from Main except for system-wide integrity failures.

Responsibilities:

- daily Tactical realized/resolved loss hard stop at `-10U`,
- dynamic concurrency cap: calm max 2, high volatility max 1, extreme/news pause,
- 3 consecutive Tactical losses pauses Tactical for 1 hour,
- 20-trade Tactical quality failure pauses or downgrades Tactical,
- execution/protection failure pauses Tactical immediately,
- no add-to-position for Tactical.

Implementation can start inside Judge/RiskGuard using shared state, but the interface should remain narrow:

```python
can_open_tactical(symbol, plan, market_state) -> (allowed, reason, state)
record_tactical_close(symbol, pnl, close_reason, event)
record_tactical_execution_failure(symbol, reason)
```

### 6. Metadata and Accounting

Metadata must be propagated through:

- `trade_decision.plan`
- `trade_decision.attribution`
- `executor.positions`
- `execution_result`
- live ledger events and lifecycle
- `pnl_resolved` / `pnl_mismatch`
- Reviewer trade history
- counterfactual records and replay reports

Reviewer should compute:

- Main-only metrics,
- Tactical-only metrics,
- cross bucket: `side_regime_entry_type_track_exit_profile`,
- incremental Tactical value during Main-idle periods.

Legacy events without `track` default to Main-compatible behavior.

### 7. Counterfactual and Replay

Replay must support two exit models:

- Main diagnostic model: existing runner assumptions.
- Tactical model: Tactical stop, TP, max hold, and cost assumptions.

For WLD-like fixtures, expected replay behavior:

- first WLD-style short can hit Tactical TP1 before Main TP1,
- second WLD-style short should either be shadow-only due to quality failure or hit capped Tactical stop far before the Main loss,
- Tactical replay result must not be counted as Main evidence.

## Data Flow

```
TechAnalysis + LLM + Regime + Market Context
    |
    v
Judge builds diagnostic Main plan
    |
    v
TrackClassifier
    | main
    | tactical
    | shadow_only/reject
    v
PlanProfileCalculator
    |
    v
RiskGovernor + SlotGate + EVGate
    |
    v
TradeDecision -> Executor -> Position lifecycle
    |
    v
ExecutionResult / PnL Resolution / Reviewer / Replay
```

## Error Handling

- Missing `track` defaults to `main`.
- Missing Tactical profile fields on `track=tactical` is a fail-closed open rejection.
- Exchange protective SL unresolved pauses Tactical immediately.
- Local TP reduce failure must not advance Tactical TP state.
- Resolver events missing new metadata remain backward compatible but cannot be used for Tactical-only metrics unless enough attribution exists.

## Testing Strategy

Focused unit tests:

- Main quality gate passes clean strong trend.
- WLD-like aligned-but-weak short does not classify as Main.
- WLD-like weak setup becomes Tactical when Tactical quality passes.
- WLD-like weak setup becomes shadow-only when provenance/cost/liquidity fails.
- Tactical R:R does not use Main ladder R:R.
- Tactical cost gate rejects tiny TP1 net target.
- Tactical sizing caps leverage at 5x and margin at 70% by default.
- Tactical very-near stop allows up to 100% Main margin.
- Tactical no-add policy rejects add requests.
- Tactical risk governor pauses on -10U daily loss, 3-loss streak, 20-trade quality failure, and protection failure.
- Tactical exit state closes on max hold, invalidation, and weakened no-progress windows.
- PnL and Reviewer events preserve `track` and `exit_profile`.

Replay tests:

- WLD first-trade fixture: Tactical TP1 is reached while Main TP1 is not.
- WLD second-trade fixture: Tactical capped stop or shadow-only avoids Main-sized loss.
- Main and Tactical replay buckets remain separate.

Integration tests:

- Accepted Tactical decision persists metadata through open, reduce/close, PnL resolution, and trade history.
- Legacy Main decision remains unchanged when Tactical flag is disabled.
- Disabling Tactical returns eligible candidates to existing Main/hold behavior without crashing metadata consumers.

## Rollout

1. Add metadata propagation and tests with no behavior change.
2. Enable Main quality gate in shadow diagnostics.
3. Enable Tactical plan generation in shadow-only mode.
4. Run replay with WLD-like fixtures and broader rejected/accepted samples.
5. Enable live Tactical opens at conservative caps only after segmented metrics are available.
6. Keep flag rollback for Tactical classification and Main quality gate independently.

## Open Implementation Notes

- `slot_type=tactical` should be added without removing `low_rr_extra`, `probe_short`, or `probe_long`.
- Existing `CandidateRanker` must learn the Tactical slot but should rank Tactical by Tactical R:R/EV, not Main R:R.
- The first implementation can keep quality gate thresholds config-driven and simple; do not add a model or new dependency.
- If Main quality gate and Tactical classifier disagree, hard veto and fail-closed behavior win.
