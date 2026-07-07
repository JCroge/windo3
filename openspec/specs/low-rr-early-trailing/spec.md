## ADDED Requirements

### Requirement: Low RR slot early trailing activation
The system SHALL activate trailing stop for low_rr slot positions when unrealized profit reaches +0.5R, without waiting for TP1 to be hit.

#### Scenario: Trailing activates at +0.5R for low_rr position
- **WHEN** a position opened via low_rr slot (low_rr_extra / long_bullish_low_rr / long_aligned_low_rr) reaches +0.5R unrealized profit
- **THEN** trailing stop activates with distance 0.3R from highest price since entry

#### Scenario: Trailing SL ratchets upward
- **WHEN** trailing is active and price makes new high
- **THEN** trailing SL moves to (new_highest - 0.3R), never moves down

#### Scenario: Trailing SL triggers exit
- **WHEN** price retraces to trailing SL level
- **THEN** position is fully closed at trailing SL price

#### Scenario: TP1 still triggers if reached
- **WHEN** price reaches TP1 before trailing SL is hit
- **THEN** normal partial_tp_1 logic fires (50% reduce), trailing continues on remainder

### Requirement: Position slot marking
The system SHALL record the slot type in position dict at open time so exit logic can differentiate low_rr from main positions.

#### Scenario: Low RR slot position is marked
- **WHEN** a position is opened via low_rr_extra / long_bullish_low_rr / long_aligned_low_rr policy
- **THEN** position dict contains `slot` field with value identifying it as low_rr

#### Scenario: Main slot position is not affected
- **WHEN** a position is opened via main slot (size=30, lev=10x)
- **THEN** position dict `slot` field is absent or set to `main`, and existing trailing logic applies unchanged

### Requirement: Early trailing parameters are configurable
The system SHALL use configurable parameters for early trailing activation threshold and distance.

#### Scenario: Default parameters
- **WHEN** no override is configured
- **THEN** activation threshold is 0.5R and trailing distance is 0.3R

#### Scenario: Parameters adjustable via config
- **WHEN** config specifies different values for `low_rr_trail_start_r` and `low_rr_trail_dist_r`
- **THEN** those values are used instead of defaults
