# Comet Design Handoff

- Change: fix-cf-lab-replay-config-parity
- Phase: design
- Mode: compact
- Context hash: 79470090e2eff5069353ef3804bb54bc46384b4f58c693608f4a97371bd5eddd

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/fix-cf-lab-replay-config-parity/proposal.md

- Source: openspec/changes/fix-cf-lab-replay-config-parity/proposal.md
- Lines: 1-32
- SHA256: 0494767b1d2ca8d5c081c48d625ff158d184905a741e38cd2b0de59b58e3a3ab

```md
## Why

反事实策略实验室 L3b 的 gate-level `baseline_fidelity` 仅 **0.34**（< 0.8 阈值）→ `untrustworthy=True` 拒答，实验室给不出可信方向。逐条转移矩阵 + 逐字坐实定位到真根因（**非**原假设的分桶 EV / archetype 状态重建）：

**replay/CF-sim baseline 跑的 config 与 live 生产不一致。** `replay_decision(record, None)` / `build_delta_report(baseline_config={})` 经 `utils/decision_replay.py::_install_config_flags` 把四个 Phase-2 flag（`phase2_signal_confidence_split_enabled` / `phase2_momentum_probe_long_enabled` / `phase2_trend_saturation_enabled` / `phase2_bucketed_ev_enabled`）默认 **False**；而 live 生产 `utils/config_loader.py::DEFAULTS`(166-169) 这四个都是 **True**。config 不一致使 replay 的 confidence 路径走错分支（`judge.py:1283 max(40, conf*0.7)` → confidence=40 → quality_gate），live 则走 htf-aligned 保值分支（`judge.py:1281 max(60, ...)`）→ rr_below_floor。

**证据**：全量 660 条 v2 磁带，`config={}` L2 fidelity = **0.3652**；`config = config_loader.DEFAULTS` L2 fidelity = **0.9015**（> 0.8）。逐条 7/8 rr_below_floor 样本 phase2-on 后从 quality_gate 翻回 rr_below_floor。

> **已证伪（勿写进 scope）**：根因不是 CF state injection——纯 L2 录制快照 replay（零 CF 注入）同样 0.365；也不是分桶 EV / archetype 主因（那只是 production-config 下剩余 ~10% 的二级残差）。

## What Changes

- **replay/CF-sim baseline 使用 live 生产 config**：`build_delta_report` / `run_arm` 的 baseline 臂、`sweep_knob`、`cf_direction_recommendation.py` 驱动，以及 `replay_decision` 的有效 config，须以 `config_loader` 生产默认（或决策时录制的 resolved config）为基线，**perturbation 叠加其上**而非叠在 `{}` 上。
- **诚实性**：修复后 baseline_fidelity 应 ≥0.9（坐实），untrustworthy 解除，实验室对单旋钮可给方向或可信的 no_actionable_direction。
- 全程 **observability-only / write-only**，红线守卫（`tests/test_cf_red_line_guard.py`）维持。

## Capabilities

### New Capabilities
<!-- 无新增；修正既有 L2/L3b 能力的 config 基线行为 -->

### Modified Capabilities
- `deterministic-replay-harness`: 回放 harness 的有效决策 config 须与 live 生产默认一致（Phase-2 flag 等），不得默认 False 致 confidence 路径发散；perturbation 是对生产基线的覆盖。
- `sequential-perturbation-driver`: `run_arm`/`build_delta_report` 的 baseline 臂须用生产 config 基线，perturbed 臂 = 生产基线 + 扰动覆盖。
- `replay-report-driver`: 报告/方向驱动须以生产 config 为 baseline 喂入回放。

## Impact

- 代码：`utils/sequential_perturbation.py`、`utils/knob_sweep.py`、`cf_direction_recommendation.py`，可能 `utils/decision_replay.py`（config 基线注入点），及对应测试。
- 行为：仅提升反事实实验室回放保真；**不改 live Judge 决策逻辑、不改 live 生产 config、不改 choppy R:R 地板 1.50、无需 event_backtest**。
- 非目标：不追 production-config 下剩余 ~10% 残差（`ev_gate→15m_blocked` 36 / `ev_gate→accept` 27，二级状态重建差异，留后续 change）。
- 红线：CLAUDE.md 反事实回放产物 observability-only write-only 约束不变。
```

## openspec/changes/fix-cf-lab-replay-config-parity/design.md

- Source: openspec/changes/fix-cf-lab-replay-config-parity/design.md
- Lines: 1-35
- SHA256: 33e42ee0f828e6e3b561d55271e412c42424e3db9f7b26b5e5f334185a799cd1

```md
# Design (high-level) — fix-cf-lab-replay-config-parity

> OpenSpec 高层草图。详细 RFC + 方案权衡定在 comet-design 的 Superpowers Design Doc。

## 问题边界（已坐实）

```
live 生产 Judge: config_loader.DEFAULTS → 四个 phase2 flag = True
        │
        ▼  录制决策 (rr_below_floor:1.41, confidence≥60 走 htf-aligned 保值分支 judge.py:1281)

replay/CF-sim baseline: config={} → _install_config_flags 默认 phase2 flag = False
        │
        ▼  confidence 走 judge.py:1283 max(40,conf*0.7)=40 → quality_gate  ❌ 发散
```

全量 660 条：config={} → fidelity 0.365；config=DEFAULTS → fidelity **0.902**。

## 待修点

- replay/CF-sim 的有效 config 基线：`build_delta_report`/`run_arm` baseline 臂、`sweep_knob`、`cf_direction_recommendation.py` 驱动。perturbation 叠加在生产基线上（不是叠在 `{}` 上）。

## 候选方案（comet-design 定夺）

- **A. 用 `config_loader` 生产默认作基线**：driver/build_delta_report 以 `config_loader.DEFAULTS`（或 `get_config()`）为 baseline_config，perturbation dict 覆盖其上。简单、立即生效（坐实 0.90）。风险：不含 env override（若 live 用了非默认 rr floor 等，仍有小差）。
- **B. 录制时把 resolved config 存进决策磁带**：replay 用录制 config（最忠实，含 env override；rr 地板 1.50 本身是 config 值也被覆盖）。需 tape schema 加字段 + 累积，重。
- **C. 折中**：A 立即落地；磁带加 resolved-config 字段为 B 铺路（旧记录 fallback 到 DEFAULTS）。

权衡点：perturbation 语义——扰动 dict 必须只覆盖目标旋钮，不能把其它旋钮重置回 default 之外的值；两臂都以同一生产基线起步，delta 才干净。

## 不变量 / 红线

- observability-only write-only：禁交易决策路径读 CF 产物（守卫不放松）。
- 不改 live Judge 决策逻辑、不改 live 生产 config、不改 choppy 地板 1.50、无需 event_backtest。
- 非目标：production-config 下剩余 ~10% 残差（ev_gate→15m_blocked/accept）留后续 change。
```

## openspec/changes/fix-cf-lab-replay-config-parity/tasks.md

- Source: openspec/changes/fix-cf-lab-replay-config-parity/tasks.md
- Lines: 1-22
- SHA256: 0a3e65d08af0470c222fe7ae13d2fe211441dee332af06cc2c8f8d887f8bfff9

```md
# Tasks — fix-cf-lab-replay-config-parity

> 骨架，comet-design 定方案后细化。

## 设计（comet-design）
- [ ] brainstorm 选定 config 基线方案（A DEFAULTS / B 录制 resolved config / C 折中）
- [ ] 确认 perturbation 叠加语义（只覆盖目标旋钮，两臂同生产基线起步）
- [ ] 产出 Design Doc + delta spec（deterministic-replay-harness / sequential-perturbation-driver / replay-report-driver）

## 实现
- [ ] replay/CF-sim baseline 用生产 config（build_delta_report/run_arm baseline 臂）
- [ ] sweep_knob + cf_direction_recommendation 驱动以生产 config 为基线，perturbation 覆盖其上
- [ ] （按方案）decision_replay 默认 config 注入点对齐生产

## 测试
- [ ] 全量 v2 磁带 L2 fidelity 用生产 config ≥ 0.85（坐实 0.90，区别于 config={} 的 0.365）
- [ ] perturbation 叠加正确：扰动单旋钮不重置其它旋钮（造 fixture 验证）
- [ ] 红线守卫 `tests/test_cf_red_line_guard.py` 维持
- [ ] 全量 pytest 回归（基线 1247 不回退）

## 验收
- [ ] 重跑 cf_direction_recommendation.py：baseline_fidelity 从 0.34 升至 ≥0.85，untrustworthy 解除，能给出方向或可信 no_actionable_direction（非 untrustworthy 拒答）
```

## openspec/changes/fix-cf-lab-replay-config-parity/specs/decision-replay-tape/spec.md

- Source: openspec/changes/fix-cf-lab-replay-config-parity/specs/decision-replay-tape/spec.md
- Lines: 1-16
- SHA256: 00ed47a6a261299c5a57309839797a5fbbf7df1beec7b9ec8758baf6bafc3460

```md
## ADDED Requirements

### Requirement: 决策磁带录制 resolved config 快照
决策磁带 SHALL 在录制每条决策时附带该决策实际运行的 config 快照（`config_snapshot`），覆盖回放 harness 消费的 config key 白名单（`_install_config_flags` 读取的 ~57 旋钮 + 四个 Phase-2 flag），使回放能用与 live 决策时一致的 config，防 config 漂移后回放发散。

#### Scenario: build_bundle 录 config_snapshot
- **WHEN** Judge 在 accept/reject chokepoint 录决策磁带
- **THEN** `build_bundle` SHALL 写入 `config_snapshot` = 决策时 Judge resolved config 的白名单子集，`SCHEMA_VERSION` 升至 v3

#### Scenario: config_snapshot 是 write-only observability
- **WHEN** Judge 写 `config_snapshot`
- **THEN** 其 SHALL 只读 Judge 自身 config 写入磁带（与 `state_snapshot` 同性质），SHALL NOT 引入任何决策路径对回放产物的读取

#### Scenario: 旧记录无 config_snapshot 向后兼容
- **WHEN** 回放遇到无 `config_snapshot` 字段的旧版本记录
- **THEN** 系统 SHALL fallback 到生产基线 config，不得报错或丢弃该记录
```

## openspec/changes/fix-cf-lab-replay-config-parity/specs/deterministic-replay-harness/spec.md

- Source: openspec/changes/fix-cf-lab-replay-config-parity/specs/deterministic-replay-harness/spec.md
- Lines: 1-16
- SHA256: 23938e83a3f01644dab9e12065f6fb3e1187b3ee62809f7ee5b10e7291aa8c42

```md
## ADDED Requirements

### Requirement: 回放有效 config 与 live 生产一致
回放 harness 的有效决策 config SHALL 与录制该决策时的 live 生产 config 一致，不得用空 config 致 `_install_config_flags` 把 Phase-2 等 flag 默认到与生产相反的值，从而使 confidence/gate 路径系统性发散。

#### Scenario: 优先用录制 config_snapshot
- **WHEN** 回放一条带 `config_snapshot` 的记录
- **THEN** harness SHALL 用该 `config_snapshot` 作为 baseline 有效 config

#### Scenario: 旧记录用生产基线 fallback
- **WHEN** 回放一条无 `config_snapshot` 的记录
- **THEN** harness SHALL 用 `production_base_config()`（取自 `config_loader` 生产解析值，含 Phase-2 flag=True）作 baseline，SHALL NOT 用空 config 默认值

#### Scenario: 生产基线显著恢复保真
- **WHEN** 用生产基线 config 对全量真实磁带跑零扰动 baseline 回放
- **THEN** gate-level baseline_fidelity SHALL 显著高于空 config（实测 0.365 → ~0.90），跨过可信阈值
```

## openspec/changes/fix-cf-lab-replay-config-parity/specs/replay-report-driver/spec.md

- Source: openspec/changes/fix-cf-lab-replay-config-parity/specs/replay-report-driver/spec.md
- Lines: 1-12
- SHA256: ec9ab0db2d0b38617cf41f8c8ebe7ec8c500b44357d296de18d38f452db22433

```md
## ADDED Requirements

### Requirement: 报告/方向驱动以生产 config 为回放基线
报告与方向推荐驱动（`cf_direction_recommendation.py` / `sweep_knob`）SHALL 以 per-record 有效生产 config 为回放基线喂入回放，SHALL NOT 用空 config，避免 baseline_fidelity 因 config 不一致虚低而误判 untrustworthy。

#### Scenario: 驱动用生产基线
- **WHEN** 驱动跑 L2 终验 / L4 扫描
- **THEN** baseline 臂 SHALL 用 per-record 有效生产 config（`config_snapshot` 或 `production_base_config()` fallback），扫描各值在该基线上覆盖目标旋钮

#### Scenario: 修复后可信度恢复
- **WHEN** 修复后重跑 `cf_direction_recommendation.py`
- **THEN** baseline_fidelity SHALL 跨过阈值（untrustworthy 解除），驱动可给出方向或可信的 no_actionable_direction，区别于此前因 config 不一致的 untrustworthy 拒答
```

## openspec/changes/fix-cf-lab-replay-config-parity/specs/sequential-perturbation-driver/spec.md

- Source: openspec/changes/fix-cf-lab-replay-config-parity/specs/sequential-perturbation-driver/spec.md
- Lines: 1-16
- SHA256: ddeeb1ad480ed2b8009d3c04676012ab78b858018587d422eb51d7b2c322bc18

```md
## ADDED Requirements

### Requirement: 两臂以生产 config 基线起步，扰动只覆盖目标旋钮
`build_delta_report`/`run_arm` 的 baseline 臂与 perturbed 臂 SHALL 以 per-record 有效生产 config（`config_snapshot` 或 `production_base_config()` fallback）为基线；perturbed 臂 = 该基线 + 扰动覆盖，扰动 SHALL 只覆盖目标旋钮，SHALL NOT 把其它旋钮重置出生产基线。

#### Scenario: baseline 臂用生产基线
- **WHEN** `run_arm` 以 `config={}`（baseline 臂）运行
- **THEN** 系统 SHALL 把空扰动解释为「生产基线，无覆盖」，即用 per-record 有效生产 config，而非 `_install_config_flags` 的硬默认

#### Scenario: 扰动叠加只覆盖目标旋钮
- **WHEN** perturbed 臂用扰动 `{rr_floor_default: 0.3}` 运行
- **THEN** 其有效 config SHALL 等于生产基线仅把 `rr_floor_default` 覆盖为 0.3，其它旋钮（含 Phase-2 flag）保持生产基线值

#### Scenario: 两臂同基线使 delta 干净
- **WHEN** baseline 与 perturbed 臂跑同一序列
- **THEN** 两臂 SHALL 从同一 per-record 生产基线起步，差异仅来自扰动旋钮，使 delta 不含 config 基线偏差
```

