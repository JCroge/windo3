## ADDED Requirements

### Requirement: Cross-source market dimensions SHALL carry a provenance triple

The data collector SHALL attach a provenance triple — `source` (string origin identifier), `freshness_sec` (age in seconds of the underlying datum), and `confidence` (0.0–1.0) — to each cross-source market dimension it publishes: `oi_data`, `taker_ratio`, `long_short_account`, `big_trades`, and `funding_rate`. The provenance SHALL be emitted as a parallel `provenance` block in the `market_data` payload, keyed by dimension name. The existing flat field values SHALL remain unchanged.

#### Scenario: Provenance present for a fetched dimension
- **WHEN** the collector successfully fetches `taker_ratio` from Binance
- **THEN** `market_data.provenance["taker_ratio"]` SHALL include `source`, `freshness_sec`, and `confidence`
- **AND** `market_data.taker_ratio` SHALL still contain the same flat fields (`buy_sell_ratio`, etc.) as before this change

#### Scenario: Missing dimension yields zero-confidence provenance
- **WHEN** a dimension fetch fails or returns empty
- **THEN** its `provenance` entry SHALL report `confidence = 0.0` (or the dimension SHALL be absent from both the value map and the provenance block, consistently)
- **AND** no flat field value SHALL be fabricated

### Requirement: Source SHALL identify the originating exchange/feed

Each provenance `source` SHALL identify the actual origin feed (e.g. `binance_fapi`, `okx`), so a cross-exchange origin (Binance data in an OKX-primary system) is visible downstream rather than implicit.

#### Scenario: Cross-exchange origin is explicit
- **WHEN** `oi_data`, `taker_ratio`, or `long_short_account` is fetched from Binance fapi
- **THEN** its provenance `source` SHALL be `binance_fapi` (not the OKX trading venue)

#### Scenario: Native origin is labeled
- **WHEN** `big_trades` is fetched from OKX
- **THEN** its provenance `source` SHALL be `okx`

### Requirement: freshness_sec SHALL be derived from the datum timestamp, not fetch time alone

`freshness_sec` SHALL reflect the age of the underlying datum, derived from the API item's own timestamp where available (the timestamp currently discarded by the fetchers), not merely the time the HTTP request completed. For periodic feeds (e.g. Binance `period=1h`), `freshness_sec` SHALL account for the sampling period so an up-to-one-hour-old sample is reported as such.

#### Scenario: Hourly-sampled feed reports realistic age
- **WHEN** `taker_ratio` is fetched from a `period=1h&limit=1` Binance endpoint whose item timestamp is 50 minutes old
- **THEN** `provenance["taker_ratio"].freshness_sec` SHALL be approximately 3000 (≈50 min), not ≈0

#### Scenario: Missing item timestamp falls back to fetch time
- **WHEN** an API item carries no usable timestamp
- **THEN** `freshness_sec` SHALL fall back to time-since-fetch and the source SHALL still be recorded (fail-safe, never crash)

### Requirement: confidence SHALL be derived from freshness, cross-exchange origin, and degraded state

`confidence` SHALL be a deterministic function of `freshness_sec`, whether the source is cross-exchange relative to the trading venue, and the collector's degraded state — decaying toward 0 as data becomes stale, cross-venue, or degraded. The derivation SHALL be centralized in a single function (mirroring `utils/symbol_mentions.py`) so all dimensions score consistently.

#### Scenario: Fresh native data scores high
- **WHEN** a dimension is fresh and from the native trading venue
- **THEN** its `confidence` SHALL be high (near 1.0)

#### Scenario: Stale cross-exchange data scores low
- **WHEN** a dimension is an hour-old Binance sample feeding an OKX system
- **THEN** its `confidence` SHALL be materially reduced relative to a fresh native datum

#### Scenario: Single derivation function
- **WHEN** confidence is computed for any dimension
- **THEN** all dimensions SHALL route through the same confidence-derivation function (no per-call-site bespoke scoring)

### Requirement: Provenance SHALL propagate through tech analysis to downstream consumers

Because Judge and Reviewer consume the derived `tech_analysis` payload rather than raw `market_data`, `tech_analyst` SHALL forward the relevant provenance into `tech_analysis` so it survives the collapse of raw dimensions into derived signals. Provenance SHALL NOT be lost at the analysis layer.

#### Scenario: Provenance survives the analysis collapse
- **WHEN** `tech_analyst` derives signals from `oi_data` / `taker_ratio` / `long_short_account`
- **THEN** the published `tech_analysis` SHALL include a `provenance` block carrying source/freshness_sec/confidence for those dimensions
- **AND** a Judge/Reviewer reading only `tech_analysis` SHALL be able to observe the provenance

#### Scenario: Legacy tech_analysis without provenance is tolerated
- **WHEN** a consumer reads a `tech_analysis` payload that predates this change (no `provenance` block)
- **THEN** the consumer SHALL treat provenance as unknown (e.g. zero confidence / unknown source) and continue without error

### Requirement: A per-decision provenance summary SHALL reach trade records via attribution

Because the Reviewer consumes trade outcome records (`execution_result` → trade history), not `tech_analysis`, the Judge SHALL attach a per-decision provenance summary to `trade_decision.attribution` so the data-source quality at decision time travels with the trade. This attribution write is metadata-only and SHALL NOT gate, rank, or veto any decision (consistent with the observability-only scope). The summary SHALL capture at least the weakest contributing-signal confidence and whether any contributing signal was cross-exchange.

#### Scenario: Provenance summary attached to a decision
- **WHEN** the Judge produces a `trade_decision` from a `tech_analysis` payload that carries a `provenance` block
- **THEN** `trade_decision.attribution` SHALL include a provenance summary with at least a weakest-signal confidence and a cross-exchange flag
- **AND** the decision action/ranking SHALL be identical to what it would be without the provenance summary (metadata-only)

#### Scenario: Missing provenance yields an unknown summary
- **WHEN** the Judge produces a decision from a `tech_analysis` payload with no `provenance` block (legacy)
- **THEN** the attribution provenance summary SHALL mark quality as unknown rather than fabricating a confidence
- **AND** the decision SHALL proceed normally

### Requirement: Reviewer SHALL be able to bucket outcomes by data-source quality

The Reviewer SHALL be able to segment/bucket trade outcomes by the per-decision provenance summary carried in the trade record (source/cross-exchange flag and/or a confidence band), so data-source quality can be correlated with performance.

#### Scenario: Bucketing by confidence band
- **WHEN** the Reviewer aggregates outcomes and the provenance summary is available on trade records
- **THEN** it SHALL be able to report metrics split by at least one provenance dimension (e.g. low- vs high-confidence, or native vs cross-exchange)

#### Scenario: Tolerates trade records without a provenance summary
- **WHEN** the Reviewer aggregates legacy trade records lacking a provenance summary
- **THEN** it SHALL bucket them as `unknown` quality and continue without error

### Requirement: Provenance SHALL be observability-only in this change

This change SHALL NOT alter Judge's decision behavior. Provenance SHALL be additive metadata for observation and Reviewer bucketing only. Any behavioral down-weighting of weak signals is explicitly deferred to a separate change.

#### Scenario: Judge decisions unchanged
- **WHEN** the same inputs are processed before and after this change
- **THEN** Judge's `trade_decision` outputs SHALL be unchanged by the presence of the provenance block (no gating, ranking, or veto driven by provenance in this change)
