## Context

`ResearchSynthesizer` 两阶段：初选（`synthesizer.py:~272`，`ask_claude_json(SYNTHESIS_PROMPT, schema=SYNTHESIS_SCHEMA)`）→ 言官 → 终选（`:~326`，`ask_claude_json(FINAL_DECISION_PROMPT, ...)`）。两处都用默认 `max_tokens=2000`。`_rule_fallback(candidates)`（:526）是规则降级选币。终选已有 `_apply_censor_rules` / `_salvage_from_preliminary` / 保底补充三层兜底；**初选只在 except 时降级**，对"LLM 成功但返回空"无兜底。

## Goals / Non-Goals

**Goals:** 初选永不产空（有候选时）；LLM 调用不被截断。**Non-Goals:** 不改选币规则、不加 config、不动 llm_client、不改终选既有兜底逻辑。

## Decisions

### D1 — max_tokens 6000（A）
初选/终选的 `ask_claude_json` 传 `max_tokens=6000`。截断发生在 ~char 2894（≈ 超过默认 2000 tokens），12 标的+中文理由需更大上限；6000 留足余量。`ask_claude_json(**kwargs)` 已透传到 `chat_json(max_tokens=...)`，无需改 client。

### D2 — 初选抽出 `_select_preliminary(result, candidates)` 纯函数（B）
把"用 LLM 结果 or 空则规则降级"收敛为可单测的纯函数：
```
def _select_preliminary(self, result, candidates):
    if result:
        selected = result.get('selected_symbols', [])[:self._max_symbols]
        if selected:
            return selected, result.get('market_regime', 'unknown')
    # LLM 异常 / 截断 / 空 → 规则降级
    return self._rule_fallback(candidates), 'unknown'
```
初选调用处：`try: result = await ask_claude_json(..., max_tokens=6000) except: result=None`，再 `selected, regime = self._select_preliminary(result, candidates)`。这样异常、截断、空三种情况统一走 `_rule_fallback`。

### D3 — 终选只加 max_tokens
终选已有 `_salvage_from_preliminary` + 保底补充，空结果已被覆盖；只补 `max_tokens=6000` 防截断，不改其兜底结构。

## Risks / Trade-offs

- **[更大 max_tokens → 单次 token 成本/延迟略增]** → 仅研判每 4h 几次调用，可忽略；换来不截断。
- **[规则降级质量低于 LLM]** → 但"有候选能交易" >> "空候选停摆"；且 LLM 正常时仍优先用 LLM 结果。

## Migration Plan

纯行为修复，无状态/schema。重启 `run_agents.py` 后研判即产出候选（LLM 或规则）。回滚=revert。
