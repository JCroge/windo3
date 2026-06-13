## Context

`agents/llm_client.py:195-197` reads `ANTHROPIC_API_KEY/BASE_URL/MODEL`. Claude Code (the CLI host) uses the same names (it currently runs as `claude-opus-4-8` via proxy `156.238.228.230:8080`). `utils/config_loader.py:394` uses `load_dotenv(override=False)`, so the inherited (CLI) env wins over the bot's `.env`. opus-4-8 rejects `temperature` → bot LLM calls fail. Confirmed only `agents/llm_client.py` reads these vars in the bot.

## Goals / Non-Goals

**Goals:** the bot's LLM config is independent of the host CLI's `ANTHROPIC_*`; `.env` is authoritative; no interference either direction.

**Non-Goals:** touching Claude Code's `ANTHROPIC_*` / `~/.zshrc`; changing other agents; keeping any `ANTHROPIC_*` fallback.

## Decisions

### D1 — Rename to bot-dedicated vars, no fallback
Read `BOT_LLM_API_KEY` / `BOT_LLM_BASE_URL` / `BOT_LLM_MODEL`. **Deliberately no `ANTHROPIC_*` fallback** — a fallback would re-collide with the CLI and reintroduce the hijack. Clean break is the whole point.
*Alternative considered:* `load_dotenv(override=True)` — one-line, but the bot would still occupy the `ANTHROPIC_*` name, so a future exported `ANTHROPIC_*` (or running in a CLI shell) could still bleed in. Rename is the durable fix.

### D2 — Defaults unchanged
`BOT_LLM_BASE_URL` default `https://api.anthropic.com`; `BOT_LLM_MODEL` default `claude-opus-4-6` (temperature-compatible). `BOT_LLM_API_KEY` default unset → client unavailable → rule fallback (existing behavior).

### D3 — Update both .env and .env.example
`.env` (live, gitignored): set `BOT_LLM_*` to current working values (new key + `http://156.238.228.230:8080` + `claude-opus-4-6`); remove the old `ANTHROPIC_*` LLM lines (they were Claude Code's anyway). `.env.example`: rename template keys so new deployments use `BOT_LLM_*`.

## Risks / Trade-offs

- **[Operator must set new var]** if `BOT_LLM_API_KEY` unset after deploy → bot LLM unavailable (rule fallback, not a crash). → set in `.env` as part of this change; document in `.env.example`.
- **[temperature still an issue if someone sets BOT_LLM_MODEL=opus-4-7/4-8]** → out of scope here; default stays opus-4-6. (A separate temperature-compat fix remains a known option.)

## Migration Plan

Edit `.env` (already has the working values, just renamed). Restart `run_agents.py`; bot reads `BOT_LLM_MODEL=claude-opus-4-6` regardless of the CLI's `ANTHROPIC_MODEL`. Rollback = revert llm_client.py + restore env var names.
