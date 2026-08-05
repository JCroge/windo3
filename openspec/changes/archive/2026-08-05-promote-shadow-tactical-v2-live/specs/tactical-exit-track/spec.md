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
The system SHALL maintain one Tactical-specific admission governor independent from Main. The governor MUST include a rolling 24-hour final-PnL limit of `-15U`, a fixed maximum of three active-or-pending Tactical slots, a three-consecutive-loss cooldown of 60 minutes, and a non-expiring integrity halt for unresolved execution, protection, or ownership ambiguity. Admission pauses MUST block only new Tactical risk; they MUST NOT block any exit or force-close existing Tactical positions solely because the threshold was reached.

#### Scenario: Rolling 24-hour loss pauses new Tactical opens
- **WHEN** the sum of final Tactical PnL resolutions in the preceding 24 hours reaches `-15U` or lower
- **THEN** the governor SHALL reject new Tactical opens until the rolling sum recovers above the threshold
- **AND** Main opens and existing Tactical exits SHALL remain eligible unless a shared system-wide halt is active

#### Scenario: Governor consumes only final idempotent PnL
- **WHEN** a Tactical close is pending, estimated, duplicated, or later corrected
- **THEN** the governor SHALL count only `pnl_is_final=true` events keyed by `resolution_id`
- **AND** a correction SHALL apply only the PnL delta rather than count the full trade again

#### Scenario: Three Tactical slots are fixed across volatility regimes
- **WHEN** fewer than three Tactical positions or pending entries occupy the Tactical pool
- **THEN** a new eligible Tactical intent MAY use an available slot regardless of calm or high-volatility labeling
- **AND** extreme/news and shared account-integrity vetoes MAY still block admission

#### Scenario: Loss streak pauses and consumes the streak
- **WHEN** three consecutive final Tactical episodes close at a loss
- **THEN** the system SHALL pause Tactical opens for 60 minutes
- **AND** it SHALL persist the reason and reset the consumed streak before post-pause accumulation

#### Scenario: Integrity failure requires reconciliation
- **WHEN** Tactical entry, TP/SL protection, close ownership, or exchange position state cannot be proven
- **THEN** the system SHALL halt new Tactical admission immediately
- **AND** the halt SHALL remain active until reconciliation proves a safe state rather than expiring on a timer

### Requirement: Tactical performance accounting
The system SHALL keep Tactical performance metrics separate from Main Trend metrics and SHALL calculate them from deduplicated filled episodes with final PnL. Tactical performance evaluation SHALL distinguish normal TP/SL/max-hold outcomes, risk-forced exits, non-filled intents, and shadow/live execution mismatches. At least 30 final Tactical episodes SHALL be required before drawing the first live performance conclusion; the first cohort SHALL NOT auto-resize or auto-pause solely from an incomplete quality window.

#### Scenario: Tactical metrics do not pollute Main
- **WHEN** Reviewer or replay calculates Main Trend performance
- **THEN** trades with `track=tactical` SHALL be excluded from Main-only metrics
- **AND** Tactical metrics SHALL be available as a separate segment

#### Scenario: Repeated and non-filled intents do not inflate win rate
- **WHEN** repeated rows share one episode or a Tactical intent expires without executable-price fill
- **THEN** the system SHALL NOT count those rows as separate filled Tactical trades
- **AND** it SHALL report the non-filled outcomes separately

#### Scenario: First performance conclusion waits for final sample
- **WHEN** fewer than 30 Tactical episodes have final PnL
- **THEN** the system SHALL label the performance sample insufficient
- **AND** it SHALL NOT infer success from raw shadow row count or automatically increase exposure

### Requirement: Tactical metadata propagation
The system SHALL propagate Tactical V2 metadata through the canonical intent, position record, execution result, PnL resolution, Reviewer trade history, shadow transitions, and operational status. Required metadata SHALL include `track`, `exit_profile`, `strategy_owner`, `intent_id`, `episode_id`, `plan_hash`, `tactical_source`, `tactical_rr`, `tactical_ev`, `tactical_cost_gate`, entry terminal reason, protection identity/state, and Tactical or risk-forced close reason when applicable.

#### Scenario: Accepted Tactical open carries V2 ownership metadata
- **WHEN** a Tactical V2 entry fills
- **THEN** the position and execution result SHALL include `track=tactical`, `exit_profile=tactical_v2`, and `strategy_owner=tactical_v2`
- **AND** they SHALL preserve intent id, episode id, and plan hash

#### Scenario: Tactical close carries final reason
- **WHEN** a Tactical position closes through TP, SL, max hold, exchange reconciliation, or system safety
- **THEN** the resulting execution and PnL events SHALL include the Tactical close or risk-forced reason
- **AND** Reviewer SHALL persist it for segmented analysis

### Requirement: 24-hour stop semantics
The legacy sidecar runner SHALL remain resident until explicitly stopped and SHALL ignore the deprecated duration argument. It SHALL provide an owner-bound stop path, and Tactical V2 cutover SHALL stop new sidecar admission before draining existing proven exposure.

#### Scenario: Deprecated duration does not stop resident monitoring
- **WHEN** the sidecar starts with a legacy duration argument
- **THEN** it SHALL continue event polling and open-position monitoring until explicitly stopped
- **AND** persisted legacy `stop_at` state SHALL NOT terminate the process

#### Scenario: Stop command handles sidecar-owned exposure
- **WHEN** a sidecar stop command is run
- **THEN** it SHALL cancel sidecar-owned pending orders where ownership can be proven
- **AND** it SHALL close sidecar-owned open positions where ownership can be proven
- **AND** it SHALL refuse to touch positions whose ownership cannot be proven from sidecar state/order tags
