# Verification Report: perturbation-knob-sweep (L4)

**Date:** 2026-06-14
**Mode:** full（2 capabilities）
**Change:** 反事实策略实验室 #4 — 旋钮扫描 + 方向推荐（**实验室 L1-L4 收官**）
**Base-ref:** b850e70 → HEAD

## Summary

| Dimension | Status |
|---|---|
| Completeness | tasks.md 全勾，2 capabilities（knob-sweep-engine / direction-recommender）全实现 |
| Correctness | requirements/scenarios 全有测试；扫描跑通真实 L3b 管线；多重比较守卫（连贯/孤峰/拒答）已测 |
| Coherence | 纯编排复用 L3b `build_delta_report` + L1 `cf_honesty_gate`，零新决策逻辑；红线 observability-only write-only 成立 |

**测试**：全量 `1223 passed / 4 deselected / 0 failed`（1217 + 6 L4）；compile 干净。

## Completeness
- tasks.md 全勾。
- `knob-sweep-engine`：`utils/knob_sweep.py::sweep_knob`（单旋钮显式值列表逐值跑 L3b，聚合 delta + 信任/样本元数据）。
- `direction-recommender`：`recommend_direction`（门控 + 排名 + 多重比较守卫 + confidence 三因子）。
- 测试 `tests/test_knob_sweep.py`（6：2 sweep 跑通真实管线 + 4 recommend 逻辑）。

## Correctness
- **扫描真实管线**：`test_sweep_collects_per_value` 经 L3b `build_delta_report` → L2 `replay_decision` → 真实 `_make_decision` 逐值跑通。
- **多重比较守卫（L4 诚实核心）**：`test_recommend_coherent_trend`（连贯单调趋势 → 推荐最优值）/ `test_recommend_isolated_spike_refused`（孤立尖刺 → `isolated_spike` 拒答）/ `test_recommend_no_trustworthy_refused`（untrustworthy+薄样本剔除 → 拒答）/ `test_recommend_below_threshold_refused`（改善不显著 → 拒答）。
- actionable 门槛随扫描值数收紧（`effective_min = actionable_min_pnl × (1 + 0.1×N)`）抵消选择性偏差。
- confidence 三因子（baseline_fidelity × divergence 惩罚 × 样本档）+ 报出三原始因子 + `all_values` 全貌 + `fidelity_note`。

## Coherence
- **零新决策逻辑**：sweep 逐值调 `build_delta_report`，recommend 纯门控/排名/守卫。延续实验室"真实代码即回测代码"。
- **红线**：`knob_sweep` 仅被 tests 引用；`test_cf_red_line_guard.py` 加 `knob_sweep` 禁读断言，决策/风控路径零引用；推荐**绝不自动改线上 config**（人审）。
- design↔delta spec 无漂移（多重比较守卫 Spec Patch 已回写）。

## 保真限制（已标注，继承 L3b）
- 退出仅 SL/TP/24h；误差沿序列累积；结论以 delta 为主非绝对值；单旋钮 1D（多旋钮组合爆炸留后续）。
- 真实磁带数据需累积（埋点 2026-06-13 上线）后才能跑实战推荐。

## Final Assessment
**无 CRITICAL。** 2 capabilities 全实现 + 测试，扫描跑通真实 L3b 管线，多重比较守卫（L4 独有诚实陷阱）已测，全量 1223 passed 零回归，红线成立（绝不自动改线上 config），零新决策逻辑。**Ready for archive。**

**🎉 反事实策略实验室 L1→L2→L3a→L3b→L4 全部完成**（基线 1149→1223）：拿真实决策磁带喂真实 Judge、扰动任意非 LLM 旋钮、量化整策略 delta、自动诚实方向推荐。后续可选：多旋钮联合扫描 / LLM 旋钮 / 真实磁带累积后实战推荐。
