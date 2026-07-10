# Comet Design Handoff

- Change: add-tactical-exit-track
- Phase: design
- Mode: compact
- Context hash: 2bccc0f2f81525ba11808e504f0af32e98820ba4fcafc5a1cf30f49ce91f20cc

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/add-tactical-exit-track/proposal.md

- Source: openspec/changes/add-tactical-exit-track/proposal.md
- Lines: 1-52
- SHA256: c4aadaca59bd2b35f75b022ce056203f64ad1f53e7ad2ab85574fededfda46ed

```md
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
```

## openspec/changes/add-tactical-exit-track/design.md

- Source: openspec/changes/add-tactical-exit-track/design.md
- Lines: 1-145
- SHA256: 0d3efe8bb0d4daada334110517cf1761196cbc9f793d28382ac005d9b859858d

[TRUNCATED]

```md
## Context

The live system already has a Main Trend-oriented open and exit path:

- `Judge` builds a plan, calculates TP/SL, effective R:R, EV, slot metadata, and publishes `trade_decision`.
- `executor.py` stores `slot_type`, `attribution`, local TP levels, and exchange-side protective SL in the position record.
- Partial TP is local: TP1 reduces 50%, TP2 reduces 25%, and the remainder uses trailing protection.
- Low-R:R handling is currently an extra slot and early trailing overlay, not a separate trading thesis.
- Reviewer, counterfactual ledger, and PnL resolution events already carry enough attribution hooks to extend with a track/profile label.

The problem is that weak or mixed-environment opportunities are currently evaluated and managed through a trend-runner TP/SL model. That can overstate R:R, delay profit realization, and blur metrics when a position should be judged as a short-horizon tactical trade rather than a clean trend continuation.

## Goals / Non-Goals

**Goals:**

- Add an explicit `tactical` track alongside the existing Main Trend path.
- Preserve Main Trend behavior for strong aligned setups.
- Downgrade eligible weak/mixed candidates into Tactical instead of forcing them through Main TP/SL assumptions.
- Calculate Tactical R:R and EV from its own exit profile, cost gate, and structure stop.
- Manage Tactical exits through local TP, exchange protective SL, thesis-health checks, and max-hold rules.
- Keep Tactical risk, concurrency, circuit breakers, and metrics independent from Main.
- Make replay and counterfactual reporting compare Main vs Tactical honestly.

**Non-Goals:**

- No exchange TP owner migration in v1; OKX remains exchange protective SL only, with TP owned locally.
- No same-symbol stacking between Main and Tactical.
- No change to Main Trend TP/SL semantics except explicit metadata and isolation.
- No manual discretionary mode; the design targets full automation behind feature flags and circuit breakers.
- No assumption that Tactical is profitable until segmented live/replay evidence supports it.

## Decisions

### Decision 1: Represent Tactical as a first-class track, not a low-R:R variant

Add `track` and `exit_profile` fields to plans, positions, execution payloads, and review records:

- Main Trend: `track=main`, `exit_profile=trend_runner`
- Tactical: `track=tactical`, `exit_profile=tactical_v1`

`slot_type` remains useful for concurrency buckets, but it is not enough to describe exit semantics. Tactical may use `slot_type=tactical` or a compatible new slot value, while `track` is the canonical performance and exit-contract field.

Alternatives considered:

- Reuse `low_rr_extra`: rejected because low-R:R is a sizing/protection overlay, while Tactical changes candidate classification, R:R math, TP/SL lifecycle, hold time, and risk governor.
- Add a completely separate executor: rejected for v1 because the current position lifecycle already supports local partial TP plus exchange SL, and duplicating order ownership increases failure surface.

### Decision 2: Classify before final R:R and EV gates

Judge should classify candidate intent before applying final R:R/EV acceptance:

1. Strong trend candidates stay Main only when HTF and daily bias align with the trade, 15m is not opposing, and a Main Trend quality gate passes.
2. Directionally valid candidates that fail Main trend protection can be considered for Tactical.
3. A narrow subset of hold/reject candidates can be reconsidered for Tactical only when rejection reason is compatible with Tactical, such as R:R below Main floor or confidence 40-60 with strong structure.
4. Hard vetoes remain hard vetoes: regime flat with no thesis, 15m opposing block, explicit reversal thesis, short structural blocks, liquidity/execution failure, and extreme/news pause.

The quality gate exists because directional alignment alone can still be a poor Main Runner setup. A WLD-like short can have HTF, daily, and 15m bearish alignment while also showing mixed regime, weak volume/OI confirmation, LLM reversal risk, trend-exhaustion warnings, and weak provenance. That setup must not remain Main solely because it is directionally aligned; it should either be downgraded to Tactical with Tactical R:R/EV or rejected live and recorded for shadow/replay.

Alternatives considered:

- Classify after Main rejection only: too late, because Main R:R/EV may already have used the wrong target assumptions.
- Classify only by confidence: too weak, because Tactical eligibility is structure and environment dependent.

### Decision 3: Tactical has its own plan math

Tactical MUST compute:

- structure-bounded stop,
- stop cap relative to Main stop (`<= 0.6R_main`) plus ATR/percentage cap,
- very-near-stop flag (`<= 0.4R_main`) for possible full Main-sized margin,
- default size at 70% of Main, max 100% only when stop is very near and conditions are clean,
- max leverage 5x,
- TP1 around 0.6R or nearest structure,
- net EV greater than 0,
- TP1 net return covering fee plus slippage by at least 4x.

The current `effective_risk_reward_ratio` for Main can remain ladder-weighted. Tactical needs a separate `tactical_effective_rr` and must not pass a Main gate by borrowing trend-runner TP2/TP3 assumptions.

### Decision 4: Tactical exit lifecycle is state-machine driven
```

Full source: openspec/changes/add-tactical-exit-track/design.md

## openspec/changes/add-tactical-exit-track/tasks.md

- Source: openspec/changes/add-tactical-exit-track/tasks.md
- Lines: 1-12
- SHA256: bffd6c32163cd060de230f6b6a47944a8fa6140bc039b0040dab6346f6d4d84e

```md
## Implementation Tasks

- [ ] Add Tactical configuration flags, defaults, and metadata fields (`track`, `exit_profile`, Tactical source, Tactical R:R/EV, cost gate, risk state).
- [ ] Implement Judge Tactical classification before final R:R/EV gates, including Main strong-trend quality gate, downgrade handling, shadow-only handling, and hard veto handling.
- [ ] Implement Tactical plan math: structure stop, stop caps, sizing/leverage limits, TP profile, net EV, and cost coverage gate.
- [ ] Add Tactical slot/concurrency handling and risk governor: daily -10U hard stop, volatility-based concurrency, loss streak pause, quality breaker, and execution/protection failure pause.
- [ ] Extend Executor local lifecycle for Tactical positions: thesis-health checks, Tactical partial/protect exits, invalidation exit, max hold, and no-add enforcement while keeping exchange SL ownership.
- [ ] Propagate Tactical metadata through trade decisions, positions, execution results, PnL resolution events, Reviewer trade history, and Telegram/status surfaces where relevant.
- [ ] Extend counterfactual and replay tooling to resolve Tactical candidates with Tactical exit assumptions and separate Main/Tactical reporting.
- [ ] Add focused tests for classifier routing, hard vetoes, Tactical R:R isolation, cost gate, exit-state transitions, risk governor breakers, event attribution, and replay segmentation.
- [ ] Add WLD-like classifier and replay fixtures covering aligned-but-weak Main rejection, Tactical downgrade, Tactical shadow-only, Tactical TP1, and Tactical capped-stop outcomes.
- [ ] Run replay/shadow validation before enabling live Tactical opens; document rollout flags and rollback path.
```

## openspec/changes/add-tactical-exit-track/specs/counterfactual-pnl/spec.md

- Source: openspec/changes/add-tactical-exit-track/specs/counterfactual-pnl/spec.md
- Lines: 1-38
- SHA256: 78248108db0fec8bb9da9c0185eda39d879643c2127729f1ad12fc9feae16624

```md
## ADDED Requirements

### Requirement: Counterfactual records distinguish Main and Tactical outcomes
The system SHALL include track and exit-profile metadata in accepted and rejected counterfactual records. Replay and counterfactual reports SHALL be able to compare Main-only, Tactical-only, and incremental Tactical outcomes without mixing their PnL or win-rate samples.

#### Scenario: Rejected Tactical shadow carries track metadata
- **WHEN** a rejected or shadowed Tactical candidate is recorded
- **THEN** the counterfactual record SHALL include `track=tactical`, `exit_profile=tactical_v1`, Tactical R:R, Tactical EV, and Tactical source reason

#### Scenario: Main and Tactical replay are separable
- **WHEN** a replay report calculates PnL deltas
- **THEN** it SHALL provide separate buckets for Main and Tactical
- **AND** it SHALL NOT treat Tactical wins as Main Trend evidence

### Requirement: Tactical counterfactual exit model matches Tactical lifecycle
The system SHALL resolve Tactical counterfactual outcomes with Tactical max hold, Tactical TP/SL profile, and Tactical cost assumptions. Tactical counterfactuals MUST NOT use the 24h Main Trend hold window unless explicitly configured as a diagnostic comparison.

#### Scenario: Tactical shadow expires at Tactical max hold
- **WHEN** a Tactical counterfactual position remains unresolved past the configured Tactical max hold
- **THEN** the resolver SHALL close or mark it according to the Tactical max-hold rule
- **AND** the result SHALL record `tactical_max_hold` as the resolution reason

#### Scenario: Diagnostic Main comparison is labelled
- **WHEN** a report compares the same signal under Main and Tactical exit models
- **THEN** each result SHALL be labelled with the exit model used
- **AND** aggregate conclusions SHALL use the Tactical-labelled result for Tactical decisions

### Requirement: Tactical sample honesty gates
The system SHALL apply the existing counterfactual honesty gate principles to Tactical buckets. Tactical conclusions with fewer than 30 samples MUST be marked insufficient sample, 30-99 samples MUST be low confidence, and actionable Tactical conclusions MUST require sufficient sample size and net PnL confidence interval not crossing zero.

#### Scenario: Thin Tactical sample refuses conclusion
- **WHEN** a Tactical replay bucket has fewer than 30 samples
- **THEN** the report SHALL mark it insufficient sample
- **AND** it SHALL NOT recommend increasing Tactical exposure from that bucket

#### Scenario: Tactical actionable requires confidence
- **WHEN** a Tactical replay bucket has sufficient samples but its net PnL confidence interval crosses zero
- **THEN** the report SHALL NOT mark the bucket actionable
```

## openspec/changes/add-tactical-exit-track/specs/ladder-weighted-rr/spec.md

- Source: openspec/changes/add-tactical-exit-track/specs/ladder-weighted-rr/spec.md
- Lines: 1-19
- SHA256: 9635177a5ea909d2fa936259a10362bb12857797cc3e9ba7d2c6636ffba2a6c8

```md
## ADDED Requirements

### Requirement: Tactical R:R isolation from ladder-weighted Main R:R
The system SHALL keep Main Trend ladder-weighted `effective_risk_reward_ratio` separate from Tactical R:R. Tactical plans MUST expose their own Tactical R:R and EV fields and MUST NOT use Main Trend TP2/TP3 ladder assumptions for Tactical acceptance, sizing, ranking, or EV gates.

#### Scenario: Main ladder remains Main-only
- **WHEN** a candidate is classified as `track=main`
- **THEN** existing ladder-weighted R:R behavior MAY be used according to the Main Trend configuration
- **AND** the plan SHALL remain compatible with the existing ladder-weighted R:R requirements

#### Scenario: Tactical uses Tactical R:R
- **WHEN** a candidate is classified as `track=tactical`
- **THEN** acceptance and ranking SHALL use Tactical R:R and Tactical EV fields
- **AND** `effective_risk_reward_ratio` from Main ladder math SHALL NOT be the deciding Tactical acceptance value

#### Scenario: Reclassification recalculates payoff fields
- **WHEN** a Main candidate is downgraded into Tactical
- **THEN** the system SHALL recalculate stop distance, TP profile, net profit, net loss, R:R, and EV using the Tactical profile
- **AND** the plan SHALL retain both original Main diagnostic R:R and final Tactical R:R for audit
```

## openspec/changes/add-tactical-exit-track/specs/low-rr-early-trailing/spec.md

- Source: openspec/changes/add-tactical-exit-track/specs/low-rr-early-trailing/spec.md
- Lines: 1-18
- SHA256: f248a12e6ba24a5f97eb483bc91bad379467ac695eae320e7e08beea875ee6f1

```md
## ADDED Requirements

### Requirement: Tactical exit profile does not reuse low_rr early trailing semantics
The system SHALL treat Tactical as a separate exit profile from `low_rr_extra`. Existing low-R:R early trailing behavior SHALL continue to apply only to low-R:R positions unless a position explicitly has `track=tactical`, in which case the Tactical exit lifecycle SHALL control protection, partial exits, thesis invalidation, and max hold.

#### Scenario: Low-R:R behavior remains unchanged
- **WHEN** a position has `slot_type=low_rr_extra` and no `track=tactical`
- **THEN** existing low-R:R early trailing activation and distance settings SHALL apply unchanged

#### Scenario: Tactical profile takes precedence
- **WHEN** a position has `track=tactical`
- **THEN** the Tactical exit lifecycle SHALL decide early protection and exit actions
- **AND** the generic low-R:R early trailing branch SHALL NOT override Tactical thesis-health or max-hold decisions

#### Scenario: Tactical is not reported as low-R:R by default
- **WHEN** a Tactical trade is recorded by Reviewer
- **THEN** it SHALL be segmented by `track=tactical`
- **AND** it SHALL NOT be counted as `is_low_rr=true` unless it also explicitly satisfies the low-R:R policy
```

## openspec/changes/add-tactical-exit-track/specs/pnl-resolution-bus-events/spec.md

- Source: openspec/changes/add-tactical-exit-track/specs/pnl-resolution-bus-events/spec.md
- Lines: 1-28
- SHA256: b9de56ae15e6ebc581bf5ac927abc1a689b355cdb8e6e8a9e33a420f1b18ffef

```md
## ADDED Requirements

### Requirement: PnL resolution events carry track and exit-profile metadata
The system SHALL propagate `track`, `exit_profile`, `slot_type`, and Tactical close/protection reason through `pnl_resolved` and `pnl_mismatch` events when those fields are known from the position, correction, execution result, or resolver evidence.

#### Scenario: Tactical pnl_resolved includes track fields
- **WHEN** a Tactical position receives a final `pnl_resolved` event
- **THEN** the event payload SHALL include `track=tactical` and `exit_profile=tactical_v1`
- **AND** Reviewer and Judge SHALL be able to consume the event without falling back to Main attribution

#### Scenario: Missing legacy fields remain backward compatible
- **WHEN** an older event lacks `track` or `exit_profile`
- **THEN** consumers SHALL default to existing Main-compatible behavior
- **AND** they SHALL NOT fail processing because the new fields are absent

### Requirement: Tactical close cause is preserved through resolution
The system SHALL preserve Tactical local close causes through asynchronous PnL resolution. Tactical close causes MUST coexist with existing `close_cause`, `final_close_cause`, `is_strategy_stop`, `close_evidence`, and `resolution_id` fields.

#### Scenario: Tactical invalidation close survives resolver upgrade
- **WHEN** a Tactical position is locally closed because of thesis invalidation
- **AND** the PnL is later upgraded by resolver
- **THEN** the final event SHALL still expose the Tactical invalidation reason
- **AND** existing final close cause fields SHALL remain present

#### Scenario: Exchange SL preserves Tactical attribution
- **WHEN** a Tactical position closes through exchange protective SL
- **THEN** the final PnL event SHALL include the exchange SL cause
- **AND** it SHALL still include `track=tactical` so Tactical risk metrics receive the loss
```

## openspec/changes/add-tactical-exit-track/specs/short-main-path-risk-guard/spec.md

- Source: openspec/changes/add-tactical-exit-track/specs/short-main-path-risk-guard/spec.md
- Lines: 1-22
- SHA256: d069b289a73f93cad4a00daab98a538548a99a63d5105460f8f17774296e9092

```md
## ADDED Requirements

### Requirement: Tactical short candidates obey shared short risk gates
The system SHALL apply the shared short structural risk gate to Tactical short candidates before publishing any executable short decision. Tactical MUST NOT bypass daily-bias, range-position, pre-move, RSI, score, higher-timeframe vote, or LLM reversal-risk attribution semantics.

#### Scenario: Tactical short rejected by daily bias
- **WHEN** a Tactical `open_short` candidate lacks required bearish daily bias
- **THEN** the system SHALL reject the candidate or keep it as non-executable shadow data
- **AND** the rejection attribution SHALL include the shared short gate reason

#### Scenario: Tactical short pass retains metadata
- **WHEN** a Tactical `open_short` candidate passes the shared short structural gate
- **THEN** the accepted decision SHALL include short gate pass metadata
- **AND** the decision SHALL also include `track=tactical`

### Requirement: Tactical downgrade cannot convert hard veto into soft veto
The system SHALL distinguish downgrade signals from hard vetoes. A short guard, 15m block, regime flat no-thesis block, or explicit reversal thesis SHALL remain a hard veto even if the candidate would otherwise satisfy Tactical sizing or cost rules.

#### Scenario: Hard veto remains hard after Tactical classification
- **WHEN** a candidate has a hard veto and also satisfies Tactical stop and cost requirements
- **THEN** the system SHALL reject the candidate
- **AND** it SHALL NOT publish an executable Tactical open
```

## openspec/changes/add-tactical-exit-track/specs/tactical-exit-track/spec.md

- Source: openspec/changes/add-tactical-exit-track/specs/tactical-exit-track/spec.md
- Lines: 1-176
- SHA256: c32eb37d41dce3cc794cfd3a4e236605b5ed73794baf0be82cda44a431473fde

[TRUNCATED]

```md
## ADDED Requirements

### Requirement: Main and Tactical track classification
The system SHALL classify every executable open candidate as `track=main` or `track=tactical` before final R:R and EV acceptance gates. Main Trend SHALL be selected only when the trade direction is aligned with higher-timeframe bias and daily bias, the 15m timing signal is not opposing the trade, and the candidate passes the Main Trend quality gate. Tactical SHALL be available only for directionally valid weak or mixed-environment candidates that do not qualify for Main Trend, or for an explicitly allowed subset of structure-backed hold/reject candidates.

#### Scenario: Strong trend remains Main
- **WHEN** a candidate has trade-direction aligned higher-timeframe bias and daily bias
- **AND** the 15m signal is not opposing the trade
- **AND** the candidate passes the Main Trend quality gate
- **THEN** the system marks the plan with `track=main` and `exit_profile=trend_runner`
- **AND** the candidate uses existing Main Trend TP/SL and R:R semantics

#### Scenario: Weak environment candidate downgrades to Tactical
- **WHEN** a candidate is directionally valid but fails Main Trend protection because the environment is weak or mixed
- **AND** no hard Tactical veto is present
- **THEN** the system MAY mark the plan with `track=tactical` and `exit_profile=tactical_v1`
- **AND** the candidate MUST be evaluated with Tactical R:R, EV, sizing, and exit lifecycle semantics

#### Scenario: Hold or reject promotion is narrow
- **WHEN** a hold or rejected candidate has a compatible reason such as `rr_below_floor`, confidence in the 40-60 range with strong structure, or light score-below-threshold
- **AND** the candidate has explicit structure support for the trade direction
- **THEN** the system MAY create a Tactical candidate
- **AND** the source reason MUST be recorded in `tactical_source`

### Requirement: Main Trend quality gate
The system SHALL require a Main Trend quality gate before assigning `track=main`. Directional alignment alone MUST NOT be sufficient for Main Trend. The quality gate MUST reject or downgrade candidates with weak regime quality, trend-exhaustion risk, LLM reversal risk, weak volume/OI/microstructure confirmation, or provenance below the configured quality threshold.

#### Scenario: Directional alignment alone is insufficient
- **WHEN** a candidate has higher-timeframe, daily, and 15m direction aligned with the trade
- **BUT** the effective regime is mixed or weak and no configured high-quality override is present
- **THEN** the system SHALL NOT classify the candidate as Main solely because of directional alignment
- **AND** it SHALL evaluate the candidate for Tactical or reject it according to Tactical eligibility

#### Scenario: WLD-like weak aligned short is not Main
- **WHEN** an `open_short` candidate is directionally bearish across higher-timeframe, daily, and 15m signals
- **AND** the candidate also has mixed regime, weak volume or OI confirmation, LLM short reversal risk, trend-exhaustion warnings, or very weak provenance
- **THEN** the system SHALL NOT mark the plan as `track=main`
- **AND** it SHALL either downgrade to Tactical with Tactical R:R/EV or reject live execution and record the candidate for shadow/replay

#### Scenario: Too many quality weaknesses become shadow-only
- **WHEN** a candidate fails the Main Trend quality gate and also fails Tactical quality thresholds such as provenance floor, cost gate, liquidity gate, or thesis-health precheck
- **THEN** the system SHALL NOT publish a live open
- **AND** it SHALL record the decision as shadow-only or rejected with quality-gate attribution

### Requirement: Tactical hard vetoes
The system SHALL NOT open Tactical trades when a hard veto is present. Hard vetoes MUST include regime flat with no directional thesis, 15m opposing block, explicit opposing thesis, short structural gate rejection, extreme/news pause, same-symbol existing position or pending open, insufficient liquidity, unacceptable spread, and execution/protection integrity failure.

#### Scenario: 15m opposing block cannot be bypassed
- **WHEN** a candidate is otherwise eligible for Tactical but the 15m entry timing gate blocks the trade as opposing the direction
- **THEN** the system SHALL publish or record a hold/reject decision
- **AND** it SHALL NOT downgrade the candidate into a live Tactical open

#### Scenario: Same symbol cannot stack across tracks
- **WHEN** a symbol already has an open or pending Main position
- **AND** a Tactical candidate for the same symbol appears
- **THEN** the Tactical candidate SHALL be rejected with same-symbol stacking attribution

#### Scenario: Short structural rejection is a hard veto
- **WHEN** an `open_short` candidate fails the shared short structural gate
- **THEN** the system SHALL NOT publish a Tactical short for that candidate
- **AND** the rejection SHALL preserve the short gate reason

### Requirement: Tactical plan uses structure-bounded risk
The system SHALL build Tactical plans with a structure-bounded stop loss instead of inheriting a wider Main Trend stop. Tactical stop distance MUST be capped at `0.6R_main` and by configured ATR/percentage caps. A Tactical stop distance at or below `0.4R_main` SHALL be marked as `tactical_stop_quality=very_near`.

#### Scenario: Tactical stop is capped relative to Main
- **WHEN** the Main plan stop would risk 1.0R_main
- **AND** the Tactical structure stop would exceed `0.6R_main`
- **THEN** the Tactical plan SHALL be rejected or adjusted to a valid structure stop within the configured cap
- **AND** the plan SHALL record the cap decision

#### Scenario: Very near stop enables larger size
- **WHEN** the Tactical structure stop is at or below `0.4R_main`
- **AND** no quality, cost, liquidity, or veto problem is present
- **THEN** the system MAY size the Tactical position up to 100% of Main margin
- **AND** the plan SHALL record `tactical_stop_quality=very_near`

### Requirement: Tactical sizing and leverage limits
The system SHALL size Tactical positions independently from Main. The default Tactical margin SHALL be 70% of the equivalent Main margin. Tactical leverage MUST NOT exceed 5x. Tactical positions MUST NOT be eligible for add-to-position actions.

```

Full source: openspec/changes/add-tactical-exit-track/specs/tactical-exit-track/spec.md

