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
