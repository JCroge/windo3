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

#### Scenario: Historical episode terminates after a newer epoch exists
- **WHEN** an in-flight intent belongs to an older episode and a reset has already made a newer episode current for the same symbol and direction
- **AND** the older intent later reaches TP, SL, max hold, or another terminal outcome
- **THEN** the older episode SHALL be consumed exactly once by its own episode id
- **AND** the newer current epoch SHALL remain unchanged across event replay and process restart

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
- **AND** it SHALL NOT submit a second entry for the intent

#### Scenario: Unknown submission fails closed
- **WHEN** restart recovery cannot prove whether a submitting intent created exchange exposure
- **THEN** the system SHALL halt new Tactical admission for integrity reconciliation
- **AND** it SHALL NOT retry the entry until the ambiguity is resolved

#### Scenario: Temporarily invisible entry is rechecked without resubmission
- **WHEN** an exact deterministic client-order lookup succeeds but returns no order before the persisted visibility deadline
- **THEN** the system SHALL keep the intent in reconciliation and recheck it periodically
- **AND** it SHALL NOT submit another entry or reset the visibility deadline across restart

#### Scenario: Entry integrity halt clears only from complete proof
- **WHEN** an `entry_reconciliation_unknown` or `entry_cancel_unproven` halt is active
- **THEN** the system SHALL periodically re-run exact owner, order, position, quantity, and protection reconciliation
- **AND** it MAY clear the halt only after the intent reaches a proven terminal, protected, or exchange-flat final state

#### Scenario: Deferred cancellation preserves its terminal reason
- **WHEN** a pre-fill terminal condition starts cancellation but the cancel result cannot be proven
- **THEN** the system SHALL persist the original cancel reason while the intent remains integrity halted
- **AND** a later open-order observation SHALL retry cancellation rather than restore the intent to normal pending entry

### Requirement: Shadow and live SHALL share lifecycle semantics
The Shadow Tactical adapter and live adapter SHALL consume the same intent, episode, entry, and exit state transitions. Shadow SHALL count a fill only after executable-price touch and SHALL use the same full TP1, full SL, and 90-minute max-hold outcomes. Adapter differences SHALL be limited to exchange I/O and explicitly attributed fill/protection variance.

#### Scenario: Untouched shadow entry is not counted as filled
- **WHEN** a shadow intent never receives an executable-price touch before expiry or invalidation
- **THEN** shadow SHALL record a non-filled terminal outcome
- **AND** it SHALL NOT include the intent in filled-trade win-rate or PnL statistics

#### Scenario: Shadow and live transitions can be compared per intent
- **WHEN** shadow and live process the same intent
- **THEN** the operational ledger SHALL expose both transition sequences keyed by intent id
- **AND** every mismatch SHALL have an attributed reason such as exchange fill, account capacity, order rejection, or system risk
