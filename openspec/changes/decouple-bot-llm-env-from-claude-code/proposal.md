## Why

The trading bot reads its LLM config from `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL` (`agents/llm_client.py:195-197,204`) — **the exact env-var names Claude Code (the CLI) uses for itself**. Because `utils/config_loader.py:394` calls `load_dotenv(override=False)`, inherited env vars win over the bot's `.env`. So Claude Code's config (currently `claude-opus-4-8` via a proxy) **hijacks** the bot: the bot runs on opus-4-8, which rejects the `temperature` param the bot sends → every bot LLM call fails (`'str' object has no attribute 'choices'`), and the bot silently degrades to rule-only.

Editing the bot's `.env` doesn't help (it's overridden), and touching the shared `ANTHROPIC_*` / `~/.zshrc` would break Claude Code itself. Root cause: **env-var namespace collision** between the bot and the CLI.

## What Changes

- Bot LLM client reads **dedicated** env vars `BOT_LLM_API_KEY` / `BOT_LLM_BASE_URL` / `BOT_LLM_MODEL` instead of `ANTHROPIC_*`. **No fallback to `ANTHROPIC_*`** (a fallback would re-introduce the collision). Defaults unchanged (`base=https://api.anthropic.com`, `model=claude-opus-4-6`).
- `.env` and `.env.example`: rename the 3 LLM keys to `BOT_LLM_*` (values unchanged — proxy + temperature-compatible model).
- Result: the bot uses its own LLM config independent of whatever model/proxy Claude Code is on; both coexist with no interference.

## Capabilities

### New Capabilities
- `bot-llm-config-isolation`: the bot's LLM client SHALL source its credentials/endpoint/model from bot-dedicated env vars that do not collide with the host CLI's `ANTHROPIC_*`.

### Modified Capabilities
<!-- none -->

## Impact

- **Modified**: `agents/llm_client.py` (3 `os.getenv` + 1 message string); `.env.example` (3 keys); `.env` (3 keys, gitignored — not committed).
- **Behavioral**: bot LLM config no longer hijacked by Claude Code's `ANTHROPIC_*`; `.env` becomes authoritative for the bot's LLM.
- **Migration**: operators must set `BOT_LLM_*` in `.env` (done as part of this change). Old `ANTHROPIC_*` are no longer read by the bot.
- **Non-goals**: do NOT touch Claude Code's `ANTHROPIC_*` or `~/.zshrc`; do NOT change other agent logic; do NOT add `ANTHROPIC_*` fallback.
