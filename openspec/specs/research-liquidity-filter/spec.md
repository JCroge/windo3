## ADDED Requirements

### Requirement: Pre-LLM Liquidity Hard Filter

The MarketScanner SHALL remove low-liquidity candidates before publishing `research_market_data`, so that low-depth symbols never reach `ResearchSynthesizer`, the initial LLM synthesis prompt, `research_preliminary`, `research_result`, or `SymbolRouter` rotation. A candidate SHALL be kept only if it passes both the 24h quote-volume gate and the open-interest gate.

#### Scenario: High volume but low open interest is removed
- **WHEN** an enriched candidate has `volume_24h >= research_min_volume_24h_usdt` but `open_interest_usd < research_min_open_interest_usd`
- **THEN** the MarketScanner SHALL remove the candidate before publishing `research_market_data`
- **AND** the rejection reason SHALL be `open_interest_below_min`

#### Scenario: Sufficient volume and open interest is kept
- **WHEN** an enriched candidate has `volume_24h >= research_min_volume_24h_usdt` and `open_interest_usd >= research_min_open_interest_usd`
- **THEN** the MarketScanner SHALL keep the candidate in the published `candidates` list

### Requirement: Fail-Closed on Missing Depth

The MarketScanner SHALL treat missing or unfetchable open interest as a removal condition rather than passing the symbol to the LLM. If depth cannot be proven, the symbol SHALL NOT consume a live candidate slot.

#### Scenario: Missing open interest is removed
- **WHEN** an enriched candidate has `open_interest_usd` missing or `None`
- **THEN** the MarketScanner SHALL remove the candidate
- **AND** the rejection reason SHALL be `open_interest_missing`

#### Scenario: Below-minimum volume is removed before open-interest check
- **WHEN** an enriched candidate has `volume_24h < research_min_volume_24h_usdt`
- **THEN** the MarketScanner SHALL remove the candidate with reason `volume_below_min`
- **AND** the volume gate SHALL be evaluated before the open-interest gate

### Requirement: Liquidity Filter Observability

The published `research_market_data` payload SHALL include a `liquidity_filter` summary describing the gate outcome so downstream agents and operators can audit removals.

#### Scenario: Published payload includes filter summary
- **WHEN** the MarketScanner publishes `research_market_data` after a successful scan
- **THEN** the payload SHALL include a `liquidity_filter` object with `min_volume_24h_usdt`, `min_open_interest_usd`, `removed`, `kept`, and an `examples` list
- **AND** the `examples` list SHALL be capped (at most 5) and each entry SHALL carry `symbol`, `volume_24h`, `open_interest_usd`, and `reason`

### Requirement: Degraded Fallback Preserves Filtering

The MarketScanner SHALL preserve the filtered candidate set and its filter summary through the degraded `last_good` fallback, so a failed scan never reintroduces previously removed low-liquidity symbols.

#### Scenario: Degraded payload carries filtered last_good candidates
- **WHEN** a scan fails and the MarketScanner publishes a degraded `last_good` `research_market_data`
- **THEN** the candidates SHALL be the already-filtered `last_good` set
- **AND** the payload SHALL carry the stored `liquidity_filter` summary from the last successful scan

### Requirement: Operator-Tunable Thresholds

The liquidity thresholds SHALL be configurable through the config loader and environment, independent of the coarse pre-enrichment scan volume filter.

#### Scenario: Defaults and env overrides resolve
- **WHEN** no override is set
- **THEN** `research_min_volume_24h_usdt` SHALL default to 50,000,000 and `research_min_open_interest_usd` SHALL default to 10,000,000
- **AND** `RESEARCH_MIN_VOLUME_24H_USDT` / `RESEARCH_MIN_OPEN_INTEREST_USD` SHALL override the defaults within their hard limits
