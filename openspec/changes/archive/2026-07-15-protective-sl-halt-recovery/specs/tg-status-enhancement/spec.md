## ADDED Requirements

### Requirement: `/status` SHALL distinguish global halt, per-symbol halt, and Tactical circuit

Telegram `/status` SHALL display global halt state, per-symbol halt state, and Tactical circuit state as separate status lines. A global protection halt MUST NOT be presented in a way that implies the Tactical circuit is paused. Tactical circuit state SHALL be read from the persisted risk guard tactical circuit state when available.

#### Scenario: global protection halt while Tactical circuit is not paused
- **WHEN** `halt_state.halted == true` with reason `okx_sl_algo_unresolved:WLD-USDT-SWAP`
- **AND** `riskguard_state.tactical_circuit.pause_until == 0`
- **THEN** `/status` MUST show global halt as active with the OKX protection reason
- **AND** `/status` MUST show Tactical circuit as not paused
- **AND** the message MUST NOT imply Tactical loss circuit caused the halt

#### Scenario: Tactical circuit paused while global halt is clear
- **WHEN** `halt_state.halted == false`
- **AND** `riskguard_state.tactical_circuit.pause_until` is in the future
- **THEN** `/status` MUST show global halt as inactive
- **AND** `/status` MUST show Tactical circuit as paused with its pause reason

#### Scenario: status data missing degrades safely
- **WHEN** `riskguard_state.tactical_circuit` is missing or unreadable
- **THEN** `/status` MUST still show global halt and per-symbol halt state
- **AND** Tactical circuit line MUST degrade to an unknown/unavailable marker rather than failing the command
