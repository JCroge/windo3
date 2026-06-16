# Verification Report: fix-cf-lab-ev-coldstart-deadlock

**Date:** 2026-06-16 · **Verify mode:** full (4 capabilities, 11 tasks, 19 files) · **Base ref:** 561cf11

## Summary

| Dimension | Status |
|---|---|
| Completeness | 11/11 tasks `[x]`；4 delta spec 全部有实现 |
| Correctness | 4/4 capability 需求覆盖，全部场景有测试 |
| Coherence | 符合 design.md + Design Doc；红线守卫维持 |

**Final assessment: All checks passed. Ready for archive.** 无 CRITICAL，无 WARNING，1 个非阻塞 SUGGESTION。

## Completeness

- **tasks.md**：设计 3 + 实现 3 + 测试 4 + 验收 1 = 11/11 全部 `[x]`。
- **Delta spec → 实现映射**：
  | Capability | 需求 | 实现 | 测试 |
  |---|---|---|---|
  | counterfactual-portfolio-sim | CF rolling 胜率窗口(live 语义) | `cf_portfolio.py:34,77,100-101` | `test_cf_portfolio.py` 3 例 |
  | sequential-perturbation-driver | EV 状态暖启动播种 | `sequential_perturbation.py:32-37` | `test_seed_warms_*`/`test_seed_window_evicted_*` |
  | perturbation-delta-report | gate-level baseline_fidelity | `sequential_perturbation.py:93-111,126,135` | `test_gate_extraction_prefix`/`test_changed_gate_counts_as_non_reproduction` |
  | replay-report-driver | 驱动按 v2+tech 过滤 | `cf_direction_recommendation.py:33-34` | `test_load_records_filters_v1_and_empty_tech` |

## Correctness

- **窗口语义对齐 live**：`_recent_win_rate = sum(window)/len(window)`，镜像 Reviewer `rolling_window_size=20`。✅
- **从 CF 自身结果演化（防 L3b 级联陷阱）**：窗口仅在 `resolve_due` 由 CF 自身结算 append；合成种子 FIFO 挤出（`test_seed_window_evicted_*` 坐实 20 笔后纯 CF）。✅
- **暖启动破死锁**：`test_relaxing_floor_breaks_deadlock_perturbed_opens` floor-only→`perturbed_cf_open=2`（>0）。该测试**未禁用 EV gate**（禁用会使未修代码也通过，属假验证），是真正的判别性测试。✅
- **gate-level 保真**：换 gate 拦（EV vs rr）计为不复现（`test_changed_gate_counts_as_non_reproduction`）。✅
- **驱动 v2 过滤**：v1/空 tech 记录被剔除，不盲信 stale `replayable`。✅
- **端到端复跑** `cf_direction_recommendation.py`：`baseline_fidelity` 由**虚假 1.0** → **诚实 gate-level 0.34** → `untrustworthy=True` 诚实拒答。区别于旧死锁空转（旧为 fidelity=1.0 假信号 + 永久 cf_open=0）。✅

## Coherence

- 符合 design.md 高层决策（镜像 Reviewer 20 窗口 + 暖启动播种 + gate-level 保真 + v2 过滤）与 Design Doc。
- **observability-only 红线维持**：仅改 CF-sim/driver/test，`test_cf_red_line_guard.py` 4 passed（禁止交易决策路径读 CF 产物）。
- 全量回归 `1247 passed / 4 deselected / 1 warning`（基线 1238 +9，不回退）。
- 最终全量人审：Ready-to-merge。

## Issues

### CRITICAL
无。

### WARNING
无。

### SUGGESTION（非阻塞）
- **Dead code**：`utils/sequential_perturbation.py` 的 `_decision_class` 在 gate-level 切换后于本模块不再被引用（`perturbation_replay.py` 同名函数是独立副本，仍在用）。两轮审查均判非阻塞。建议后续清理，不阻断归档。

## 超 scope 的新发现（留后续 change）
gate-level `baseline_fidelity` 仅 **0.34**，揭示 CF-sim 序列重建与现实在 gate 层仅 34% 一致（疑分桶 EV/archetype 状态重建差异）。本 change scope 是 EV 冷启动死锁（已解），该保真缺口是 L3b 下一保真前沿，应另开 change。修复前实验室对单旋钮维持 `untrustworthy` 诚实拒答，choppy 地板 1.50 维持不动。
