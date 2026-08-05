## ADDED Requirements

### Requirement: Tactical V2 SHALL use deterministic owner-tagged TP and SL protection
Every filled Tactical V2 position SHALL have full-quantity exchange-owned TP and SL protection whose client identities are deterministic derivatives of the intent id and satisfy the existing bot owner-tag format. The position state SHALL persist the entry client id, each protection client id, returned exchange algo ids, protected quantity, trigger prices, and reconciliation state. The ownership model MUST support exchanges that expose the TP and SL under one OCO algo id or separate algo ids without changing the Tactical lifecycle contract.

#### Scenario: Tactical fill installs identifiable full protection
- **WHEN** a Tactical V2 entry is confirmed filled for a quantity
- **THEN** the system SHALL submit full-quantity TP and SL protection with deterministic Tactical V2 owner-tagged client identities
- **AND** the persisted position SHALL contain enough identity to prove ownership after restart

#### Scenario: Main and sidecar retain distinct bot owners
- **WHEN** Main and the legacy sidecar run concurrently during shadow observation or drain
- **THEN** Main/V2 SHALL use the configured `BOT_INSTANCE_ID`
- **AND** the sidecar SHALL force its executor to use the distinct `SIDECAR_BOT_INSTANCE_ID` even when Main's owner is present in the shared environment

#### Scenario: Partial fill protects only confirmed quantity
- **WHEN** a Tactical V2 entry partially fills and its remainder is canceled
- **THEN** TP and SL protection SHALL cover the confirmed filled quantity only
- **AND** the system SHALL NOT place protection for or chase the unfilled quantity

#### Scenario: Combined and separate algo representations reconcile equivalently
- **WHEN** OKX returns one parent algo id for attached TP and SL or returns independently addressable TP and SL algo ids
- **THEN** the reconciler SHALL persist the observed representation
- **AND** it SHALL prove both required protection legs without assuming a fixed exchange response shape

### Requirement: Unverified Tactical protection SHALL fail closed
A Tactical fill SHALL NOT be considered safely open until both its full-quantity TP and SL legs are verified against exchange state. If verification or ownership proof fails, the system SHALL stop new Tactical admission, cancel any provably owned residual entry or protection orders, and attempt to close confirmed unprotected exposure through the owner-bound safety path. The integrity halt MUST remain until reconciliation proves account, position, and protection state.

#### Scenario: One missing protection leg triggers integrity handling
- **WHEN** a filled Tactical V2 position has a verifiable SL but no verifiable full-quantity TP, or a verifiable TP but no verifiable full-quantity SL
- **THEN** the system SHALL mark protection incomplete and activate the non-expiring Tactical integrity halt
- **AND** it SHALL attempt an owner-bound safe close of confirmed exposure rather than continue normal admission

#### Scenario: Unknown ownership does not cause broad cancellation
- **WHEN** an exchange protection order or position could belong to Main, legacy sidecar, another bot, or a manual operator
- **THEN** Tactical V2 SHALL preserve the ambiguous object and halt new Tactical admission for reconciliation
- **AND** it SHALL NOT cancel or close the object without ownership proof

#### Scenario: Reconciliation clears halt only after proof
- **WHEN** an integrity halt is active because protection or exposure was ambiguous
- **THEN** elapsed time alone SHALL NOT clear the halt
- **AND** admission MAY resume only after reconciliation proves every affected owner flat or fully protected

### Requirement: Tactical exit paths SHALL serialize and reconcile idempotently
Exchange TP/SL fills, local max-hold closes, global safety closes, and restart recovery SHALL coordinate through the existing normalized-symbol exit lock and owner identity. Exchange fills SHALL be authoritative. Before a local close, the system MUST reconcile remaining exchange quantity and cancel or amend only proven Tactical protection. Repeated observations of one close MUST converge on one final resolution rather than submit duplicate reduce-only closes.

#### Scenario: Exchange TP races max hold
- **WHEN** exchange TP fills while the local max-hold path is waiting for the same symbol exit lock
- **THEN** the local path SHALL reconcile the remaining quantity after acquiring the lock
- **AND** it SHALL NOT submit a second close when the exchange position is already flat

#### Scenario: Restart during close does not duplicate resolution
- **WHEN** the process restarts after an exchange close but before local close state is final
- **THEN** recovery SHALL reconcile exchange position, protection orders, and the deterministic intent identity
- **AND** it SHALL publish or retain one final PnL resolution for that close

#### Scenario: Final PnL delivery survives a publisher crash
- **WHEN** a final Tactical PnL correction is persisted before, during, or after downstream publication
- **THEN** the correction SHALL remain in a durable outbox until a publication acknowledgement is persisted
- **AND** restart recovery SHALL re-deliver it without applying the same `resolution_id` to the governor more than once

#### Scenario: Shared safety close retains Tactical attribution
- **WHEN** a global safety path closes a proven Tactical V2 position
- **THEN** owner-bound protection cleanup SHALL run under the serialized exit path
- **AND** the close SHALL be attributed as Tactical `risk_forced` rather than as a Main strategy exit
