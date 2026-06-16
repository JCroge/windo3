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
