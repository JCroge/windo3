# Comet Design Handoff

- Change: ev-decouple-forward-ab
- Phase: design
- Mode: compact
- Context hash: 8736f74950f3fff5202591b39a9e53abf5bdfe7d035448cfc08d0065184a4dc6

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/ev-decouple-forward-ab/proposal.md

- Source: openspec/changes/ev-decouple-forward-ab/proposal.md
- Lines: 1-34
- SHA256: 2e67d019abd415540e911c3acb31cc7da71e5927ec78e1e0c2e548a370123d84

```md
## Why

`ev-gate-winrate-decouple`（2026-06-18 上线）把开仓 EV 门的胜率因子剔除：`ev_winrate_gate_enabled=false` 时 `_get_p_win` 返回固定 0.55、跳过胜率<40% 硬阈值，只保留经济门。复盘最近 8 笔开仓发现它们**全是 neutral 趋势 + 勉强压地板 R:R~1.5 的边缘单**，`p_win=0.55 fixed` 放行，旧胜率门（真实胜率 21%<40%）本会 EV 拒；放行后实盘**净亏 ~−16U/8 笔**（赢小 +0.2/+0.9、亏大 −4.4/−10）。

端到端验证（磁带 gate-toggle 复盘）证实这不是边缘现象：**64 条 replayable accept 中，baseline 自检忠实 52 条，其中 36 条（69%）是"解耦放行"——旧胜率门会以 `ev_gate` 拒**。即近期大多数开仓只因解耦才过门。需量化这批解耦放行单的前向期望，决定是否回滚或加约束（如 neutral 趋势不享解耦）。

本 change **只量化、不改 live**；证据足够再另起 change 决定回滚/约束。

## What Changes

- 新增 observability-only 离线驱动 `cf_ev_decouple_ab.py`（镜像 `cf_lever2_rejected_ab.py`），对决策磁带的 accept 流做 gate-toggle 两臂复盘 + 前向结算：
  - **baseline 自检臂** `replay(ev_winrate_gate_enabled=False)`（= live 现配置）必须复现 live accept，否则该条复盘失真、排除（复用 `fix-shadow-logger-replay-baseline-parity` 的 baseline 自检思想）。
  - **反事实臂** `replay(ev_winrate_gate_enabled=True)`（= 旧胜率门）翻成 reject(ev_gate) = "解耦放行"。
  - 解耦放行簇 vs 双门皆过簇，各用 `resolve_counterfactual`+klines **统一 CF 结算**（TP1 保守口径、含亏单），系统性偏差在两桶 delta 抵消。
  - real PnL（实际开仓 ~8 笔，经 symbol+ts 模糊 join lifecycle）作**次要 sanity 交叉验证**。
  - 簇去重后经 `cf_honesty_gate` 诚实门，薄样本拒答。
- 输出报表：解耦放行簇数 / 前向净 R / vs 双门皆过基线 / coverage 受限说明。

## Capabilities

### New Capabilities

- `ev-decouple-forward-ab`: observability-only 量化"胜率解耦放行单"前向期望的离线驱动（gate-toggle 两臂复盘 + baseline 自检 + CF 结算 + 诚实门）。

### Modified Capabilities

（无——不改 `open-gate-ev` 门逻辑、不改 live）

## Impact

- 新增 `cf_ev_decouple_ab.py`（repo 根，与 cf_lever2_rejected_ab.py / cf_shadow_lever1_compare.py 同级）。
- 复用：`utils/decision_replay.py::replay_decision`（gate-toggle 经 perturbation override）、`utils/counterfactual_pnl.py::resolve_counterfactual`、`utils/cf_honesty_gate.py::summarize_bucket`、`data/decision_replay_tape.jsonl` / `data/klines*.db` / `data/live_position_lifecycle.json`。
- 红线：observability-only write-only——输出严禁任何交易决策/风控路径消费、绝不自动改线上 config、绝不下单。
- 无 live 行为改动、无库机制改动（纯新驱动 + 测试）。
```

## openspec/changes/ev-decouple-forward-ab/design.md

- Source: openspec/changes/ev-decouple-forward-ab/design.md
- Lines: 1-40
- SHA256: b4d2082b5c578e30fe0959a0c3f7e3f7f5566ad87aae008784a96ed8ad009302

```md
## 高层架构决策（深度技术设计见 comet-design 的 Superpowers Design Doc）

### 测量方法学

```
对磁带每条 accept 决策(replayable):
  ① baseline 自检臂  replay(ev_winrate_gate_enabled=False)  # = live 现配置
       └─ 复现 live accept? 否 → 复盘失真, 排除
  ② 反事实臂        replay(ev_winrate_gate_enabled=True)   # = 旧胜率门
       └─ 翻 reject(ev_gate)? 是 → "解耦放行" ; 否 → "双门皆过"
  ③ 簇去重(同 symbol 连续重复评估归一簇, 同 cf_lever2_rejected_ab)
  ④ 两桶各 resolve_counterfactual+klines 统一 CF 结算(TP1保守含亏单) → 净R
  ⑤ cf_honesty_gate 诚实门: 薄样本拒答
  ⑥ real PnL(实际开仓~8, symbol+ts 模糊 join lifecycle) 作次要 sanity 交叉
```

### 关键决策

1. **CF 结算为主、real PnL 为辅**（用户已确认）：解耦放行单虽真实开了仓，但 lifecycle 无 request_id（只能 symbol+ts 模糊 join）、样本仅 ~8、含 pending external_close。统一 CF 口径结算两桶使系统性偏差在 delta 抵消（同 `cf_lever2_rejected_ab` / `sequential_perturbation` 两臂同估算原则），N 更大、口径一致。real PnL 作 sanity 交叉锚，不作主判据。

2. **baseline 自检闸不可省**：端到端验证显示 64 accept 中 12 条（~23%）复盘失真（与影子记录器诊断的失真率一致）。不自检则解耦放行分类被复盘失真污染。复用 `fix-shadow-logger-replay-baseline-parity` 刚确立的 baseline 二元 accept/reject 自检。

3. **对比桶 = 双门皆过**：解耦放行桶（gate-on→reject）vs 双门皆过桶（gate-on 仍 accept）。两桶净 R 的 delta 才是"解耦特有边缘单"的增量效应；绝对值受 CF 口径限制不单独采信。

4. **纯新驱动、零库改动**：复用 replay_decision（gate toggle 经 perturbation override，`_EPOCH_FALLBACK` 已含 ev_winrate_gate_enabled）+ resolve_counterfactual + cf_honesty_gate。无新机制、无 live 改动。

### 端到端可行性（explore 已实测）

`replay(accept, {ev_winrate_gate_enabled:True/False})` 跑通：64 accept → 52 忠实 / 12 失真，其中 **36 解耦放行（全 ev_gate）**。方法可行、population 非平凡。

### 红线（不变）

- observability-only write-only：输出严禁交易决策/风控路径消费、绝不下单、绝不自动改 config、绝不 mutate live。
- 复盘臂复用 replay_decision 隔离机器（publish 绝不进真实 bus）。

### 非目标

- 不改 `open-gate-ev` 门逻辑、不回滚/约束解耦（证据足够后另起 change）。
- 不追求 real PnL 精确归因（无 request_id，模糊 join 仅作 sanity）。
- 不解决 klines coverage 受限（如实报跳过数，不外推）。
```

## openspec/changes/ev-decouple-forward-ab/tasks.md

- Source: openspec/changes/ev-decouple-forward-ab/tasks.md
- Lines: 1-35
- SHA256: e4b2885bec9e82ff7ce86aa7a12e6947ae3a174693ca7985e49c5a325c6fa831

```md
## 1. 分类：gate-toggle 两臂复盘 + baseline 自检

- [ ] 1.1 `cf_ev_decouple_ab.py` 读 `data/decision_replay_tape.jsonl`，筛 `decision=accept` 且 `replayable`
- [ ] 1.2 baseline 臂 `replay(record, {ev_winrate_gate_enabled:False})` 复现 live accept 自检，失真排除（复用 `_is_accept` 二元判定）
- [ ] 1.3 反事实臂 `replay(record, {ev_winrate_gate_enabled:True})` 翻 reject(ev_gate) → 归 "解耦放行"，否则 "双门皆过"
- [ ] 1.4 报失真排除条数（透明）

## 2. 簇去重 + 前向 CF 结算

- [ ] 2.1 两桶各按 symbol+连续重复评估归一信号簇（同 `cf_lever2_rejected_ab` 簇逻辑）
- [ ] 2.2 每簇代表用 `resolve_counterfactual`+`load_bars`(klines_1s→klines fallback) 结算前向 outcome（TP1 保守口径含亏单）
- [ ] 2.3 算两桶净 R + delta；klines 无覆盖簇跳过并计数

## 3. 诚实门 + real PnL 交叉

- [ ] 3.1 去重簇数经 `cf_honesty_gate.summarize_bucket` 诚实门，薄样本拒答
- [ ] 3.2 解耦放行实际开仓单经 symbol+ts 模糊 join `live_position_lifecycle.json` 取真实 PnL 作次要 sanity 交叉，标注模糊 join/无 request_id、pending 不计

## 4. 报表

- [ ] 4.1 输出：忠实/失真数、解耦放行簇数/双门皆过簇数、两桶净 R + delta、可结算/跳过簇数、real PnL 交叉、诚实门结论
- [ ] 4.2 报表显式判据：解耦放行净 R << 双门皆过且 <0 → 提示解耦放行亏损单（非自动执行）

## 5. 测试

- [ ] 5.1 单测：gate-toggle 分类（构造 accept 记录，gate-on→reject 归解耦放行 / gate-on→accept 归双门皆过）
- [ ] 5.2 单测：baseline 自检失真排除（baseline 臂复盘≠live accept → 排除）
- [ ] 5.3 单测：薄样本诚实门拒答
- [ ] 5.4 红线守卫 `tests/test_cf_red_line_guard.py` 扩展：决策/风控路径禁 import/读 `cf_ev_decouple_ab` 产物
- [ ] 5.5 main() 登记新用例，全量回归零退化

## 6. 真跑 + 文档

- [ ] 6.1 真跑 `python3 cf_ev_decouple_ab.py`，记录结论（解耦放行前向期望 vs 双门皆过，诚实门是否拒答）入验证报告
- [ ] 6.2 comet-design 产出 Superpowers Design Doc
```

## openspec/changes/ev-decouple-forward-ab/specs/ev-decouple-forward-ab/spec.md

- Source: openspec/changes/ev-decouple-forward-ab/specs/ev-decouple-forward-ab/spec.md
- Lines: 1-52
- SHA256: 7ae586e6a075953d3b6c33e16a093e8d8a95b2621eb841cfaec6fd44ad7ff252

```md
## ADDED Requirements

### Requirement: 解耦放行单分类（gate-toggle 两臂复盘 + baseline 自检）

驱动 SHALL 对决策磁带的每条 `decision=accept` 且 `replayable` 记录跑两条复盘臂——**baseline 臂** `replay(ev_winrate_gate_enabled=False)`（= live 现配置）与**反事实臂** `replay(ev_winrate_gate_enabled=True)`（= 06-18 前旧胜率门）。baseline 臂复盘出的 accept/reject MUST 复现 live record 的 accept（二元类别），否则该条标复盘失真并排除出统计；反事实臂翻成 reject 的记录归类为 **"解耦放行"**（旧胜率门会拒、解耦后才过）。复盘 MUST 复用 `utils/decision_replay.py::replay_decision`（gate 经 perturbation override 切换），MUST NOT 重写门逻辑。

#### Scenario: baseline 自检忠实 → 可分类

- **WHEN** baseline 臂 `replay(ev_winrate_gate_enabled=False)` 复盘出 accept、与 live record 一致
- **THEN** 该条进入分类；反事实臂 reject 则归 "解耦放行"，accept 则归 "双门皆过"

#### Scenario: baseline 自检失真 → 排除

- **WHEN** baseline 臂复盘背离 live record 的 accept/reject 类别（复盘失真）
- **THEN** 该条排除出统计，报表报出失真排除条数（透明）

### Requirement: 前向结算与桶对比（CF 为主、real PnL 交叉）

驱动 SHALL 对 "解耦放行" 与 "双门皆过" 两桶各用 `resolve_counterfactual`+klines **统一 CF 口径**（TP1 保守、含亏单：tp→+tp1_dist/sl_dist、sl→−1、expired→0）结算前向净 R，两桶同口径使系统性 CF 偏差在 delta 抵消。对实际开仓的解耦放行单（经 symbol+ts 模糊 join `live_position_lifecycle.json`）SHALL 报出真实已实现 PnL 作**次要 sanity 交叉**，并标注 join 为模糊匹配、无 request_id。

#### Scenario: 两桶净 R 对比

- **WHEN** 两桶均结算完成
- **THEN** 报出解耦放行桶净 R、双门皆过桶净 R 及其 delta；解耦放行桶净 R 显著低于双门皆过且为负 → 提示解耦在放行亏损单（结论性判据，非自动执行）

#### Scenario: real PnL 交叉验证

- **WHEN** 解耦放行单中有实际开仓且 lifecycle 有已实现 PnL
- **THEN** 报出其真实净 PnL 与 CF 估算对照；join 为 symbol+ts 模糊（无 request_id）须显式标注，pending/external_close 不强行计入

### Requirement: 诚实门与 coverage 透明

驱动 SHALL 对结算结果按信号簇去重（同 symbol 连续重复评估归一簇，同 `cf_lever2_rejected_ab` 做法），并经 `utils/cf_honesty_gate.py::summarize_bucket` 诚实门——薄样本（簇数低于阈值）MUST 拒答而非给结论。klines 覆盖受限（`klines_1s` 仅近 ~数日 ~24 标的）导致无法结算的簇 MUST 跳过并如实报出跳过数。

#### Scenario: 薄样本拒答

- **WHEN** 去重后可结算簇数低于诚实门阈值
- **THEN** 驱动输出 "样本不足、拒答"，不给净 R 结论

#### Scenario: coverage 受限透明

- **WHEN** 部分解耦放行单因 klines 无覆盖无法结算
- **THEN** 报表报出可结算簇数 / 跳过数，不把跳过当作零影响

### Requirement: observability-only write-only 红线

驱动与其输出 SHALL 是 observability-only write-only：MUST NOT 被任何交易决策/风控路径 import 或读取，MUST NOT 下单，MUST NOT 自动修改线上 config（`ev_winrate_gate_enabled` 等），MUST NOT mutate 任何 live 状态。复盘臂复用 `replay_decision` 隔离机器（mock 外部 await、捕获 publish 绝不进真实 bus）。

#### Scenario: 不碰 live

- **WHEN** 驱动运行
- **THEN** 只读磁带/klines/lifecycle + 写报表（stdout 或独立文件），不发任何真实 bus 消息、不下单、不改 config
```

