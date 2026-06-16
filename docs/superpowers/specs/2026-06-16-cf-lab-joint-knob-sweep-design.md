---
comet_change: cf-lab-joint-knob-sweep
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-16-cf-lab-joint-knob-sweep
status: final
---

# Design Doc — cf-lab-joint-knob-sweep（多旋钮联合扫描 + 交互效应检验）

> 技术 RFC。需求事实源是 OpenSpec delta spec `specs/joint-knob-sweep/spec.md`；本文只做实现方案/风险/测试，不重定义需求。

## 背景与动机

L4 单旋钮扫描收官，给出首个可信结论：放宽 `rr_floor_default`（1.50→1.20）与 `min_confidence`（60→40）**各自** PnL delta≈0。但单旋钮扫描有结构性盲区：**看不见旋钮间交互**。两个 reject 门若在链路上串联，单独放宽任一都会被另一个继续拦，唯有联合放宽才可能解锁开仓——单旋钮 delta≈0 无法区分「真没用」与「被另一个门掩盖」。本 change 用 2-way 因子交互项区分二者。

## 模块边界

新建 `utils/joint_knob_sweep.py`（纯离线 observability-only）。对已归档可信模块 `utils/sequential_perturbation.py` 仅做**一处纯提取重构**：

- 把 `build_delta_report` 内的局部闭包 `_summ` 提升为模块级 `_summarize_arm(arm, initial_equity)`，`build_delta_report` 与新模块共用。
- 纯提取，**行为不变**（同样的 `net_pnl / trades / win_rate / max_drawdown` 计算），由全量回归兜底。

新模块从 `sequential_perturbation` import：`run_arm`、`_gate_of_recorded`、`_summarize_arm`、`_FIDELITY_NOTE`。

三函数各一职责、独立可测：

| 函数 | 职责 | 依赖 |
|------|------|------|
| `sweep_grid(records, knob_grids, price_loader, *, baseline_config=None, fidelity_threshold=0.8, initial_equity=1000.0, max_slots=3, daily_pnl_hard_stop=-50.0, consecutive_loss_limit=3)` | 笛卡尔积扫描，baseline 臂只跑一次 | `run_arm`、`_gate_of_recorded`、`_summarize_arm` |
| `compute_interactions(grid_result, base_values)` | 交互矩阵 + 协同/可加/拮抗判定 + `(base,base)` 自检锚点 | 纯函数，无引擎 |
| `recommend_direction_nd(grid_result, base_values, *, coherence_frac=0.5, min_sample=30, actionable_min_pnl=0.0, value_penalty_k=0.1)` | 多维轴邻居孤峰守卫 + 门槛随网格点数收紧 | 纯函数，无引擎 |

## 数据流（sweep_grid）

```
recs = sorted(records, key=timestamp)
base = await run_arm(recs, baseline_config or {}, **kw)          # 跑【1 次】
fidelity = mean(base.gate[i] == _gate_of_recorded(recs[i]))
if fidelity < fidelity_threshold:
    return {combos: [], baseline_fidelity, sequence_len, untrustworthy: True, fidelity_note}
base_summary = _summarize_arm(base, initial_equity)
combos = []
for combo_values in itertools.product(*[knob_grids[k] for k in knob_keys]):   # M = ∏ |grid_i|
    perturbed_config = dict(zip(knob_keys, combo_values))
    pert = await run_arm(recs, perturbed_config, **kw)                          # 跑【M 次】
    p_summary = _summarize_arm(pert, initial_equity)
    delta = {net_pnl: p−b, win_rate: p−b, max_drawdown: p−b}
    divergence_ratio = mean(base.gate[i] != pert.gate[i])
    combos.append({combo: perturbed_config, delta, divergence_ratio,
                   perturbed_cf_open_count: pert.cf_open_count})
return {combos, baseline_fidelity: fidelity, sequence_len: len(recs),
        untrustworthy: False, baseline_cf_open_count: base.cf_open_count,
        baseline_summary: base_summary, fidelity_note: _FIDELITY_NOTE}
```

**关键**：fidelity 是 baseline 臂相对录制的属性，对全网格相同 → 算一次。笛卡尔积下省 M 倍 baseline 重算。`combo` 用 `dict(zip(knob_keys, values))`，多 key 透传给 `run_arm`（引擎对多 key config 与单 key 同路径，零改动）。

> knob_keys 取 `list(knob_grids.keys())` 固定顺序，combo 字典与 base_values 比对、product 展开都依赖该顺序一致。

## 交互项与显著性（首发 2 轴）

每个旋钮取值列表**含其生产 base 值**；`base_values = {k: base_v}` 显式传入做分类（不从 config 猜）。组合分三类：

```
                    min_confidence
                 60*       50        40
rr  1.5*  │  (base,base)  edge_B1   edge_B2     * = base 边缘参照
_floor 1.4 │   edge_A1     joint     joint
       1.3 │   edge_A2     joint     joint
       1.2 │   edge_A3     joint     joint(A3,B2)
```

- `(base,base)` = **自检锚点**：delta.net_pnl 必须 ≈0（`|delta| ≤ anchor_tol`，默认 = 显著性阈值），否则 `anchor_ok=False` 标引擎自检失败。
- edge `(a,base)`/`(base,b)` = 纯单旋钮效果。
- joint `(a,b)` 双非 base → `interaction = Δ(a,b).net_pnl − Δ(a,base).net_pnl − Δ(base,b).net_pnl`。

**显著性阈值复用推荐器同款绝对阈**（与方向推荐口径统一，诚实门控一致）：

```
effective_threshold = actionable_min_pnl × (1 + value_penalty_k × M)      # M = 组合总数
|interaction| ≤ effective_threshold        → additive   （旋钮独立；确认单旋钮 delta≈0 真没用）
 interaction > effective_threshold         → synergy    （协同；联合解锁，发现新杠杆）
 interaction < −effective_threshold        → antagonism  （拮抗；互相抵消，别一起调）
```

返回：`{interactions: [{combo:(a,b), interaction, classification, delta_ab, delta_a, delta_b}], anchor_ok, effective_threshold, fidelity_note}`。

> 首发 `compute_interactions` 只做 **2 旋钮 pairwise** 交互。引擎 `sweep_grid` 通用支持 N 轴扫描，但 N>2 的完整高阶交互分解留作未来（与 proposal 非目标一致）。组合按非 base 轴数分类：0 轴 = anchor（不入 interactions 列，仅做自检）；1 轴 = `edge`（纯单旋钮效果，入列但 interaction=None，供 joint 公式查找）；恰好 2 轴 = 计算 interaction；**≥3 轴标 `skipped:higher_order`**。

## 多维孤峰守卫（recommend_direction_nd）

推广单旋钮一维「前后邻居连贯」到网格：

```
trustworthy = [c for c in combos if not untrustworthy and sequence_len ≥ min_sample and delta]
best = max(trustworthy, key=delta.net_pnl)
effective_min = actionable_min_pnl × (1 + value_penalty_k × M)     # 同交互阈值口径
if best.delta.net_pnl ≤ effective_min: → no_actionable_direction (below_threshold)

# 轴邻居：沿每个旋钮轴 ±1 step 的相邻组合（曼哈顿距离=1）
neighbors = combos where exactly one knob index differs by ±1, others equal
coherent = any(nb.delta.net_pnl ≥ best.delta.net_pnl × coherence_frac for nb in neighbors)
if not coherent: → no_actionable_direction (isolated_spike=True)
else: → recommend {recommended_combo, delta_net_pnl, confidence, sample, baseline_fidelity}
```

`confidence` 沿用单旋钮三因子（`fidelity × div_factor × sample_factor`）。输出含 `all_combos` + 交互矩阵 + `fidelity_note`，报全貌供人看趋势。

## 不变量 / 红线

- observability-only write-only；红线守卫 `tests/test_cf_red_line_guard.py` 扩展覆盖 `joint_knob_sweep`（禁任何 gate/veto/halt/rank/daily-stop import）。
- 不改 live Judge 决策逻辑、不改生产 config、不改 choppy 地板 1.50、无需 event_backtest。
- 推荐绝不自动应用到线上 config（人审）；证据不足拒答不杜撰。
- 继承 L3b 保真天花板（`fidelity_note`）：退出仅 SL/TP/24h，结论以 delta（两臂相消系统偏差）为主非绝对值。
- `_summ → _summarize_arm` 纯提取，`build_delta_report` 输出不变（回归断言）。

## 测试策略

数学与引擎**解耦**是核心：

1. **`compute_interactions` 纯函数测**（确定性、快、不过引擎）：喂构造的 grid_result（注入已知 delta），断言三判定：
   - 协同：joint delta ≫ 两 edge 之和 → `synergy`
   - 可加：joint delta ≈ 两 edge 之和 → `additive`
   - 拮抗：joint delta ≪ 两 edge 之和 → `antagonism`
   - `(base,base)` delta≈0 → `anchor_ok=True`；注入非零 → `anchor_ok=False`
2. **`sweep_grid` 测**：monkeypatch `run_arm` 计数 + 记录入参，断言：
   - 组合数 = ∏ 各轴取值；perturbed_config 多 key 正确（`dict(zip(keys, values))`）
   - base 臂只调 1 次（计数）；untrustworthy（mock 低 fidelity）短路返回空 combos
3. **`recommend_direction_nd` 测**：构造 grid_result 验证轴邻居连贯推荐 / 孤立尖刺 `isolated_spike` / `effective_min` 随 M 收紧 / 薄样本剔除。
4. **红线守卫**：扩展 `test_cf_red_line_guard.py` 断言生产模块不 import `joint_knob_sweep`。
5. **全量回归**：基线 1255 不回退；`build_delta_report` 经 `_summarize_arm` 提取后输出不变。

## Driver

`cf_direction_recommendation.py` 增一段（或新 driver `cf_joint_sweep_recommendation.py`）：用真实磁带跑 `rr_floor_default × min_confidence` 两轴联合扫描 → `compute_interactions` 出交互矩阵 + verdict → `recommend_direction_nd`。产出可信结论：两门是否存在交互效应。

## 风险

| 风险 | 缓解 |
|------|------|
| 触碰已归档可信模块（`_summ` 提取） | 纯提取不改行为；全量回归 + `build_delta_report` 输出不变断言兜底 |
| 笛卡尔积算力（M 次 run_arm，每次跑整序列 N 条真实 Judge 重演） | baseline 复用已省 M 倍 base 臂；首发 2 轴小网格（如 4×3=12）；值列表显式可控 |
| 交互项被 L3b 保真噪声淹没（delta 本身估算有误差） | 阈值复用诚实门控 + 多重比较收紧；anchor 自检；继承 fidelity_note 警示以 delta 为主 |
| N>2 高阶交互复杂度 | 首发只做 2 轴 pairwise，N>2 标 `skipped:higher_order` |
