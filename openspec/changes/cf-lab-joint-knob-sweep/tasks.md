# Tasks — cf-lab-joint-knob-sweep

> 骨架，comet-design 定方案后细化。

## 设计（comet-design）
- [ ] brainstorm 选定方案（A 新建 `utils/joint_knob_sweep.py` vs B 重构 build_delta_report）
- [ ] 确认交互项定义 + base 值纳入网格 + 自检锚点边界
- [ ] 确认多维孤峰守卫的轴邻居语义 + 多重比较门槛收紧公式
- [ ] 产出 Design Doc + delta spec（joint-knob-sweep，validate 通过）

## 实现
- [ ] `sweep_grid(records, knob_grids, ...)`：baseline 臂跑一次 + 笛卡尔积 perturbed 臂，复用 L3b run_arm
- [ ] `compute_interactions(grid_result)`：边缘/联合点分类 + 交互项矩阵 + 协同/可加/拮抗判定 + (base,base) 自检
- [ ] `recommend_direction_nd(...)`：多维轴邻居孤峰守卫 + 门槛随网格点数收紧 + 报全貌
- [ ] driver：`cf_direction_recommendation.py` 增两轴（rr_floor_default × min_confidence）联合扫描段（或新 driver）

## 测试
- [ ] `sweep_grid` 笛卡尔积组合数 = ∏ 各轴取值，每组合多 key perturbed_config 正确透传
- [ ] baseline 臂只跑一次（复用），untrustworthy 时整体拒答
- [ ] `(base,base)` 自检锚点 delta≈0
- [ ] 交互项计算正确（构造已知协同/可加/拮抗的合成数据验证三种判定）
- [ ] 多维孤峰守卫：轴邻居连贯才推荐，孤立尖刺标 isolated_spike
- [ ] 红线守卫 `tests/test_cf_red_line_guard.py` 覆盖新模块（禁生产链路 import）
- [ ] 全量 pytest 回归不回退（基线 1255）

## 验收
- [ ] 跑真实磁带两轴联合扫描：产出交互矩阵 + verdict（协同/可加/拮抗）
- [ ] 给出可信结论：rr_floor × min_confidence 是否存在交互效应（区分「单旋钮真没用」vs「被另一个门掩盖」）
- [ ] 记录基线数（1255 → 新增 N）+ 实验室端到端 fidelity 仍跨可信线
