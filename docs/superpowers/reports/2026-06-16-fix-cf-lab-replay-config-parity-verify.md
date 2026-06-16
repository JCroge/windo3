# Verification Report: fix-cf-lab-replay-config-parity

**Date:** 2026-06-16 · **Verify mode:** full (4 capabilities, 11 tasks) · **Base ref:** 21159c5

## Summary

| Dimension | Status |
|---|---|
| Completeness | 11/11 tasks `[x]`；4 delta spec 全部有实现 |
| Correctness | config-parity 根因已修并坐实（直接 L2 fidelity 0.34→**0.914**）；scope 内需求全覆盖 |
| Coherence | 符合 design.md + Design Doc；observability-only 红线维持 |

**Final assessment: 范围内全部达成，ready for archive —— 但带一个必须如实记录的端到端限制（见下）。** 无 CRITICAL，无 WARNING，1 个非阻塞 SUGGESTION。

## Completeness

- tasks.md 11/11 `[x]`。
- Delta spec → 实现：
  | Capability | 需求 | 实现 | 测试 |
  |---|---|---|---|
  | deterministic-replay-harness | 回放用生产 config 基线（snapshot 优先/DEFAULTS fallback） | `decision_replay.py` production_base_config + merge | `test_production_base_config_has_phase2_true` / `test_production_baseline_restores_fidelity` |
  | decision-replay-tape | 录 config_snapshot（schema v3，write-only） | `decision_tape.py` v3 + judge.py 2 chokepoint | `test_build_bundle_records_config_snapshot` / `_optional` |
  | sequential-perturbation-driver | 两臂同生产基线 + 扰动只覆盖目标旋钮 | replay_decision merge 精度 | `test_perturbation_overlays_on_production_base_only_target` |
  | replay-report-driver | 驱动经 replay_decision 自动获益 | 单 chokepoint | 端到端 driver 重跑 |

## Correctness

- **根因修复坐实**：直接 L2 replay（`replay_decision(r, None)`，生产基线）gate-level fidelity **0.914**（≥0.85 阈值；对照 config={} 的 0.365）。config-parity 是诊断的根因，已修。
- **merge 精度**：生产基线 < config_snapshot < perturbation；baseline 臂（config={}）= 纯生产基线；扰动只覆盖目标旋钮（坐实）。
- **observability-only**：config_snapshot 是 Judge write-only 捕获自身 config（同 state_snapshot），CF-sim 不读 live 运行态；红线守卫 4 passed。
- **向后兼容**：旧 v2 记录无 config_snapshot → fallback production_base_config()；v3 记录带自身 resolved config（含 env/YAML override，比 DEFAULTS 更忠实）。
- **回归**：全量 `1252 passed / 4 deselected`（基线 1247 +5，不回退）。最终全量人审 Ready-to-merge。

## ⚠️ 端到端限制（如实记录，非本 change 失败）

驱动 `cf_direction_recommendation.py` 重跑：`baseline_fidelity = 0.798`，**仍 < 0.8 → untrustworthy**，实验室端到端仍给不出可信方向。原因：驱动走 **sequential 臂**（`run_arm` 经 `_inject_cf_state` 把 `_symbol_state={}`/balance 等用 CF 组合重建替换录制快照），该 CF-state 注入引入 ~12pp 额外发散，把直接 L2 的 0.914 拉到 0.798。

这是本 change **proposal 明确列为非目标**的 CF 序列状态重建残差（`ev_gate→15m_blocked/accept` 同源）。本 change 修的是 **config parity**（已达成且是解开该残差的必要前置：两个 bug 此前纠缠，config 不修无法隔离 state 残差）。**实验室距可信线仅差 ~0.002，但未跨过**——需后续 change 修 `_inject_cf_state` 的 CF 序列状态重建保真（`_symbol_state`/balance 等）。

## Issues

### CRITICAL / WARNING
无。

### SUGGESTION（非阻塞）
- e2e fidelity 测试用直接 L2 replay（0.914），未覆盖 sequential 臂的端到端 fidelity；可在后续 change（修 CF-state 残差）加 sequential-臂 fidelity 断言。

## 后续 change（建议）
修 `_inject_cf_state` 的 CF 序列状态重建：`_symbol_state` 不应清空、`_available_balance` 应贴近现实——把 sequential baseline_fidelity 从 0.798 推过 0.8，使实验室端到端可信。这是 L3b 最后一层保真前沿。
