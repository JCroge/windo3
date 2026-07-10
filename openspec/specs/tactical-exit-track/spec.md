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

#### Scenario: Default Tactical sizing
- **WHEN** a candidate is accepted as Tactical and the stop is not very near
- **THEN** the plan margin SHALL be 70% of the Main-equivalent margin or lower
- **AND** leverage SHALL be capped at 5x

#### Scenario: No Tactical add
- **WHEN** a `position_analyst` or other source proposes adding to an open Tactical position
- **THEN** the system SHALL reject the add request
- **AND** it SHALL preserve the existing Tactical position lifecycle

### Requirement: Tactical R:R and net EV are isolated from Main
The system SHALL calculate Tactical R:R and EV from Tactical stop, Tactical TP profile, expected local partial exits, fees, funding approximation, and slippage assumptions. Tactical acceptance MUST require net EV greater than zero and TP1 net return covering fee plus slippage by at least 4x. Tactical MUST NOT use Main Trend ladder TP2/TP3 assumptions to pass acceptance gates.

#### Scenario: Tactical cannot pass using Main ladder R:R
- **WHEN** a candidate has Main ladder `effective_risk_reward_ratio` above the Main floor
- **BUT** its Tactical TP1/structure-based net EV is not positive
- **THEN** the Tactical candidate SHALL be rejected
- **AND** the rejection SHALL reference Tactical EV or cost coverage, not Main R:R

#### Scenario: Cost coverage gate blocks low net target
- **WHEN** Tactical TP1 gross distance is too small to cover configured fee plus slippage by at least 4x
- **THEN** the system SHALL reject the Tactical plan
- **AND** it SHALL record `tactical_cost_gate=fail`

### Requirement: Tactical local exit lifecycle
The system SHALL manage Tactical exits with local TP logic and exchange-side protective SL. Tactical exit state MUST evaluate thesis health every minute, on relevant market events, and with heavier weighting on each 15m candle close. Tactical maximum hold time SHALL be 90 minutes.

#### Scenario: Healthy Tactical thesis continues
- **WHEN** a Tactical position remains thesis-healthy and has not exceeded 90 minutes
- **THEN** the system MAY keep the position open toward staged Tactical TP levels
- **AND** protective SL movement SHALL remain ratcheted in the favorable direction only

#### Scenario: Weakened Tactical thesis exits if no progress
- **WHEN** a Tactical position is thesis-weakened and makes no sufficient progress for the configured 30-45 minute window
- **THEN** the system SHALL protect or close the position according to the Tactical exit profile
- **AND** the close or protection reason SHALL be recorded

#### Scenario: Invalidated Tactical thesis exits fast
- **WHEN** a Tactical thesis becomes invalidated by structure, 15m close, opposing signal, or hard risk event
- **THEN** the system SHALL request immediate exit
- **AND** market execution MAY be used only when spread and liquidity checks pass

#### Scenario: Tactical max hold closes position
- **WHEN** a Tactical position reaches 90 minutes of age
- **THEN** the system SHALL close the position or issue a forced local close request
- **AND** the close reason SHALL be `tactical_max_hold`

### Requirement: Tactical risk governor and circuit breakers
The system SHALL maintain Tactical-specific risk controls independent from Main. Tactical risk controls MUST include a daily loss hard stop of -10U, dynamic concurrency of max 2 in calm markets and max 1 in high volatility, pause in extreme/news regimes, 3 consecutive loss cooldown for 1 hour, 20-trade quality failure pause, and immediate pause on execution or protection failure.

#### Scenario: Tactical daily loss hard stop
- **WHEN** Tactical realized plus resolved daily PnL reaches -10U or lower
- **THEN** the system SHALL reject new Tactical opens for the rest of the configured day
- **AND** Main opens SHALL remain eligible unless a shared system-wide risk halt is active

#### Scenario: Dynamic concurrency caps Tactical
- **WHEN** market volatility is calm
- **THEN** at most 2 Tactical positions MAY be open or pending
- **WHEN** market volatility is high
- **THEN** at most 1 Tactical position MAY be open or pending

#### Scenario: Loss streak pauses Tactical
- **WHEN** three consecutive Tactical trades close at a loss
- **THEN** the system SHALL pause Tactical opens for 1 hour
- **AND** it SHALL record the cooldown reason

#### Scenario: Execution protection failure pauses Tactical
- **WHEN** a Tactical open, reduce, close, or protective SL update reports execution/protection failure
- **THEN** the system SHALL pause new Tactical opens immediately
- **AND** it SHALL emit risk attribution for the failure

### Requirement: Tactical performance accounting
The system SHALL keep Tactical performance metrics separate from Main Trend metrics. Tactical success SHALL be evaluated on recent 30 Tactical trades with win rate at least 55%, profit factor at least 1.2, and average PnL greater than zero before increasing exposure or frequency.

#### Scenario: Tactical metrics do not pollute Main
- **WHEN** Reviewer or replay calculates Main Trend performance
- **THEN** trades with `track=tactical` SHALL be excluded from Main-only metrics
- **AND** Tactical metrics SHALL be available as a separate segment

#### Scenario: Tactical quality failure pauses the track
- **WHEN** the recent Tactical sample reaches 20 trades and fails configured quality thresholds
- **THEN** the system SHALL pause or downgrade Tactical opens
- **AND** it SHALL preserve Main Trend behavior

### Requirement: Tactical metadata propagation
The system SHALL propagate Tactical metadata through plan, trade decision, position record, execution result, PnL resolution, Reviewer trade history, and counterfactual records. Required metadata SHALL include `track`, `exit_profile`, `tactical_source`, `tactical_rr`, `tactical_ev`, `tactical_cost_gate`, `tactical_risk_state`, and Tactical close/protection reason when applicable.

#### Scenario: Accepted Tactical open carries metadata
- **WHEN** Judge publishes an accepted Tactical open
- **THEN** the decision plan and attribution SHALL include `track=tactical` and `exit_profile=tactical_v1`
- **AND** Executor SHALL persist those fields into the position record

#### Scenario: Tactical close carries close reason
- **WHEN** a Tactical position closes through TP, SL, thesis invalidation, max hold, or risk governor action
- **THEN** the resulting execution and PnL events SHALL include the Tactical close reason
- **AND** Reviewer SHALL persist it for segmented analysis
