## Why

After the LLM started working (opus-4-6), the research synthesizer's **preliminary** selection produces **empty candidates** every cycle (`[研判·初选] 候选标的: []`, reproduced at 20:20/20:44/20:49). With no candidates, the Censor/final have nothing, `SymbolRouter` publishes no active symbols, `data_collector` collects nothing → **0 采集 / 0 决策 → the system stops trading** (idle until the next 4h research cycle).

Root cause: `synthesizer.py:274` calls `ask_claude_json(SYNTHESIS_PROMPT, ...)` with the default `max_tokens=2000` (`llm_client.py:231`). opus-4-6's verbose 12-candidate + reasoning response exceeds that → the JSON is **truncated** mid-string → `chat_json` schema-validates a partial dict (missing `selected_symbols`) and returns it **without raising** → `result.get('selected_symbols', [])` is `[]` → the existing `except → _rule_fallback` never fires → empty.

(Ironically, while the LLM was *broken* (opus-4-8 temperature failure → exception → `_rule_fallback`), the synthesizer produced candidates and traded. Fixing the LLM exposed the truncation + the missing empty-result fallback.)

## What Changes

- **A — size the calls**: pass a larger `max_tokens` (6000) to the preliminary (`:274`) and final (`:326`) `ask_claude_json` calls so the JSON isn't truncated. (`ask_claude_json` already forwards `**kwargs` → `chat_json(max_tokens=...)`, no client change.)
- **B — never produce empty preliminary**: when the LLM returns an empty/invalid `selected` list (not just on exception), fall back to the existing `_rule_fallback(candidates)`. Extract a small `_select_preliminary(result, candidates)` seam so this is unit-testable. The **final** stage already has robust fallbacks (`_salvage_from_preliminary`, 保底 fill) — only `max_tokens` is added there.

## Capabilities

### New Capabilities
- `research-synthesis-resilience`: the preliminary research synthesis SHALL never publish an empty candidate set when usable candidates exist — on any LLM failure (exception, truncation, or empty result) it falls back to rule-based selection; LLM calls are sized to avoid truncation.

### Modified Capabilities
<!-- none -->

## Impact

- **Modified**: `agents/research/synthesizer.py` (max_tokens on 2 calls + `_select_preliminary` seam + empty→fallback).
- **Test**: `test_synthesizer_empty_fallback.py`.
- **Behavioral**: research always yields candidates (LLM result, or rule fallback) → trading pipeline no longer stalls on truncated LLM JSON.
- **Non-goals**: do NOT change the selection rules themselves, add config, or touch `llm_client.py`.
