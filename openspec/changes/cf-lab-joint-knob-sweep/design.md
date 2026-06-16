# Design (high-level) — cf-lab-joint-knob-sweep

> OpenSpec 高层草图；详细 RFC + 方案权衡定在 comet-design 的 Superpowers Design Doc。

## 引擎层：多旋钮几乎免费

```
build_delta_report(records, baseline_config, perturbed_config, ...)
                                              └─ dict 透传给 run_arm
  现状 sweep_knob:  perturbed_config = {knob: v}        (1 key)
  目标 sweep_grid:  perturbed_config = {k1:v1, k2:v2}   (N key)  ← 引擎照跑,零改动
```

`run_arm` 接受任意 config dict（base 上 overlay），多 key 与单 key 同路径。改动全在 L4 上层。

## 笛卡尔积 + baseline 复用

```
knob_grids = {rr_floor_default: [1.5*, 1.4, 1.3, 1.2],   (* = base 边缘参照)
              min_confidence:   [60*,  50,  40]}
            → product = 4 × 3 = 12 组合

baseline 臂:  跑 1 次 (baseline_config, 录制对照) → fidelity 对 12 组合通用
perturbed 臂: 跑 12 次 (每组合一个 perturbed_config) → 各自 delta vs 共享 baseline
```

效率：现 `build_delta_report` 每调用内重跑 baseline 臂 → 笛卡尔积下浪费 N 倍。新引擎把 baseline 臂提到外层跑一次（fidelity 本是 baseline 属性，对所有组合相同），仅 perturbed 臂随组合变。

## 核心产出：交互效应（2-way 因子交互项）

```
                    min_confidence
                 60*      50       40
rr  1.5*  │  Δ=0(锚)   Δ(B1)    Δ(B2)    ┐ 边缘 = 纯 min_confidence 效果
_floor 1.4 │  Δ(A1)    Δ(A1,B1) ...      │
       1.3 │  Δ(A2)    ...               │ 内部 = 联合点
       1.2 │  Δ(A3)    ...     Δ(A3,B2)  ┘
              └ 边缘 = 纯 rr_floor 效果

interaction(a,b) = Δ(a,b) − Δ(a,base) − Δ(base,b)
  ≈ 0      → 可加, 旋钮独立 (确认单旋钮 delta≈0 是真的没用)
  ≫ 0      → 协同, 联合解锁了单独看不到的开仓 (发现新杠杆!)
  ≪ 0      → 拮抗, 互相抵消 (别一起调)
```

自检锚点：`(base,base)` 组合 delta 必须≈0（同 config 两臂），否则引擎有 bug。

## 多维孤峰守卫（推荐用，非交互检验用）

```
一维 (现状):  best 的前后两个值同向才算趋势
多维 (目标):  best 在网格上沿每个轴 ±1 step 的轴邻居,
              至少一个同向(delta ≥ best*coherence_frac) 才算趋势, 否则 isolated_spike
多重比较:     有效门槛 ∝ 网格点数(∏ 各轴取值), 比单旋钮 len 收得更紧
```

## 候选方案（comet-design 定夺）

- **A. 新建 `utils/joint_knob_sweep.py`**：`sweep_grid(records, knob_grids, ...)` 自跑一次 baseline 臂 + 笛卡尔积 perturbed 臂；`compute_interactions(grid_result)` 算交互矩阵；`recommend_direction_nd(...)` 多维守卫推荐。复用 `run_arm`/`build_delta_report` 的 summary 逻辑。最小耦合，单旋钮模块不动。
- **B. 重构 `build_delta_report` 支持外部预算 baseline + `knob_sweep.py` 扩展多旋钮**：复用度高但改动现有已归档可信模块，风险大。
- 共同约束：observability-only；不改 `run_arm`/Judge/生产 config；交互项与推荐都继承 L3b `fidelity_note` 保真天花板。

## 不变量 / 红线

- observability-only write-only；红线守卫 `tests/test_cf_red_line_guard.py` 扩展覆盖新模块（禁生产链路 import）。
- 不改 live Judge 决策逻辑、不改生产 config、不改 choppy 地板 1.50、无需 event_backtest。
- 推荐绝不自动应用到线上 config（人审）；证据不足拒答不杜撰。
- 继承 L3b 保真边界：退出仅 SL/TP/24h，结论以 delta（两臂相消系统偏差）为主非绝对值。
