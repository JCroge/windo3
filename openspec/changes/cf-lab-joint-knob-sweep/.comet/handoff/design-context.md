# Comet Design Handoff

- Change: cf-lab-joint-knob-sweep
- Phase: design
- Mode: compact
- Context hash: ef65b6a11cf99175b11ee353e4247f598e8acc8f3b1b9059879bf018df7b0461

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/cf-lab-joint-knob-sweep/proposal.md

- Source: openspec/changes/cf-lab-joint-knob-sweep/proposal.md
- Lines: 1-30
- SHA256: c73e752b5e9aaa42e17ce180c9023a1b277e4c2ac919aa50b3706bd2ca31a3be

```md
## Why

反事实实验室 L4 单旋钮扫描已收官，且给出**首个可信结论**：放宽 `rr_floor_default`（1.50→1.20）与 `min_confidence`（60→40）**各自** PnL delta≈0 → 非高价值杠杆（reject 被多 gate 过度决定，放宽单门只把决策级联到其它 gate 而非盈利开仓）。

但单旋钮扫描有一个结构性盲区：**它看不见旋钮之间的交互效应**。两个门各自不是瓶颈，不代表「同时放宽两个门」不是——若 `rr_floor` 与 `min_confidence` 在 reject 链路上**串联**（信号先过 confidence 门再过 R:R 门），单独放宽任一个都会被另一个继续拦截，唯有联合放宽才可能解锁开仓。单旋钮 delta≈0 既可能是「真没用」，也可能是「被另一个门掩盖」——单旋钮扫描**无法区分这两者**。

多旋钮联合扫描是区分二者的唯一手段。其科学价值不在「扫一个更大的网格找最优点」（背景下大概率仍 `no_actionable_direction`），而在**量化旋钮间的交互项**：`interaction(a,b) = delta(a,b) − delta(a,base) − delta(base,b)`。这是 2-way 因子设计的交互项，能给出单旋钮答不了的新信息：协同（联合解锁）/ 可加（确认独立无用）/ 拮抗（互相抵消）。

## What Changes

- 新增**通用 N 旋钮笛卡尔积扫描引擎**：复用 L3b `build_delta_report`（真实 Judge 序列重演），对多个旋钮的取值网格做笛卡尔积，每个组合作为一个多 key `perturbed_config` 跑一个 perturbed 臂。
- **baseline 臂只跑一次复用**：baseline_fidelity 是 baseline 臂相对录制的属性，对所有组合相同，不应每组合重算（笛卡尔积下省 N 倍 baseline 重算）。
- **核心产出：交互效应报告**——网格里纳入各旋钮 base 值作边缘参照，`(base,base)` 的 delta≈0 当自检锚点；对每个联合点算 `interaction = delta(a,b) − delta(a,base) − delta(base,b)`，判定协同/可加/拮抗。
- **辅助：多维孤峰守卫的方向推荐**——把单旋钮的一维「前后邻居连贯」守卫推广到网格上的「轴邻居连贯」（曼哈顿距离=1 的相邻点同向才算趋势），多重比较门槛随网格点数（∏ 各轴取值）收紧。
- 首发 driver 聚焦 `rr_floor_default × min_confidence` 两轴，接续已有的两个单旋钮结论。

## Capabilities

### New Capabilities
- `joint-knob-sweep`: 多旋钮笛卡尔积联合扫描 + 交互效应（可加性偏离）量化 + 多维孤峰守卫的方向推荐，复用 L3b `build_delta_report`，observability-only。

### Modified Capabilities
<!-- 无修改；与单旋钮 knob-sweep-engine / direction-recommender 并列，不改其语义 -->

## Impact

- 代码：新增 `utils/joint_knob_sweep.py`（笛卡尔积扫描 + 交互项计算 + 多维推荐）+ 对应测试；driver `cf_direction_recommendation.py` 增一段两轴联合扫描示例（或新 driver）。复用 `utils/sequential_perturbation.py::build_delta_report` / `run_arm`，**不改其逻辑**。
- 行为：纯离线分析工具；**不改 live Judge 决策逻辑、不改生产 config、不改 choppy R:R 地板 1.50、无需 event_backtest**。
- 红线：observability-only write-only；严禁任何 gate/veto/halt/rank/daily-stop import；推荐绝不自动改线上 config（人审）。红线守卫 `tests/test_cf_red_line_guard.py` 扩展覆盖新模块。
- 非目标：不引入 LLM 旋钮；不做 >2 轴的实战推荐（引擎通用支持 N 轴，但首发只验证 2 轴方法论）；不优化 `build_delta_report` 内部 PnL 估算保真（继承 L3b 天花板）；不自动应用任何结论到线上。
```

## openspec/changes/cf-lab-joint-knob-sweep/design.md

- Source: openspec/changes/cf-lab-joint-knob-sweep/design.md
- Lines: 1-68
- SHA256: dbc9407e8f5814d649ace29d670b7d2e6191d55a7bbd51da98a4e274db2da6c8

```md
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
```

## openspec/changes/cf-lab-joint-knob-sweep/tasks.md

- Source: openspec/changes/cf-lab-joint-knob-sweep/tasks.md
- Lines: 1-29
- SHA256: b67a3f8bde8108e9063fa00e3f20c14c7867af23ff8dd6b696c26d257519085a

```md
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
```

## openspec/changes/cf-lab-joint-knob-sweep/specs/joint-knob-sweep/spec.md

- Source: openspec/changes/cf-lab-joint-knob-sweep/specs/joint-knob-sweep/spec.md
- Lines: 1-68
- SHA256: 2d20241a53c83998e5b86a5a1ee014bcc83cc4b1d97183fe627fd1d28b51d000

```md
## ADDED Requirements

### Requirement: 多旋钮笛卡尔积联合扫描
系统 SHALL 对多个旋钮的显式取值网格做笛卡尔积，每个组合作为一个多 key `perturbed_config` 跑 L3b 序列重演，聚合每个组合的 delta 与信任/样本元数据。复用 L3b 引擎，绝不另写决策/PnL 逻辑。

#### Scenario: 笛卡尔积逐组合跑 L3b
- **WHEN** 对 `knob_grids = {k1: [v...], k2: [v...]}` 联合扫描
- **THEN** 系统 SHALL 对每个组合 `(v1, v2, ...)` 跑一个 perturbed 臂，扰动配置为多 key 字典 `{k1: v1, k2: v2, ...}`，收集 `{combo, delta, divergence_ratio}` 等

#### Scenario: 复用 L3b 真实 Judge 重演
- **WHEN** 任一组合执行
- **THEN** 其 perturbed 臂 SHALL 经 L3b `run_arm` / `build_delta_report` 同款真实 Judge 序列重演，SHALL NOT 另写决策或 PnL 逻辑

#### Scenario: 显式取值网格
- **WHEN** 指定各旋钮的扫描值域
- **THEN** 系统 SHALL 接受每个旋钮的显式值列表（非 range+step），允许非均匀值与任意旋钮数（N ≥ 2）

### Requirement: baseline 臂单次复用
系统 SHALL 把 baseline 臂跑一次并对全部组合复用，因 baseline_fidelity 是 baseline 臂相对录制的属性、对所有组合相同；笛卡尔积下 SHALL NOT 每组合重跑相同 baseline 臂。

#### Scenario: baseline 跑一次
- **WHEN** 联合扫描 M 个组合
- **THEN** 系统 SHALL 只跑 1 次 baseline 臂并复用其 fidelity 与 summary，仅 perturbed 臂跑 M 次

#### Scenario: 不可信则整体拒答
- **WHEN** 共享 baseline 臂 baseline_fidelity < 阈值（untrustworthy）
- **THEN** 系统 SHALL 标记整个联合扫描 untrustworthy 并拒答，SHALL NOT 输出可信交互结论

### Requirement: 交互效应量化（可加性偏离）
系统 SHALL 把各旋钮 base 值纳入网格作边缘参照，对每个联合点计算交互项 `interaction(a,b) = delta(a,b) − delta(a,base) − delta(base,b)`，判定旋钮间为协同/可加/拮抗，给出单旋钮扫描无法给出的交互信息。

#### Scenario: base 值纳入网格作边缘
- **WHEN** 构造扫描网格
- **THEN** 每个旋钮的取值列表 SHALL 含其生产 base 值，使 `(base, base)` 组合存在并作自检锚点（其 delta SHALL ≈ 0），边缘组合 `(a, base)` / `(base, b)` 提供纯单旋钮效果

#### Scenario: 计算交互项
- **WHEN** 对联合点 `(a, b)`（a、b 均非 base）求交互
- **THEN** 系统 SHALL 输出 `interaction = delta(a,b).net_pnl − delta(a,base).net_pnl − delta(base,b).net_pnl`，并按符号/量级判定协同(≫0)/可加(≈0)/拮抗(≪0)

#### Scenario: 自检锚点
- **WHEN** `(base, base)` 组合参与扫描
- **THEN** 其 delta SHALL ≈ 0（同 config 两臂）；显著非零 SHALL 标记引擎自检失败

#### Scenario: 显著性阈值复用诚实门控口径
- **WHEN** 判定交互项为协同/可加/拮抗
- **THEN** 系统 SHALL 复用方向推荐器同款绝对阈值 `actionable_min_pnl × (1 + value_penalty_k × M)`（M = 网格组合总数），随网格点数收紧抵消多重比较：`|interaction| ≤ 阈值` → 可加（确认旋钮独立）；`> 阈值` 且正 → 协同；`< −阈值` → 拮抗。阈值口径 SHALL 与 actionable 推荐门控一致，不另立标准

### Requirement: 多维孤峰守卫的方向推荐
系统 SHALL 把单旋钮一维连贯守卫推广到网格：最优组合须在网格上沿轴邻居连贯（曼哈顿距离=1 的相邻点同向）才推荐，否则标 isolated_spike 拒答；actionable 门槛随网格点数收紧以抵消多重比较；证据不足绝不杜撰方向。

#### Scenario: 轴邻居连贯才推荐
- **WHEN** 最优 trustworthy 组合在网格中
- **THEN** 系统 SHALL 检查其沿每个轴 ±1 step 的轴邻居，至少一个同向（delta ≥ best × coherence_frac）才视为连贯趋势；否则标 `isolated_spike` 并 SHALL NOT 推荐

#### Scenario: 门槛随网格点数收紧
- **WHEN** 网格组合数越多（∏ 各轴取值）
- **THEN** actionable 有效净 PnL 门槛 SHALL 相应提高（比单旋钮值数收得更紧），抵消多重比较假阳性

#### Scenario: 报出全貌
- **WHEN** 生成推荐
- **THEN** 输出 SHALL 含全部组合的 delta + 交互矩阵 + 信任元数据 + `fidelity_note`，供人看趋势而非只看赢家

### Requirement: 联合扫描 observability-only，绝不自动应用
系统 SHALL 保证联合扫描与交互报告为离线工具，严禁被任何 gate/veto/halt/rank/daily-stop import 或读取，推荐绝不自动改线上 config。

#### Scenario: 不进生产链路
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其 SHALL NOT import 或调用联合扫描引擎；扫描/交互/推荐产物 SHALL NOT 自动应用到线上 config（人审）
```

