# Verification Report: fix-synthesizer-llm-truncation-empty-candidates

- **Date**: 2026-06-13
- **Workflow**: hotfix · **Mode**: full（scale 因 openspec scaffold 计 8 文件；真实代码改动 2 文件）

## Summary

| Dimension | Status |
|---|---|
| Completeness | 7/7 tasks ✓ · 1/1 capability 实现 |
| Correctness | 3/3 spec scenarios 由代码 + 测试覆盖 |
| Coherence | Design D1/D2/D3 遵循；无密钥泄露 |

## 证据

- 全量 `python3 -m pytest -q` → **1152 passed / 4 deselected / 1 warning**（基线 1149 + 新增 `test_synthesizer_empty_fallback.py` 3 case）。
- `compileall agents/research/synthesizer.py` OK；build guard 通过。
- 真实代码改动：`agents/research/synthesizer.py`（初选/终选 `max_tokens=6000` + 新增 `_select_preliminary`）+ 测试。

## Scenario 覆盖（research-synthesis-resilience）

- **LLM 返回有效非空选择 → 用 LLM** → `_select_preliminary` 取 `selected_symbols`；`test_uses_llm_when_nonempty`。
- **LLM 空/截断 → 规则降级（非空）** → 空 `selected_symbols` → `_rule_fallback`；`test_empty_selected_falls_back_to_rules`。
- **LLM 调用抛异常 → 规则降级（非空）** → `result=None` → `_rule_fallback`；`test_none_result_falls_back_to_rules`。

## Design 遵循

- D1：初选(`SYNTHESIS_SCHEMA`)与终选(`FINAL_SYNTHESIS_SCHEMA`)的 `ask_claude_json` 均加 `max_tokens=6000`（各 1 处，grep 证实）。
- D2：新增纯函数 `_select_preliminary(result, candidates)`，异常/截断/空三态统一 `_rule_fallback`；初选调用处改用它。
- D3：终选既有 `_salvage_from_preliminary`/保底补充逻辑未改，只加 max_tokens。

## 安全

- diff 内 `api_key/sk-` 命中均为变量名（`BOT_LLM_API_KEY`）与测试假 key（`sk-test`/`sk-bot-123`），**无真实密钥**（真实 key 仅在 gitignored `.env`）。

## Issues

- **CRITICAL**: 无 · **WARNING**: 无。
- **备注**：本修复保证研判初选永不产空（有候选时）；运行期重启确认（采集/决策恢复）为部署动作，交用户执行。根因——`max_tokens=2000` 对 12 标的输出过小——为长期潜伏项，LLM 一直失败走规则降级时未暴露，LLM 修通后显现。

## Final Assessment

**All checks passed — ready for archive.** 无 CRITICAL/WARNING。
