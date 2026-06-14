# Verification Report: perturbation-replay-per-decision (L3a)

**Date:** 2026-06-14
**Mode:** full（2 capabilities）
**Change:** 反事实策略实验室 #3 第一步 — 逐决策扰动回放
**Base-ref:** 2158122 → HEAD

## Summary

| Dimension | Status |
|---|---|
| Completeness | tasks.md 全勾，2 capabilities（knob-perturbation-engine / perturbation-flip-report）全实现 |
| Correctness | requirements/scenarios 全有测试；引擎行为坐实——真实 open fixture 在 R:R 地板抬到 10.0 时翻转成 reject |
| Coherence | 纯编排复用 L2 `replay_decision`/`compare_decision` + L1 `cf_honesty_gate`，零新决策逻辑；红线 observability-only write-only 成立 |

**测试**：全量 `1208 passed / 4 deselected / 0 failed`（1201 + 7 L3a）；compile 干净。

## Completeness
- tasks.md 全勾。
- `knob-perturbation-engine`：`utils/perturbation_replay.py::replay_with_perturbation`（baseline 复现自检 + flip_kind 派生）；`_decision_class` / `_gate_label_changed`。
- `perturbation-flip-report`：`build_perturbation_report`（reject_reason×regime×side 分桶 + Wilson CI + 诚实 verdict + metadata）。
- 测试 `tests/test_perturbation_replay.py`（7）。

## Correctness
- **引擎坐实**：`test_perturb_tighten_rr_floor_flips_accept_to_reject` —— 真实 open_long fixture 在 `rr_floor_long_bullish=10.0` 下翻转成 reject，证明扰动真的过了真实 Judge 的 R:R gate（非 mock）。
- **baseline 复现自检**：`test_baseline_mismatch_excluded` —— 录下 decision 与 baseline replay 类不一致时标 `baseline_mismatch` 排除。
- **不可回放**：`test_not_replayable_returns_status`。
- **报表**：分桶 + flip_count + Wilson CI + verdict + `perturbed_knobs`/`fidelity_note`/`skipped_not_replayable` metadata；薄样本拒答；缺快照跳过。
- flip_kind 复用 `compare_decision._DISCRETE_ATTR`，无第二份 gate 标签定义。

## Coherence
- **零新决策逻辑**：引擎只调 `replay_decision` 两次 + `compare_decision` diff，决策全在真实 `_make_decision`。延续 L2 的"真实代码即回测代码"。
- **红线**：`utils.perturbation_replay` 仅被 tests 引用；`test_cf_red_line_guard.py::test_decision_paths_do_not_read_replay_products` 加 `perturbation_replay` 禁读断言，决策/风控路径零引用。
- design↔delta spec 无漂移（baseline 自检 Spec Patch 已回写）。

## 保真限制（已标注，非缺陷）
- 逐决策独立，**不含级联**（早期翻转改变后续状态）→ L3b 序列组合态重演。
- 只对**非 LLM 旋钮**确定（LLM 取录制内联输出）。
- 报表只声称"录下决策点的翻转率"，**不声称整策略 PnL**（待 L3b）。

## Final Assessment
**无 CRITICAL。** 2 capabilities 全实现 + 测试，引擎行为经真实 R:R 翻转坐实，全量 1208 passed 零回归，红线成立，零新决策逻辑。**Ready for archive。** L3b（序列组合态重演）为后续 change。
