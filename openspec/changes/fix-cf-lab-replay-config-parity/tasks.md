# Tasks — fix-cf-lab-replay-config-parity

> 骨架，comet-design 定方案后细化。

## 设计（comet-design）
- [ ] brainstorm 选定 config 基线方案（A DEFAULTS / B 录制 resolved config / C 折中）
- [ ] 确认 perturbation 叠加语义（只覆盖目标旋钮，两臂同生产基线起步）
- [ ] 产出 Design Doc + delta spec（deterministic-replay-harness / sequential-perturbation-driver / replay-report-driver）

## 实现
- [ ] replay/CF-sim baseline 用生产 config（build_delta_report/run_arm baseline 臂）
- [ ] sweep_knob + cf_direction_recommendation 驱动以生产 config 为基线，perturbation 覆盖其上
- [ ] （按方案）decision_replay 默认 config 注入点对齐生产

## 测试
- [ ] 全量 v2 磁带 L2 fidelity 用生产 config ≥ 0.85（坐实 0.90，区别于 config={} 的 0.365）
- [ ] perturbation 叠加正确：扰动单旋钮不重置其它旋钮（造 fixture 验证）
- [ ] 红线守卫 `tests/test_cf_red_line_guard.py` 维持
- [ ] 全量 pytest 回归（基线 1247 不回退）

## 验收
- [ ] 重跑 cf_direction_recommendation.py：baseline_fidelity 从 0.34 升至 ≥0.85，untrustworthy 解除，能给出方向或可信 no_actionable_direction（非 untrustworthy 拒答）
