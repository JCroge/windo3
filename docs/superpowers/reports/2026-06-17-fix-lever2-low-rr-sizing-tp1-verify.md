# Verification Report: fix-lever2-low-rr-sizing-tp1

**Date:** 2026-06-17 · **Workflow:** hotfix · **verify_mode:** full（含 delta spec）

## Summary

| Dimension | Status |
|---|---|
| Completeness | 3/3 tasks；delta `ladder-weighted-rr` 2 scenarios |
| Correctness | 低 R:R 缩仓单一收口用 TP1 口径；地板 gate 仍用阶梯（lever2 多开仓不变） |
| Coherence | 单一收口消除重复（符合红线哲学）；1302 绿；lever2 关零回归 |

**Final Assessment: All checks passed. Ready for archive.**

## 根因消除核对

- **根因**：lever2 默认开后 `effective_risk_reward_ratio`（阶梯值）喂进低 R:R 缩仓判定（`judge.py` 原 ~1486/~3038），阶梯抬高的 R:R 把本应缩仓的低-R:R 趋势单松绑成全仓满杠杆。
- **消除**：两处缩仓块 consolidate 进单一收口 `_apply_low_rr_sizing`（`judge.py:3034`），缩仓判定 + `rr_scale` 用 `effective_rr_tp1`（TP1 口径）；两处调用点（主路径 `1486` / `_apply_regime_policy` `3030`）都传 `plan.get('effective_rr_tp1', rr)`。grep 核对：已无裸 `rr < 1.5 and is_long` 缩仓块。

## delta spec 场景核对

| 场景 | 证据 | 状态 |
|---|---|---|
| 阶梯抬高仍保护性缩仓 | `_apply_low_rr_sizing` 用 `rr_for_sizing`(=TP1)；`tests/test_low_rr_sizing_tp1.py::test_low_rr_sizing_scales_on_tp1_even_if_ladder_high`（阶梯1.7/TP1 1.4 仍缩仓） | ✓ |
| lever2 关时零回归 | lever2 关 `effective_rr_tp1 == effective_risk_reward_ratio` → 缩仓行为不变；1302 passed | ✓ |

## 解耦不变量

- 地板 gate `if rr < min_rr`（`judge.py:1466`/`3024`）仍用阶梯 `rr` → **lever2 多开仓不变**。
- 缩仓/降杠杆判定用 `effective_rr_tp1` → **保护性 sizing 恢复 pre-lever2 行为**。
- 二者解耦：开更多仓拿到了，但敞口不被阶梯松绑。

## 附带改进

- 低 R:R 缩仓从两处重复定义 consolidate 为单一收口 `_apply_low_rr_sizing`，符合项目"单一函数收口"红线哲学；回归守卫 `test_path_evidence_policy_in_low_rr_family` 更新为"单一收口 + 两调用点路由"不变量。

## 测试

- 全量 `python3 -m pytest -q` → **1302 passed / 4 deselected**（1298 + 4 新 sizing 测试）。
- event_backtest：结构性不调 `_apply_low_rr_sizing`（独立 `_build_plan`，0 引用）→ 不受影响。

## Issues

- CRITICAL：无 · WARNING：无 · SUGGESTION：无

## 上线影响

- 此 hotfix 是**更保守**的（恢复保护性缩仓），与 lever2 一并经 OS 重启生效。lever2 + 本 fix 一起上 = 多开仓但低-R:R 单仍缩仓保护。`LADDER_RR_ENABLED=false` 仍可整体回滚 lever2。
