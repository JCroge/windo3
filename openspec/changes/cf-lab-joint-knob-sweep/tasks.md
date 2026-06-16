# Tasks — cf-lab-joint-knob-sweep

> comet-design 选定方案 A（新建 `utils/joint_knob_sweep.py`，对 sequential_perturbation 仅一处纯提取）。

## 设计（comet-design）
- [x] brainstorm 选定方案（**采用 A**：新建 `utils/joint_knob_sweep.py`；对 sequential_perturbation.py 仅纯提取 `_summarize_arm`；B 重构 build_delta_report 因碰可信模块风险大不采）
- [x] 确认交互项定义 `interaction = Δ(a,b) − Δ(a,base) − Δ(base,b)` + base 值纳入网格作边缘 + (base,base) 自检锚点
- [x] 确认多维孤峰守卫的轴邻居语义（曼哈顿距离=1）+ 多重比较门槛 `actionable_min_pnl × (1+k×M)` 收紧
- [x] 产出 Design Doc + delta spec（joint-knob-sweep，validate 通过）

## 实现
- [x] `sweep_grid(records, knob_grids, ...)`：baseline 臂跑一次复用 + 笛卡尔积 perturbed 臂，复用 L3b run_arm（commit 8cfea4a）
- [x] `compute_interactions(grid_result, base_values)`：edge/joint/higher-order 分类 + 交互项矩阵 + 协同/可加/拮抗判定 + (base,base) 自检（commit a19e7cd + a072bf1 修 edge 标签 + eb37389 epsilon 地板）
- [x] `recommend_direction_nd(...)`：多维轴邻居孤峰守卫 + 门槛随网格点数收紧 + 报全貌（commit adde137 + 3c320c7 polish）
- [x] driver：`cf_direction_recommendation.py` 增两轴（rr_floor_default × min_confidence）联合扫描段（commit 34c0588）
- [x] 纯提取 `_summarize_arm`（sequential_perturbation.py，行为不变，commit e520717）

## 测试
- [x] `sweep_grid` 笛卡尔积组合数 = ∏ 各轴取值，每组合多 key perturbed_config 正确透传
- [x] baseline 臂只跑一次（复用），untrustworthy 时整体拒答
- [x] `(base,base)` 自检锚点 delta≈0（含 epsilon 地板）+ edge/missing_edge/higher_order 分类
- [x] 交互项计算正确（构造已知协同/可加/拮抗的合成数据验证三种判定）
- [x] 多维孤峰守卫：轴邻居连贯才推荐，孤立尖刺标 isolated_spike，below_threshold 门槛收紧
- [x] 红线守卫 `tests/test_cf_red_line_guard.py` 覆盖新模块（显式禁生产链路 import joint_knob_sweep，commit ff55a34）
- [x] 全量 pytest 回归不回退（基线 1255 → 1270，新增 +15：joint_knob_sweep 13 + sequential_perturbation _summarize_arm 2）

## 验收
- [x] 跑真实磁带两轴联合扫描（853 条 v2+tech 可回放磁带 + klines_1s.db）：产出交互矩阵 + verdict（见 verify 报告）
- [x] 给出可信结论：rr_floor × min_confidence 是否存在交互效应（区分「单旋钮真没用」vs「被另一个门掩盖」）→ 详见 comet-verify
- [x] 记录基线数（1255 → 1270，+15）+ 实验室端到端 fidelity 仍跨可信线
