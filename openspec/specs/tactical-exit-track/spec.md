## Purpose

Define Tactical track admission, exits, risk isolation, metadata propagation, and live sidecar hard-veto behavior.

## Requirements

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

#### Scenario: Tactical fields are persisted on live sidecar position
- **WHEN** a mapped Tactical shadow record is opened by the sidecar
- **THEN** the persisted sidecar position SHALL include `track=tactical`, `exit_profile=tactical_v1`, `tactical_source`, `tactical_max_hold_minutes`, `entry_ref`, and sidecar gate metadata
- **AND** local sidecar monitoring SHALL evaluate Tactical exit rules from the persisted position rather than treating it as a generic position

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

#### Scenario: Exchange-flat reconciliation records a sidecar close event
- **WHEN** sidecar monitoring proves that an active sidecar-owned local position is flat on the exchange
- **THEN** it SHALL close the sidecar owner record and remove the local sidecar position
- **AND** it SHALL write a sidecar ledger close event or pending external close event with the original shadow id, symbol, side, opened timestamp, closed timestamp, amount, leverage, and protection identifiers
- **AND** it SHALL leave the exchange-derived final PnL resolution pending when fills are not yet resolved locally

#### Scenario: Main process is not restarted
- **WHEN** the sidecar starts for the 24-hour run
- **THEN** the existing `run_agents.py` process SHALL remain running
- **AND** the sidecar start procedure SHALL NOT require changing Main Tactical `.env` gates or restarting Main

### Requirement: Same-account owner isolation
The system SHALL support same-account sidecar deployment by recording sidecar ownership and preventing Main from taking ownership of sidecar account objects.

#### Scenario: Main sync skips sidecar-owned position
- **WHEN** Main `sync_positions()` sees an OKX account-level position
- **AND** the position matches an active sidecar ownership record
- **THEN** Main SHALL NOT backfill that position into Main `positions.json`
- **AND** Main SHALL record or log that the position was ignored as sidecar-owned

#### Scenario: Main migration preserves foreign sidecar SL algo
- **WHEN** Main OKX algo migration sees a pending SL algo
- **AND** the algo has a sidecar or otherwise foreign owner tag
- **THEN** Main SHALL NOT cancel, replace, or adopt that algo
- **AND** Main SHALL continue processing Main-owned algos normally

#### Scenario: Same-account same-symbol guard blocks inseparable exposure
- **WHEN** a sidecar Tactical shadow record targets a symbol that already has non-sidecar account exposure
- **THEN** the sidecar SHALL reject or defer that record without opening new exposure
- **AND** it SHALL write a sidecar audit event with `same_symbol_account_exposure`

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

### Requirement: Live sidecar admission SHALL enforce Tactical hard vetoes
The live sidecar admission path SHALL enforce Tactical hard vetoes that protect against strategy drift, stale decisions, same-symbol stacking, and unbounded duplicate exposure. A Tactical Shadow event SHALL create live Sidecar exposure only when it carries a supported, fresh, internally consistent frozen admission decision produced by Judge. The Sidecar SHALL NOT recompute indicators or strategy gates. A Tactical Shadow event that would create inseparable same-symbol exposure in the live Sidecar SHALL be rejected before order submission and recorded with attribution.

#### Scenario: Frozen policy rejection blocks live admission
- **WHEN** a Tactical Shadow event is stamped ineligible, stale, malformed, unsupported, or inconsistent with its canonical policy evidence
- **THEN** live Sidecar admission SHALL reject the event before capacity or exchange calls
- **AND** the rejection SHALL preserve the frozen policy version, tier, evidence, and specific failure reason

#### Scenario: Existing sidecar owner blocks duplicate live admission
- **WHEN** a Tactical Shadow event targets a symbol and side with an already open sidecar owner row
- **THEN** live Sidecar admission SHALL reject the event before order submission
- **AND** the rejection SHALL preserve attribution identifying same-symbol sidecar activity

#### Scenario: Existing account exposure blocks sidecar admission
- **WHEN** a Tactical Shadow event targets a symbol that already has Main, manual, unknown, or otherwise non-sidecar account exposure
- **THEN** live Sidecar admission SHALL reject the event with same-symbol exposure attribution
- **AND** it SHALL NOT convert the candidate into a sidecar add-to-position action

#### Scenario: Verified policy pass retains execution safety gates
- **WHEN** a fresh eligible frozen decision passes policy verification
- **THEN** Sidecar SHALL still enforce active capacity, account exposure, symbol halt, balance, entry drift, slippage, order capability, geometry, and attached protective-stop verification
- **AND** no policy field SHALL bypass those safety checks
