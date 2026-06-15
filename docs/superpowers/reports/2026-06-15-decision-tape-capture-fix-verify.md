# Verification Report: decision-tape-capture-fix

**Date:** 2026-06-15
**Change:** `openspec/changes/decision-tape-capture-fix/`
**Design Doc:** `docs/superpowers/specs/2026-06-15-decision-tape-capture-fix-design.md`
**Plan:** `docs/superpowers/plans/2026-06-15-decision-tape-capture-fix.md`
**Verify mode:** full（16 tasks / 17 files / 1 capability delta）

## Summary

| Dimension | Status |
|---|---|
| Completeness | 16/16 tasks `[x]`；delta capability `decision-replay-tape` 实现到位 |
| Correctness | 10/10 spec scenario 覆盖（8 强行为测试 + 2 机制/源码断言）；全量 1234 passed |
| Coherence | 实现符合 design.md + Design Doc（含 D1.1 tech 侧信道修正回写）；delta ↔ design 无矛盾 |

**最终结论：无 CRITICAL、无 WARNING，2 项 SUGGESTION。Ready for archive。**

## Completeness

- tasks.md：16/16 全勾。
- 生产改动：`agents/trading/judge.py`（`_symbol_llm_cache` + `_symbol_tech_tape_cache` 侧信道捕获 + reset/set/pop + ranked re-prime + 两个 chokepoint 读 cache）、`utils/decision_tape.py`（`replayable` 守卫 + schema v2）。
- 测试新增：`test_decision_tape.py`(+3)、`test_decision_state_snapshot.py`(+1/改1)、`test_judge_decision_tape_wiring.py`(+5)、`test_decision_tape_capture.py`(+2 端到端)。

## Correctness — delta spec scenario 覆盖

| Scenario | 覆盖 | 证据 |
|---|---|---|
| 开仓 accept 落磁带 | 机制+源码断言 | accept chokepoint 读 cache；`test_accept_tape_reads_llm_cache_not_hardcoded_none` |
| 拒单也落磁带 | 强 | `test_reject_path_captures_tech_and_llm_from_cache` / `test_reject_path_writes_to_tape` |
| 捕获使回放复现拒因 | 强（端到端） | `test_capture_record_replays_to_gate_reject`（floor 1.50 → `rr_below_floor:1.39`） |
| 捕获使旋钮扰动可翻转 | 强（端到端） | `test_capture_record_flips_to_accept_when_floor_lowered`（floor 1.30 → open_long） |
| 原子写不污染主链路 | 强 | `test_missing_tape_does_not_break_reject` / `test_reject_capture_defensive_when_caches_absent` |
| 内联输出抗 llm_audit 过期 | 机制 | `llm_output_inline` 内联存储，端到端测试用内联 llm 驱动回放 |
| 规则降级无 LLM | 机制 | per-decision reset 保证 rule-only 路径 llm cache=None |
| 输入完整才可回放 | 强 | `test_replayable_requires_nonempty_tech` / `test_build_bundle_with_snapshot_marks_replayable` |
| 缺输入标不可回放 | 强 | `test_missing_snapshot_not_replayable` / `test_build_bundle_snapshot_but_empty_tech_not_replayable` |
| schema 版本标记自包含 | 强 | `test_schema_version_is_v2` |

## Coherence — 设计一致性

- 实现严格符合 Design Doc：D1（`_symbol_llm_cache` 镜像）、**D1.1（tech 专属侧信道 `_symbol_tech_tape_cache`，observability-only 不变量修正）**、D2（ranked re-prime 写 tape 侧信道）、D3（replayable 守卫 + schema v2）。
- **observability-only 不变量（核心红线）**：最终 opus 全局审查发现 ranked-flush 曾误写 live `_symbol_tech_cache`（被 regime manager + probe-short gate 读取），已修复为专属 `_symbol_tech_tape_cache`；`grep "_symbol_tech_cache["` 确认 live cache 仅由消息处理器写（行 637），tape 代码绝不写。守卫测试 `test_flush_does_not_mutate_live_tech_cache` 锁定。opus 复审：Ready to merge。
- `test_cf_red_line_guard.py` 4 passed，决策/风控路径仍不读 CF 产物。
- judge.py 全量 diff 纯 cache/捕获，零 gate/plan/ranking/阈值/RR/EV 逻辑改动。

## Issues

### CRITICAL
无。

### WARNING
无。

### SUGGESTION
1. **accept 落磁带为源码断言而非行为测试**：accept chokepoint 的 tech+llm 捕获由 `test_accept_tape_reads_llm_cache_not_hardcoded_none`（源码契约）覆盖，无驱动 `_gate_and_publish_open` 真实落带断言字段的行为测试。建议后续补一条 accept 行为测试。影响：低（reject 路径行为测试 + 端到端回放已强覆盖捕获机制）。
2. **规则降级 llm=None 无专门行为测试**：rule-only open 路径 llm cache=None 由 per-decision reset 机制保证，无专门断言"rule-only 落带 llm_output_inline=null"的测试。建议后续补一条。影响：低（reset 逻辑简单且已被多测试间接经过）。

## 已知边界（非缺陷）

- 修复只影响新磁带；已有 909 条 tech=[]/llm=null 记录永久 `replayable=false` 不可回放。
- 生效需 OS 层重启 live（`/restart` 同进程不重 import）+ 等新磁带累积 ~1-2 天后重跑 `cf_direction_recommendation.py` 验证 L2 真实化 + L4 产出推荐或诚实拒答。

## 最终评估

**All critical checks passed. Ready for archive.** 2 项 SUGGESTION 为测试加固建议，非阻断，可后续补。
