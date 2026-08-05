## MODIFIED Requirements

### Requirement: Tactical order submission SHALL recover idempotently across restart
The system SHALL persist `submitting` before exchange I/O and derive a deterministic entry client-order id from the intent id. On restart, any non-terminal `submitting`, `filled`, or `closing` state SHALL be reconciled against exchange orders, positions, and owner-tagged protection before another action is submitted. An exchange order in a terminal state MUST have zero cancelable remainder even when its original size exceeds its filled size. The system MUST NOT blindly retry an unknown submission or repeat cancellation without exact terminal proof.

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

#### Scenario: Already-terminal entry is not canceled again
- **WHEN** exact deterministic lookup returns an entry in `canceled`, `cancelled`, `closed`, `filled`, `rejected`, or `expired` state
- **THEN** the system SHALL treat its cancelable remainder as zero while preserving its confirmed filled quantity
- **AND** it SHALL NOT submit another cancel request for that terminal order

#### Scenario: Cancel not-found race requires exact terminal proof
- **WHEN** an entry is open during the pre-cancel lookup but the cancel request reports that the order is already filled, canceled, or absent
- **THEN** the system SHALL re-query the deterministic client-order id
- **AND** it SHALL accept cancellation as proven only if the exact order is terminal with zero remainder; otherwise Tactical admission SHALL remain halted for reconciliation
