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
