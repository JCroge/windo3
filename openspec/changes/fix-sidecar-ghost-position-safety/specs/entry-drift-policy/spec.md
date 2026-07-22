## ADDED Requirements

### Requirement: Sidecar live opens SHALL enforce stale-entry drift protection
The sidecar live open path SHALL evaluate live price drift against the Tactical shadow plan entry reference before submitting a market order. If explicit drift anchors are missing, the sidecar SHALL derive stop and TP percentages from `entry_ref`, `stop_loss`, and the first `take_profit` level when possible. A stale sidecar plan beyond the configured hard drift bound SHALL be rejected before order submission.

#### Scenario: Large sidecar entry drift rejects before order
- **WHEN** a sidecar Tactical plan has `entry_ref`
- **AND** the current market price drifts beyond the configured hard drift bound from that entry reference
- **THEN** `open_sidecar_plan` SHALL reject the open before calling `create_order`
- **AND** the sidecar SHALL record a drift rejection audit event

#### Scenario: Sidecar drift decision is recorded on accepted open
- **WHEN** a sidecar Tactical plan passes stale-entry drift protection
- **THEN** the sidecar SHALL persist enough drift metadata on the position or audit stream to explain the admission decision
- **AND** the open SHALL still satisfy existing SL-side, slippage, precheck, min-size, and protective-SL verification checks

#### Scenario: Missing drift anchors fail safely
- **WHEN** a sidecar Tactical plan cannot provide or derive enough information for stale-entry drift protection
- **THEN** the sidecar SHALL reject the open or emit an explicit fail-safe audit reason before order submission
- **AND** it SHALL NOT silently bypass drift protection
