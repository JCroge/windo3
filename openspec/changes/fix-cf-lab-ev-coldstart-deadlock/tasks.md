# Tasks — fix-cf-lab-ev-coldstart-deadlock

> 骨架任务，comet-design 阶段定方案后细化/拆分。

## 设计（comet-design 阶段细化）
- [x] brainstorm 选定 EV 冷启动修法（采用：镜像 Reviewer 20 窗口 + 暖启动播种）
- [x] 确认 tape 是否录有 EV gate 所需输入（坐实：snapshot 录有 _recent_win_rate=0.45 / _total=52）
- [x] 产出 Superpowers Design Doc + delta spec（4 个 capability delta，openspec validate 通过）

## 实现
- [x] 修 EV 冷启动死锁（cf_portfolio rolling 窗口 + sequential_perturbation 暖启动播种）
- [x] baseline_fidelity 改 gate-level（_gate_of_recorded/_gate_of_replayed，换 gate 拦计为不复现）
- [x] cf_direction_recommendation.load_records 按 schema v2 AND tech 非空过滤

## 测试
- [x] 端到端：floor-only 放宽至 0.3 → perturbed_cf_open=2（>0，坐实死锁已解；非禁用 EV gate 的假验证）
- [x] gate-level 保真：换 gate 拦计为不复现（test_changed_gate_counts_as_non_reproduction）
- [x] 红线守卫 `tests/test_cf_red_line_guard.py` 维持通过（4 passed）
- [x] 全量 pytest 回归：1247 passed / 4 deselected（基线 1238 +9，不回退）

## 验收
- [x] 重跑 cf_direction_recommendation.py：死锁已解（cf_open 可 >0），baseline_fidelity 从**虚假 1.0** 变为**诚实 gate-level 0.34** → `untrustworthy=True` 诚实拒答（区别于旧的死锁空转：旧为 fidelity=1.0 假信号 + 永久 cf_open=0）。**新发现（超本 change scope）**：gate-level 保真仅 0.34，揭示 CF-sim 序列重建与现实在 gate 层仅 34% 一致（疑分桶 EV/archetype 状态重建差异），是 L3b 下一个保真前沿，留后续 change。
