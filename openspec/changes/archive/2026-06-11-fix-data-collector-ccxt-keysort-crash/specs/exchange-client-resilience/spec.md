## ADDED Requirements

### Requirement: Exchange clients tolerate malformed market data

ccxt exchange clients created through `utils/exchange_factory.create_exchange` SHALL complete `load_markets()` without raising when the exchange returns malformed markets (e.g. markets whose `id` is `None`). The system MUST NOT require a ccxt version upgrade to achieve this.

#### Scenario: OKX returns markets with null id
- **WHEN** `load_markets()` is called and the exchange's `markets_by_id` contains one or more `None` keys
- **THEN** `keysort` sorts the entries deterministically (treating `None` as ordered-first) instead of raising `TypeError`
- **AND** `load_markets()` returns successfully with the valid markets available

#### Scenario: All factory-created clients are protected
- **WHEN** any agent (`data_collector`, `market_scanner`, `judge`, `telegram_notifier`) constructs its client via `create_exchange`
- **THEN** the null-tolerance behavior is already installed (the shim is applied once on import of the factory)
- **AND** no per-call-site change is required for protection

#### Scenario: Normal market data is unaffected
- **WHEN** `keysort` receives a dictionary whose keys are all strings
- **THEN** the resulting order is identical to the pre-shim ccxt behavior
