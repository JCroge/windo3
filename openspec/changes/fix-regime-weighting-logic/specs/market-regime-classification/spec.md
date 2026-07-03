## MODIFIED Requirements

### Requirement: Weighted regime calculation with BTC/ETH anchors

The system SHALL compute market regime using weighted aggregation of symbol directions with BTC/ETH anchor bias. The weighted_total SHALL only include anchor weights when the corresponding bias is 'bullish' or 'bearish', not 'neutral'.

#### Scenario: BTC bias is bullish
- **WHEN** BTC bias is 'bullish' and 10 of 30 symbols are bullish
- **THEN** weighted_bullish = 10 + 2.0 = 12.0, weighted_total = 30 + 2.0 = 32.0, bullish_pct = 37.5%

#### Scenario: BTC bias is neutral
- **WHEN** BTC bias is 'neutral' and 10 of 30 symbols are bullish
- **THEN** weighted_total = 30 (no anchor weight added), bullish_pct calculated without BTC weight inflation

#### Scenario: ETH bias is bearish
- **WHEN** ETH bias is 'bearish' and 5 of 30 symbols are bearish
- **THEN** weighted_bearish = 5 + 1.5 = 6.5, weighted_total = 30 + 1.5 = 31.5, bearish_pct = 20.6%

### Requirement: Neutral percentage with anchor weighting

The system SHALL calculate neutral_pct using weighted aggregation when BTC/ETH bias is 'neutral'. Neutral anchor weights SHALL be added to weighted_neutral and weighted_total.

#### Scenario: BTC neutral bias increases neutral weight
- **WHEN** BTC bias is 'neutral' and 15 of 30 symbols are neutral
- **THEN** weighted_neutral = 15 + 2.0 = 17.0, weighted_total includes neutral anchor weight

#### Scenario: ETH neutral bias increases neutral weight
- **WHEN** ETH bias is 'neutral' and 18 of 30 symbols are neutral
- **THEN** weighted_neutral = 18 + 1.5 = 19.5, weighted_total includes neutral anchor weight

#### Scenario: Both BTC and ETH neutral
- **WHEN** both BTC and ETH bias are 'neutral' and 20 of 30 symbols are neutral
- **THEN** weighted_neutral = 20 + 2.0 + 1.5 = 23.5, weighted_total = 30 + 3.5 = 33.5, neutral_pct = 70.1%

### Requirement: BULLISH regime threshold

The system SHALL classify regime as BULLISH when weighted bullish_pct >= 0.45 (threshold lowered from 0.5).

#### Scenario: 45% bullish triggers BULLISH regime
- **WHEN** bullish_pct = 0.45 and confidence >= 65
- **THEN** regime = BULLISH

#### Scenario: 44% bullish does not trigger BULLISH
- **WHEN** bullish_pct = 0.44
- **THEN** regime != BULLISH (falls through to other checks)

### Requirement: BEARISH regime threshold

The system SHALL classify regime as BEARISH when weighted bearish_pct >= 0.45 (threshold lowered from 0.5).

#### Scenario: 45% bearish triggers BEARISH regime
- **WHEN** bearish_pct = 0.45 and confidence >= 65
- **THEN** regime = BEARISH

#### Scenario: 44% bearish does not trigger BEARISH
- **WHEN** bearish_pct = 0.44
- **THEN** regime != BEARISH (falls through to other checks)

### Requirement: CHOPPY regime threshold

The system SHALL classify regime as CHOPPY when low volatility and weighted neutral_pct >= 0.70 (threshold raised from 0.6).

#### Scenario: 70% neutral triggers CHOPPY with low volatility
- **WHEN** avg_atr <= 0.04 and neutral_pct = 0.70
- **THEN** regime = CHOPPY with confidence = 60

#### Scenario: 69% neutral does not trigger CHOPPY
- **WHEN** avg_atr <= 0.04 and neutral_pct = 0.69
- **THEN** regime = MIXED (falls through to else branch)

#### Scenario: High volatility prevents CHOPPY
- **WHEN** avg_atr > 0.04 and neutral_pct = 0.75
- **THEN** regime != CHOPPY (high volatility condition not met)

## ADDED Requirements

### Requirement: Anchor weight consistency validation

The system SHALL ensure that anchor weights are only added to weighted_total when they are also added to one of the directional weights (bullish, bearish, or neutral).

#### Scenario: All weights balanced
- **WHEN** BTC bias = 'bullish', ETH bias = 'neutral'
- **THEN** BTC weight (+2.0) added to both weighted_bullish and weighted_total, ETH weight (+1.5) added to both weighted_neutral and weighted_total

#### Scenario: No spurious weight inflation
- **WHEN** BTC bias = None or empty string
- **THEN** no BTC weight added to any component (weighted_bullish, weighted_bearish, weighted_neutral, or weighted_total)
