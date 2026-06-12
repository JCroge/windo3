## ADDED Requirements

### Requirement: Position sync tolerates transient exchange errors

`sync_positions` SHALL retry `fetch_positions()` on transient exchange network errors (`ccxt.NetworkError` and its subclasses `RequestTimeout` / `ExchangeNotAvailable` / `DDoSProtection`) up to a bounded number of attempts with backoff, before treating the cycle as failed. Each transient retry SHALL be logged at WARNING with the exception type name. The system MUST escalate to an ERROR log only after retries are exhausted (or for a non-transient exception), and the ERROR MUST include the exception type name. On terminal failure the local position state MUST be preserved (unchanged from prior behavior).

#### Scenario: Transient error then success
- **WHEN** `fetch_positions()` raises a `ccxt.NetworkError` on the first attempt and succeeds on a retry
- **THEN** the retry is logged at WARNING (with the exception type)
- **AND** `sync_positions` completes normally using the successful result
- **AND** no ERROR is logged

#### Scenario: Transient error exhausts retries
- **WHEN** `fetch_positions()` raises `ccxt.NetworkError` on every attempt
- **THEN** the helper raises after the bounded attempts
- **AND** exactly one ERROR is logged including the exception type name
- **AND** local positions are preserved and `_last_sync_result` is empty

#### Scenario: Non-transient error is not retried
- **WHEN** `fetch_positions()` raises a non-network exception (e.g. `ccxt.AuthenticationError`)
- **THEN** it is not retried
- **AND** it surfaces immediately as a single ERROR including the exception type name
