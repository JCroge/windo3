# Comet Design Handoff

- Change: perturbation-knob-sweep
- Phase: design
- Mode: compact
- Context hash: 6c1372856bf88009dc4f25b6af48594010dda198898040fb8595e62a1b0b3bcb

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/perturbation-knob-sweep/proposal.md

- Source: openspec/changes/perturbation-knob-sweep/proposal.md
- Lines: 1-29
- SHA256: e53f8e9bd8927ff49fe13e99859e5cf56f0bb274064170ef169c631a9ada9b06

```md
## Why

反事实策略实验室路线图 #4，收官层。L1-L3 已把"拿真实磁带喂真实 Judge、扰动旋钮、量化整策略 delta"的能力建齐：L3b `build_delta_report` 能对单个 perturbed_config 给出 baseline-vs-perturbed 的 PnL/胜率/回撤 delta + baseline 序列保真自检。L4 在其上做编排——把一个旋钮在值域 grid 上**扫描**，逐值跑 L3b，按 delta 净 PnL 排名，配诚实门控自动给出**方向推荐**："这旋钮往这调，+X%，置信度 Y，样本 N"；证据不足时明确拒答"无可行方向"。

这是用户最初问题（"放宽 choppy R:R 地板会怎样"）的**自动化、可重复、带诚实门控**的答案出口。

## What Changes

- **新增旋钮扫描引擎**：对一个旋钮的 grid 值域，逐值 `build_delta_report(baseline_config={}, perturbed_config={knob: value})`，收集每个值的 delta（净 PnL/胜率/回撤）+ baseline_fidelity + divergence_ratio + 样本量。
- **新增方向推荐器**：门控（剔除 L3b untrustworthy + L1 薄样本）→ trustworthy 值按 delta 净 PnL 排名 → 最优值若 actionable（净 PnL delta 显著 > 0，CI 不跨 0）输出方向推荐 + 量化；否则"无可行方向（证据不足）"，**绝不杜撰方向**。
- 推荐随结论报出样本量 / 置信度 / baseline 保真度 / 保真天花板。
- 全程 observability-only write-only；**只出推荐绝不自动改线上 config（人审）**。

## Capabilities

### New Capabilities
- `knob-sweep-engine`: 单旋钮 grid 扫描——逐值跑 L3b `build_delta_report`，聚合每值 delta + 信任/样本元数据。
- `direction-recommender`: 诚实门控 + 排名 + actionable 判定 → 方向推荐或拒答（证据不足不杜撰）。

### Modified Capabilities
<!-- 无：复用 L3b perturbation-delta-report、L1 cf-honesty 既有能力，本 change 为新增编排+推荐层。 -->

## Impact

- **新增代码**：扫描引擎 + 推荐器（如 `utils/knob_sweep.py`：`sweep_knob(records, knob, values, price_loader)` + `recommend_direction(sweep_result, ...)`）。
- **复用既有**：L3b `build_delta_report`（逐值调用）、L1 `cf_honesty_gate`（样本/置信）、L3b baseline_fidelity 门控。
- **保真天花板（明确标注）**：继承 L3b——退出仅 SL/TP/24h、误差沿序列累积、结论以 delta 为主非绝对值。L4 推荐必带这些限制 + 样本量 + 置信度。
- **红线合规**：observability-only write-only，扫描/推荐产物严禁被任何 gate/veto/halt/rank/daily-stop 读取（守卫扩展）；**绝不自动应用到线上 config**（只出建议，人审）。
- **非目标（后续）**：多旋钮联合扫描（组合爆炸）；LLM 旋钮；自动改线上 config。
```

## openspec/changes/perturbation-knob-sweep/design.md

- Source: openspec/changes/perturbation-knob-sweep/design.md
- Lines: 1-54
- SHA256: 9f85955e79dd9fd0237024d4c725b814419f8b50e77c898df85362f40d91fbda

```md
## Context

反事实策略实验室 L4（收官）。L3b `build_delta_report(records, baseline_config, perturbed_config, price_loader, *, fidelity_threshold, ...)` 返回 `{baseline, perturbed, delta:{net_pnl,win_rate,max_drawdown}, metadata:{baseline_fidelity, untrustworthy, divergence_ratio, sequence_len, fidelity_note, ...}}`（baseline_fidelity < 阈值时 delta=None+untrustworthy）。L4 是其编排：grid 扫描 + 排名 + 诚实推荐。

红线（CLAUDE.md）：observability-only write-only；绝不自动改线上 config。

## Goals / Non-Goals

**Goals:**
- 单旋钮 grid 扫描：逐值跑 L3b，聚合 delta + 信任/样本元数据。
- 方向推荐：门控 + 排名 + actionable → 推荐或拒答（证据不足不杜撰）。
- 推荐带样本量/置信度/baseline 保真度/保真天花板。
- observability-only write-only，绝不自动应用。

**Non-Goals:**
- 多旋钮联合扫描（组合爆炸）。
- LLM 旋钮。
- 自动改线上 config（只出建议，人审）。

## Decisions（4 叉子收口）

### D1（叉子①）— 单旋钮 1D 扫描
`sweep_knob(records, knob, values, price_loader, baseline_config={})`：对 `values` 中每个 v，跑 `build_delta_report(records, {}, {knob: v}, price_loader)`，收集 `{value, delta, baseline_fidelity, untrustworthy, divergence_ratio, sequence_len}`。多旋钮组合爆炸留后续。

### D2（叉子④）— grid 显式值列表
`values` 是显式值列表（`[1.3, 1.4, 1.5, 1.6, 1.7]`），不是 range+step——显式更可控、可非均匀、避免浮点步长坑。

### D3（叉子②）— 排名/推荐判据
`recommend_direction(sweep_result, *, min_sample, actionable_min_pnl)`：
1. **门控**：剔除 `untrustworthy=True`（L3b baseline_fidelity 不足）与 `sequence_len < min_sample`（L1 诚实）。
2. **排名**：剩余 trustworthy 值按 `delta.net_pnl` 降序。
3. **actionable 判定**：最优值的 `delta.net_pnl > actionable_min_pnl`（显著正）且与 baseline（v=当前生产值或 0 扰动）相比净改善 → 输出方向推荐。否则 `verdict="no_actionable_direction"`（绝不杜撰）。
4. 输出：`{verdict, recommended_value, delta_net_pnl, confidence, sample, baseline_fidelity, fidelity_note}`。

### D4（叉子③）— 置信度派生
`confidence` 从三因子派生（observability 标签非精确概率）：`baseline_fidelity`（越高越可信）× `(1 - divergence_ratio 过高惩罚)` × 样本量档（L1 三档 INSUFFICIENT/low/actionable）。低任一 → confidence 低/拒答。报表同时报出三原始因子供人核对，不把它们藏进单一数字。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| 扫描出"最优"但其实噪声 | 门控（untrustworthy/薄样本）+ actionable CI/阈值 + 拒答 no_actionable_direction |
| 多值扫描放大过拟合（多重比较） | 单旋钮 1D 起步；报出全部值的 delta 供人看趋势非只挑最优；保真天花板随报 |
| 用户误把推荐当自动应用 | 红线：只出建议，绝不改线上 config；文档显式人审 |
| 继承 L3b 保真天花板 | fidelity_note 随每条推荐报出 |
| 红线误用 | 守卫测试扩展 |

## Migration Plan
纯新增离线编排层，无生产改动、无 schema 迁移。回滚=删模块。

## Open Questions（build 收口）
- `actionable_min_pnl` 默认阈值与 confidence 派生函数细节。
- 推荐里"方向"的措辞（value 升/降 + 量化）。
- baseline 取 v=生产默认（perturbed_config={}）作 0 扰动锚点。
```

## openspec/changes/perturbation-knob-sweep/tasks.md

- Source: openspec/changes/perturbation-knob-sweep/tasks.md
- Lines: 1-24
- SHA256: 818bd8f1b8c13e52c43a34491d004f9b8124f3d7506b1bb1f667086db5790031

```md
# Tasks — perturbation-knob-sweep (L4)

> 反事实策略实验室 #4 收官层。observability-only write-only，绝不自动改线上 config。复用 L3b build_delta_report + L1 诚实 gate。

## 1. 旋钮扫描引擎（knob-sweep-engine）

- [ ] 1.1 新建 `utils/knob_sweep.py`：`sweep_knob(records, knob, values, price_loader, *, baseline_config={})` 逐值跑 L3b `build_delta_report`，收集 `{value, delta, baseline_fidelity, untrustworthy, divergence_ratio, sequence_len}`
- [ ] 1.2 单测（合成短序列 fixture）：逐值跑 + 聚合、显式值列表、untrustworthy 值带标记

## 2. 方向推荐器（direction-recommender）

- [ ] 2.1 `recommend_direction(sweep_result, *, min_sample, actionable_min_pnl)`：门控（剔 untrustworthy/薄样本）+ 按 delta 净 PnL 排名 + actionable 判定 → recommend / no_actionable_direction
- [ ] 2.2 confidence 三因子派生（baseline_fidelity × divergence 惩罚 × 样本档）+ 报出三原始因子 + fidelity_note
- [ ] 2.3 单测：actionable 给推荐、无 trustworthy 拒答、改善不显著拒答、三因子透明、薄样本剔除

## 3. 红线守卫 + 文档

- [ ] 3.1 扩展 `tests/test_cf_red_line_guard.py`：决策/风控路径不读 `knob_sweep` 产物
- [ ] 3.2 docs：CLAUDE.md 红线补 L4 声明（绝不自动改线上 config）；docs/to-do-list.md 路线图（#4 完成 = 实验室 L1-L4 全收官）；memory roadmap 标 L4 完成

## 4. 验证

- [ ] 4.1 全量 `python3 -m pytest -q` 通过，基线不回归（当前 1217，只增不减）
- [ ] 4.2 `python3 -m compileall -q .` 通过
```

## openspec/changes/perturbation-knob-sweep/specs/direction-recommender/spec.md

- Source: openspec/changes/perturbation-knob-sweep/specs/direction-recommender/spec.md
- Lines: 1-45
- SHA256: c555c9ca967d25a11a45f8934833ad2144e96a78640b57e3230200eea4b60f53

```md
## ADDED Requirements

### Requirement: 诚实门控 + 排名 + actionable 推荐
系统 SHALL 对扫描结果先门控（剔除 untrustworthy 与薄样本），再按 delta 净 PnL 排名，最优值 actionable 时输出方向推荐，否则拒答，绝不杜撰方向。

#### Scenario: 剔除不可信值
- **WHEN** 某扫描值 `untrustworthy=True`（L3b baseline_fidelity 不足）或 `sequence_len < min_sample`
- **THEN** 系统 SHALL 把它排除出排名

#### Scenario: actionable 给推荐
- **WHEN** 排名最优的 trustworthy 值 `delta.net_pnl > actionable_min_pnl`（显著正改善）
- **THEN** 系统 SHALL 输出 `{verdict="recommend", recommended_value, delta_net_pnl, confidence, sample, baseline_fidelity}`

#### Scenario: 证据不足拒答
- **WHEN** 无 trustworthy 值，或最优值改善不显著
- **THEN** 系统 SHALL 输出 `verdict="no_actionable_direction"`，SHALL NOT 编造方向

### Requirement: 多重比较守卫 — 连贯趋势才推荐
系统 SHALL 防止"扫一排挑最高"的选择性偏差：报出全部值的 delta 全貌；最优值必须是连贯趋势（相邻值同向）而非孤立尖刺才推荐；actionable 门槛随扫描值数收紧。

#### Scenario: 报出全貌
- **WHEN** 生成推荐
- **THEN** 输出 SHALL 含 `all_values`（每个扫描值的 delta + 信任元数据），供人看趋势非只看赢家

#### Scenario: 孤立尖刺拒答
- **WHEN** 排名最优值的 delta 远高于其相邻值（相邻不同向，疑似噪声尖刺）
- **THEN** 系统 SHALL 标 `isolated_spike` 并 SHALL NOT 推荐该值

#### Scenario: 门槛随值数收紧
- **WHEN** 扫描的值越多
- **THEN** actionable 的有效净 PnL 门槛 SHALL 相应提高（抵消多重比较）

### Requirement: 置信度三因子透明
系统 SHALL 从 baseline_fidelity、divergence_ratio、样本量三因子派生 confidence，并同时报出三原始因子，不藏进单一数字。

#### Scenario: 三因子随推荐报出
- **WHEN** 生成推荐
- **THEN** 输出 SHALL 含 confidence 与 `baseline_fidelity`/`divergence_ratio`/`sample` 三原始因子 + `fidelity_note`（继承 L3b 保真天花板）

### Requirement: 推荐 observability-only，绝不自动应用
系统 SHALL 保证推荐为离线建议，严禁被任何 gate/veto/halt/rank/daily-stop 读取，绝不自动改线上 config（人审）。

#### Scenario: 推荐不进决策、不自动应用
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其 SHALL NOT 读取推荐产物；推荐 SHALL NOT 自动应用到线上 config
```

## openspec/changes/perturbation-knob-sweep/specs/knob-sweep-engine/spec.md

- Source: openspec/changes/perturbation-knob-sweep/specs/knob-sweep-engine/spec.md
- Lines: 1-23
- SHA256: c605a4438735a17576e5827e92ef2edd21f678cdd06f6d48e913fc9ba2194214

```md
## ADDED Requirements

### Requirement: 单旋钮 grid 扫描
系统 SHALL 对一个旋钮的显式值列表逐值跑 L3b `build_delta_report`，聚合每值的 delta 与信任/样本元数据。

#### Scenario: 逐值跑 L3b
- **WHEN** 对 knob 的 values=[v1,v2,...] 扫描
- **THEN** 系统 SHALL 对每个 v 跑 `build_delta_report(records, baseline_config={}, perturbed_config={knob: v}, ...)`，收集 `{value, delta, baseline_fidelity, untrustworthy, divergence_ratio, sequence_len}`

#### Scenario: 复用 L3b 不重写
- **WHEN** 扫描执行
- **THEN** 其 SHALL 经 L3b `build_delta_report`（真实 Judge 序列重演），SHALL NOT 另写决策/PnL 逻辑

#### Scenario: 显式值列表
- **WHEN** 指定扫描值域
- **THEN** 系统 SHALL 接受显式值列表（非 range+step），允许非均匀值

### Requirement: 扫描 observability-only write-only
系统 SHALL 保证扫描引擎为离线工具，严禁被任何 gate/veto/halt/rank/daily-stop 读取，绝不自动改线上 config。

#### Scenario: 不进生产链路
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其 SHALL NOT import 或调用扫描引擎；扫描产物 SHALL NOT 自动应用到线上 config
```

