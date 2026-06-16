# Verification Report: cf-lab-joint-knob-sweep

> 完整验证（verify_mode=full）。日期 2026-06-16。base-ref c2d2e76。

## Summary

| Dimension | Status |
|-----------|--------|
| Completeness | 19/19 tasks ✅；5/5 requirements 实现 |
| Correctness | 5/5 requirements 覆盖，13 module + 2 helper 测试，真跑磁带验收通过 |
| Coherence | 符合 design doc + delta spec，无漂移；observability-only 红线守卫扩展 |

**全量回归**：`1270 passed / 4 deselected / 1 warning`（基线 1255 → +15，零回退）。
**openspec validate**：valid。

## Requirement → 实现 → 测试 映射

| # | Requirement | 实现 | 测试 |
|---|-------------|------|------|
| 1 | 多旋钮笛卡尔积联合扫描 | `sweep_grid`（itertools.product，多 key perturbed_config 透传，复用 L3b run_arm） | `test_sweep_grid_cartesian_and_baseline_reuse`（组合数=∏、多 key 透传） |
| 2 | baseline 臂单次复用 | `sweep_grid`（base 臂跑一次提到循环外 + untrustworthy 短路） | `test_sweep_grid_cartesian_and_baseline_reuse`（call 计数=1）+ `test_sweep_grid_untrustworthy_short_circuit` |
| 3 | 交互效应量化（base 纳入网格/锚点/显著性阈值） | `compute_interactions`（interaction=Δ(a,b)−Δ(a,base)−Δ(base,b)，edge/joint/higher_order 分类，(base,base) 锚点 + epsilon 地板，阈值 actionable_min_pnl×(1+k×M)） | `test_additive` / `test_synergy` / `test_antagonism` / `test_anchor_fail` / `test_edge_combos_labeled_edge` / `test_missing_edge_skipped` / `test_higher_order_skipped` |
| 4 | 多维孤峰守卫的方向推荐 | `recommend_direction_nd` + `_axis_neighbors`（曼哈顿距离=1 轴邻居连贯，门槛随 M 收紧，报全貌） | `test_recommend_coherent_neighbor` / `test_recommend_isolated_spike` / `test_recommend_below_threshold` / `test_recommend_reports_all_combos` |
| 5 | observability-only 绝不自动应用 | 模块 docstring 红线声明；driver 在 repo 根非生产路径 | `test_cf_red_line_guard.py`（显式断言生产链路不 import joint_knob_sweep） |

辅助：`_summarize_arm` 纯提取（sequential_perturbation.py，行为不变，`test_summarize_arm_extracted_helper` / `test_summarize_arm_empty_realized`）。

## 真实磁带验收（科学结论）

用 853 条 v2+tech 可回放磁带 + `klines_1s.db` 跑 `cf_direction_recommendation.py`：

- **baseline_fidelity = 0.9472**（> 0.85 可信线），sequence_len=853，anchor_ok=True，零扰动 div=0.0 → 实验室端到端仍跨可信线。
- **交互矩阵：全部 `additive`，interaction=+0.00**（4×3 网格，rr_floor 1.50→1.20 × min_confidence 60→40）。
- **关键发现**：随旋钮放宽，`divergence_ratio` 显著增长（联合最宽松 (1.20,40) div=0.899，即 ~90% 决策 gate-label 改变），但**所有组合 net_pnl delta = +0.00，CF 开仓数恒=2**（=baseline）。

**可信结论**：rr_floor_default × min_confidence **无交互效应（纯可加/独立）**。即使联合放宽到翻转 90% 决策 gate-label，也未解锁任何额外盈利开仓（CF opens 恒 2，PnL delta 恒 0）。这**证伪了"单旋钮 delta≈0 是被另一个门掩盖"的假设**——两门翻转的只是 reject→其它 reject 级联，不触达盈利开仓。独立、更强地佐证 choppy R:R 地板 1.50 维持的合理性（reject 被多 gate 过度决定，非该两门可解）。`recommend_direction_nd` 正确返回 `no_actionable_direction`（best delta=0 ≤ 门槛），不杜撰方向。

## Issues

- CRITICAL：无。
- WARNING：无。
- SUGGESTION：`_delta_of` 为 O(n) 线性扫描（每 combo 调用 → O(n²)），首发小网格（≤16 combos）无影响；若未来网格 >20 combos 可改 dict 索引。已在代码审查记录，非阻塞。

## Final Assessment

All checks passed. 5/5 requirements 实现并测试，真实磁带验收产出可信交互结论，全量回归零回退，observability-only 红线守卫扩展。Ready for archive。
