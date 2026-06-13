# Tasks

## 1. 修复初选产空 + 调大 max_tokens (research-synthesis-resilience)
- [x] 1.1 `agents/research/synthesizer.py` 初选：`ask_claude_json(SYNTHESIS_PROMPT, ..., schema=SYNTHESIS_SCHEMA, max_tokens=6000)`；try 仅包 LLM 调用，异常时 `result=None`
- [x] 1.2 新增 `_select_preliminary(self, result, candidates)` 纯函数：result 有效且 `selected_symbols` 非空 → 用 LLM 结果（截 `_max_symbols`）+ market_regime；否则（None/截断/空）→ `self._rule_fallback(candidates)` + regime='unknown'，记 WARNING；初选调用处改用它
- [x] 1.3 终选：`ask_claude_json(FINAL_DECISION_PROMPT, ..., max_tokens=6000)`（终选既有 `_salvage_from_preliminary`/保底兜底不动）
- [x] 1.4 单测 `test_synthesizer_empty_fallback.py`：`_select_preliminary` —— 有效非空→用 LLM；空 selected→`_rule_fallback`（非空）；result=None→`_rule_fallback`（非空）

## 2. 验证与收尾
- [x] 2.1 全量 `python3 -m pytest -q` 通过（基线 1149 + 新增）
- [x] 2.2 编译 `python3 -m compileall -q agents/research/synthesizer.py`
- [x] 2.3 部署后运行期确认（代码层已验证：单测覆盖空→fallback；运行期重启确认交用户）—（重启后研判初选出非空候选 → 采集/决策恢复）——交用户重启验证
