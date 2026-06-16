# Tasks — fix-cf-lab-ev-coldstart-deadlock

> 骨架任务，comet-design 阶段定方案后细化/拆分。

## 设计（comet-design 阶段细化）
- [ ] brainstorm 选定 EV 冷启动修法（方案 A 读录制 EV / B 暖启动先验 / C 贴 live，含掩盖级联风险评估）
- [ ] 确认 tape 是否录有 EV gate 所需输入（决定方案 A 可行性）
- [ ] 产出 Superpowers Design Doc + delta spec（counterfactual-portfolio-sim / sequential-perturbation-driver / perturbation-delta-report / replay-report-driver）

## 实现
- [ ] 修 EV 冷启动死锁（按选定方案改 cf_portfolio / sequential_perturbation）
- [ ] baseline_fidelity 改 gate-level（reject_reason / 触达 gate 一致才算复现）
- [ ] cf_direction_recommendation.load_records 按 schema v2 AND tech 非空过滤

## 测试
- [ ] 端到端：rr_below_floor 记录在 build_delta_report 下放宽地板须产生 perturbed_cf_open>0（坐实死锁已解）
- [ ] gate-level 保真：CF-sim 换 gate 拦（EV vs rr）须计为不复现 / 反映到 untrustworthy
- [ ] 红线守卫 `tests/test_cf_red_line_guard.py` 维持通过（observability-only 不放松）
- [ ] 全量 pytest 回归（基线 1238，不回退）

## 验收
- [ ] 重跑 cf_direction_recommendation.py：能产出非零 delta 或可信的 no_actionable_direction（区别于死锁空转）
