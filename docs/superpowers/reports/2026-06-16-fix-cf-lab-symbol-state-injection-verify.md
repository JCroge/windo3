# Verification Report: fix-cf-lab-symbol-state-injection

**Date:** 2026-06-16 · **Verify mode:** full (1 capability, 1-line core) · **Base ref:** 3d683d8

## Summary

| Dimension | Status |
|---|---|
| Completeness | tasks 全勾（A-minimal，overlay 项 N/A）；1 delta spec 实现 |
| Correctness | 根因 100% 坐实并修复；**sequential baseline_fidelity 0.798→0.944** |
| Coherence | 符合 design.md + Design Doc；observability-only 红线维持 |

**Final assessment: All checks passed, ready for archive —— 且 L3b 实验室端到端首次跨过可信线（里程碑）。** 无 CRITICAL/WARNING。

## Correctness

- **修复坐实**：`_inject_cf_state` 还原录制 `_symbol_state`（一行，镜像 `_regime_manager` 透传）。sequential baseline fidelity **0.798 → 0.944**（坐实 ≥0.85；甚至 > 直接 L2 0.914，因 sequential 还多复现了若干 cooldown 相关）。
- **红线**：还原的是市场决策输入（`trend_streak`/`last_tech`），非 reality EV/胜率交易结果累计 → 不触 L3b 反模式；EV/cooldown 战绩仍 CF 自累计未动。守卫 4 passed。
- **回归**：全量 `1255 passed / 4 deselected`（基线 1252 +3，不回退）。

## 🎯 端到端里程碑（驱动重跑）

`cf_direction_recommendation.py`（787 条）：
- `baseline_fidelity = 0.9441` → **`untrustworthy=False`**（**实验室端到端首次跨可信线**）。
- `baseline_cf_open=2`（CF 开仓，无死锁）；L4 扫描产出**真实 delta**（非 None）；perturbation 咬合——`divergence_ratio` 随地板降而增长（floor 1.50→1.20：0.0→0.16→0.44→0.73→0.81）。
- **结论（可信）**：放宽 `rr_floor_default`/`min_confidence` 的 PnL delta ≈ **+0.00** → **"非高价值杠杆"**。机理:这些 reject 被多 gate 过度决定(short_score/daily_bias/15m)，放宽地板只把决策级联到其它 gate，不产出盈利开仓。**独立佐证 choppy 地板 1.50 维持的合理性**。

这是三连修后的终点对比:
| 阶段 | 驱动 baseline_fidelity | 性质 |
|---|---|---|
| 初始 | 1.0（虚假） | 死锁空转，假信号 |
| 修 EV 冷启动后 | 0.34 | 诚实但太低，untrustworthy |
| 修 config parity 后 | 0.798 | 接近但未跨线 |
| **修 _symbol_state 后** | **0.944** | **可信，首次给出可信结论** |

## Issues
无 CRITICAL/WARNING。**SUGGESTION（非阻塞）**：A-minimal 的 `last_open_time` 两臂对称偏差是已知边际限制（delta 抵消），A-full 精确级联留作未来可选；当前 0.944 已充分。

## 后续（可选，非本 change）
实验室现可信，可做:多旋钮联合扫描(单旋钮已证非杠杆)、LLM 旋钮、A-full 精确级联。choppy 地板 1.50 维持不动(已获实验室可信佐证)。
