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
