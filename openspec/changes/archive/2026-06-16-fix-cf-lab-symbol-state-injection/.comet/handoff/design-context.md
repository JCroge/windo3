# Comet Design Handoff

- Change: fix-cf-lab-symbol-state-injection
- Phase: design
- Mode: compact
- Context hash: 6749e3e28184e83f3194949743b6e41550c16a156cf94280ae10c5e205f99bd0

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/fix-cf-lab-symbol-state-injection/proposal.md

- Source: openspec/changes/fix-cf-lab-symbol-state-injection/proposal.md
- Lines: 1-28
- SHA256: d317b466c9bd0d71b016fb188fba9af0acec66a05138bba29ba6922fb778dad2

```md
## Why

反事实实验室 L3b **sequential 臂** baseline_fidelity = **0.798**（< 0.8）→ untrustworthy，驱动 `cf_direction_recommendation.py` 仍给不出可信方向；而**直接 L2**（录制快照）已 **0.914**。差距 100% 坐实在一处：

`utils/sequential_perturbation.py::_inject_cf_state` 用 `cf.to_snapshot()` 替换录制 `state_snapshot`，其中 **`_symbol_state` 被清空为 `{}`**。但 `_symbol_state` 含 per-symbol **决策输入上下文** `{last_decision_time, last_tech, last_force_close_time, last_open_time, trend_streak}`；Judge 信号强度路径读 `trend_streak`/`last_tech` → 空 `{}` 致 **"信号强度不足"→ hold_other**，而非录制的 `rr_below_floor`。

**证据**：全量 779 条 v2/v3，L2 fidelity 0.911 vs sequential 0.798；残差 **90 条**全是「L2 复现但 inject 没复现」，其中 86 `rr_below_floor→hold_other`；override 还原 `_symbol_state` 后 **90/90（100%）复现**。其它字段（`_available_balance`/`_archetype_cooldown`/`_open_positions`/`_recent_win_rate`）override 均不复现 → **非根因**（已证伪）。config-parity 已在上个 change 修。

## What Changes

- `_inject_cf_state` 的 `_symbol_state` **基于录制快照**：还原 CF 无法重建的市场决策输入字段（`last_tech`/`trend_streak`/`last_decision_time`），使 baseline 臂忠实复现录制 gate。
- CF 只 **overlay 它自己开过仓的 position-outcome 字段**（`last_open_time`/`last_force_close_time`），以保 perturbed 臂级联真实；baseline 臂（cf_open=0）无 overlay → 录制值原样 → 100% 复现。
- 目标：sequential baseline_fidelity 0.798 → ≥0.85（坐实 ~0.91），untrustworthy 解除，实验室端到端可信。

## Capabilities

### New Capabilities
<!-- 无新增 -->

### Modified Capabilities
- `sequential-perturbation-driver`: `_inject_cf_state` 注入的 `_symbol_state` 须保留录制的 per-symbol 决策输入上下文（市场/信号连续性字段），不得清空致信号强度路径发散；仅 CF 自身开仓影响的 position-outcome 字段由 CF overlay。

## Impact

- 代码：`utils/sequential_perturbation.py`（`_inject_cf_state`），可能 `utils/cf_portfolio.py::to_snapshot`（若 `_symbol_state` 由 to_snapshot 产出空），及对应测试。
- 行为：仅提升 L3b sequential 臂回放保真；**不改 live Judge 决策逻辑、不改 live 生产 config、不改 choppy R:R 地板 1.50、无需 event_backtest**。
- 红线：observability-only write-only 不变；还原的是**市场决策输入**（非 reality 的 EV/胜率交易结果累计），不触 L3b "绝不 per-record 注入 reality 演化计数" 反模式。
- 非目标：不动决策输入以外的 CF 状态语义；不追已 0.91 的直接 L2。
```

## openspec/changes/fix-cf-lab-symbol-state-injection/design.md

- Source: openspec/changes/fix-cf-lab-symbol-state-injection/design.md
- Lines: 1-34
- SHA256: 9657e231a692f162d7edb214a68e09289925bab12a6e10d96f6ae6b70e356a54

```md
# Design (high-level) — fix-cf-lab-symbol-state-injection

> OpenSpec 高层草图；详细 RFC + 方案权衡定在 comet-design 的 Superpowers Design Doc。

## 问题边界（100% 坐实）

```
record(录制 _symbol_state = {last_tech, trend_streak, last_open_time, ...})
  │  直接 L2 replay(录制快照)        → rr_below_floor   ✅ 0.914
  │
  └─ run_arm: _inject_cf_state → cf.to_snapshot()._symbol_state = {}（清空）
        │
        ▼  replay → 信号强度路径读不到 trend_streak/last_tech → "信号强度不足" → hold_other  ❌ 0.798
```
override 还原 `_symbol_state` → 90/90 残差全复现。

## 字段分类（决定 overlay 边界）

| 字段 | 性质 | 处理 |
|---|---|---|
| `last_tech` / `trend_streak` / `last_decision_time` | 市场决策输入(CF 无法重建) | **还原录制值** |
| `last_open_time` / `last_force_close_time` | position-outcome(依赖 CF 自身开仓) | baseline 用录制值；perturbed 臂 CF 开过该 symbol 则 overlay CF 值 |

## 候选方案（comet-design 定夺）

- **A. `_inject_cf_state` 以录制 `_symbol_state` 为基**，CF 仅对自己 `_open` 里的 symbol overlay position-outcome 字段。最小、直接、baseline 100% 复现，perturbed 级联保留。
- **B. `cf.to_snapshot` 接收录制 `_symbol_state` 作种**，内部合并 CF position 事件。封装在 CF 侧，但 to_snapshot 需新增入参。
- 共同约束：还原的是市场决策输入（非交易结果累计）——不违反 L3b "绝不注入 reality 演化计数"；position-outcome 字段仍由 CF 自累计保级联。

## 不变量 / 红线

- observability-only write-only；红线守卫 `tests/test_cf_red_line_guard.py` 维持。
- 不改 live Judge 决策逻辑、不改生产 config、不改 choppy 地板 1.50、无需 event_backtest。
- perturbed 臂级联真实性不被削弱（position-outcome 字段仍随 CF 自身开仓演化）。
```

## openspec/changes/fix-cf-lab-symbol-state-injection/tasks.md

- Source: openspec/changes/fix-cf-lab-symbol-state-injection/tasks.md
- Lines: 1-21
- SHA256: ca693a68226afa48108d6a3e88aee670bdbc31c3121e5b393cf8d492834cbf9b

```md
# Tasks — fix-cf-lab-symbol-state-injection

> 骨架，comet-design 定方案后细化。

## 设计（comet-design）
- [ ] brainstorm 选定方案（A `_inject_cf_state` 以录制 _symbol_state 为基 + CF overlay / B to_snapshot 接种合并）
- [ ] 确认字段分类边界（决策输入还原 vs position-outcome CF overlay）+ perturbed 臂级联不削弱
- [ ] 产出 Design Doc + delta spec（sequential-perturbation-driver）

## 实现
- [ ] `_inject_cf_state` 的 `_symbol_state` 基于录制快照（还原 last_tech/trend_streak/last_decision_time）
- [ ] CF overlay 自身开过仓 symbol 的 position-outcome 字段（last_open_time/last_force_close_time）

## 测试
- [ ] sequential baseline fidelity ≥0.85（坐实 ~0.91，对照修前 0.798）
- [ ] perturbed 臂级联保留：CF 开仓后该 symbol 的 position-outcome 反映 CF 自身（非录制 reality）
- [ ] 红线守卫 `tests/test_cf_red_line_guard.py` 维持
- [ ] 全量 pytest 回归（基线 1252 不回退）

## 验收
- [ ] 重跑 cf_direction_recommendation.py：baseline_fidelity 从 0.798 升至 ≥0.85，untrustworthy 解除，实验室端到端可给方向或可信 no_actionable_direction
```

## openspec/changes/fix-cf-lab-symbol-state-injection/specs/sequential-perturbation-driver/spec.md

- Source: openspec/changes/fix-cf-lab-symbol-state-injection/specs/sequential-perturbation-driver/spec.md
- Lines: 1-16
- SHA256: 33b4096ebaea4fd39bbae4affb5e83e0666b666a24ddc91c3d836e56baf4c2fa

```md
## ADDED Requirements

### Requirement: 注入保留录制的 per-symbol 决策输入上下文
`_inject_cf_state` 注入回放状态时 SHALL 保留录制快照的 `_symbol_state`（per-symbol 市场决策输入上下文，如 `trend_streak`/`last_tech`/`last_decision_time`），SHALL NOT 清空为 `{}` 致 Judge 信号强度路径读不到上下文而误判信号不足。还原的是市场决策输入（非 reality 的 EV/胜率交易结果累计），不触 "绝不 per-record 注入 reality 演化计数" 反模式。

#### Scenario: 注入保留录制 _symbol_state
- **WHEN** `_inject_cf_state` 构造回放状态快照
- **THEN** 其 SHALL 以录制快照的 `_symbol_state` 填充（镜像 `_regime_manager` 透传），而非 `cf.to_snapshot()` 的空 `{}`

#### Scenario: baseline 臂忠实复现
- **WHEN** 零扰动 baseline 臂回放含 `trend_streak`/`last_tech` 的录制记录
- **THEN** 其 gate SHALL 与录制一致（不再因空 `_symbol_state` 退化为 hold_other），sequential baseline_fidelity 显著高于清空时（0.798 → ~0.91）

#### Scenario: 不改 EV/cooldown 战绩累计
- **WHEN** 还原 `_symbol_state` 决策输入字段
- **THEN** CF 的 EV gate / cooldown 战绩累计语义 SHALL 不变（仍由 `_seed_cf_prior` + CF 自累计驱动），perturbed 臂级联不被还原市场上下文削弱
```

