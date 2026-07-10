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
