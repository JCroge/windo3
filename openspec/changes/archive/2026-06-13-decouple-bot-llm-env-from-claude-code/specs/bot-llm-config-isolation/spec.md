## ADDED Requirements

### Requirement: Bot LLM config is isolated from the host CLI's env vars

The bot's LLM client SHALL read its API key, base URL, and model from bot-dedicated environment variables (`BOT_LLM_API_KEY`, `BOT_LLM_BASE_URL`, `BOT_LLM_MODEL`) and SHALL NOT read the host CLI's `ANTHROPIC_*` variables. This prevents the host (Claude Code) LLM configuration from overriding the bot's own configuration.

#### Scenario: Bot uses its own model, not the inherited one
- **WHEN** `BOT_LLM_MODEL` is set to one value AND `ANTHROPIC_MODEL` is set to a different value in the environment
- **THEN** the bot's LLM client uses the `BOT_LLM_MODEL` value
- **AND** ignores `ANTHROPIC_MODEL`

#### Scenario: Bot reads dedicated key and base URL
- **WHEN** `BOT_LLM_API_KEY` and `BOT_LLM_BASE_URL` are set
- **THEN** the client initializes with those, independent of any `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL`

#### Scenario: Defaults when unset
- **WHEN** `BOT_LLM_BASE_URL` / `BOT_LLM_MODEL` are unset
- **THEN** the client defaults to `https://api.anthropic.com` and `claude-opus-4-6` respectively
- **AND** when `BOT_LLM_API_KEY` is unset the client is unavailable and the bot falls back to rule-based logic (unchanged behavior)
