# Tasks — fix-cf-lab-symbol-state-injection

> 骨架，comet-design 定方案后细化。

## 设计（comet-design）
- [ ] brainstorm 选定方案（A `_inject_cf_state` 以录制 _symbol_state 为基 + CF overlay / B to_snapshot 接种合并）
- [ ] 确认字段分类边界（决策输入还原 vs position-outcome CF overlay）+ perturbed 臂级联不削弱
- [ ] 产出 Design Doc + delta spec（sequential-perturbation-driver）

## 实现
- [ ] `_inject_cf_state` 的 `_symbol_state` 基于录制快照（还原 last_tech/trend_streak/last_decision_time）
- [ ] CF overlay 自身开过仓 symbol 的 position-outcome 字段（last_open_time/last_force_close_time）

## 测试
- [ ] sequential baseline fidelity ≥0.85（坐实 ~0.91，对照修前 0.798）
- [ ] perturbed 臂级联保留：CF 开仓后该 symbol 的 position-outcome 反映 CF 自身（非录制 reality）
- [ ] 红线守卫 `tests/test_cf_red_line_guard.py` 维持
- [ ] 全量 pytest 回归（基线 1252 不回退）

## 验收
- [ ] 重跑 cf_direction_recommendation.py：baseline_fidelity 从 0.798 升至 ≥0.85，untrustworthy 解除，实验室端到端可给方向或可信 no_actionable_direction
