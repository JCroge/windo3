## ADDED Requirements

### Requirement: Preliminary synthesis never yields empty candidates when input exists

When usable candidate market data is available, the preliminary research synthesis SHALL produce a non-empty selected-symbol set. If the LLM call fails, returns truncated/invalid JSON, or returns an empty selection, the synthesizer SHALL fall back to rule-based selection (`_rule_fallback`). The synthesizer's LLM calls SHALL be sized (max_tokens) to avoid response truncation for the expected candidate count.

#### Scenario: LLM returns a valid non-empty selection
- **WHEN** the preliminary LLM call returns a result with a non-empty `selected_symbols`
- **THEN** the synthesizer uses that LLM selection

#### Scenario: LLM returns empty/truncated selection
- **WHEN** the preliminary LLM call returns an empty `selected_symbols` (e.g. truncated JSON) and candidates exist
- **THEN** the synthesizer falls back to `_rule_fallback(candidates)`
- **AND** the resulting selection is non-empty

#### Scenario: LLM call raises
- **WHEN** the preliminary LLM call raises an exception and candidates exist
- **THEN** the synthesizer falls back to `_rule_fallback(candidates)` (non-empty)
