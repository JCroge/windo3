---
comet_change: fix-cf-lab-symbol-state-injection
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-16-fix-cf-lab-symbol-state-injection
status: final
---

# Design Doc — fix-cf-lab-symbol-state-injection

修复 L3b sequential 臂 baseline_fidelity 0.798 的最后一层残差。方案 A-minimal。上游事实源见 proposal.md。

## 1. 根因（100% 坐实）

`utils/sequential_perturbation.py::_inject_cf_state` 用 `cf.to_snapshot()` 替换录制 `state_snapshot`，`to_snapshot` 把 `_symbol_state` 硬编码 `{}`。Judge 信号强度路径读 `_symbol_state` 的 `trend_streak`/`last_tech` → 空 `{}` 致 "信号强度不足" → hold_other，而非录制 `rr_below_floor`。

**证据**：全量 779 条，L2 fidelity 0.911 vs sequential 0.798；残差 90 条全是「L2 复现但 inject 没复现」（86 `rr_below_floor→hold_other`）；override 还原 `_symbol_state` → **90/90（100%）复现**。其它字段 override 不复现（已证伪非根因）。

## 2. 修复（A-minimal）

`_inject_cf_state` 在 `snap = cf.to_snapshot(...)` 后，新增：
```python
snap["_symbol_state"] = recorded_snap.get("_symbol_state") or {}
```
镜像它已有的 `recorded_snap.get("_regime_manager")` 透传模式（市场状态 CF 不重算，用录制值）。

**为何 minimal 而非 overlay CF position-outcome**：`last_open_time` 等 position-outcome 字段在 perturbed 臂用 live 值会有窄失真，但**两臂用同值 → 系统偏差在 delta 抵消**（L3b 既有原则，见 fidelity_note）；仅当扰动致 CF 开了 baseline 没开的 symbol 且 300s 内同 symbol 再决策才有边际影响。A-full 的精确收益窄、却引入 overlay 边界出错面（本实验室已栽过多次微妙级联 bug）。YAGNI：达成 baseline ≥0.85 目标的最小改动。

## 3. 红线（关键）

还原的是**市场决策输入**（`trend_streak`/`last_tech`/`last_decision_time`——CF 无法重建的市场上下文），**不是** reality 的 EV/胜率交易结果累计 → **不触** L3b "绝不 per-record 注入 reality 演化计数" 反模式（该反模式针对 EV gate/cooldown 的战绩计数，已由 `_seed_cf_prior` + CF 自累计正确处理，本 change 不动）。observability-only write-only 不变。

## 4. 测试策略

- **坐实**：全量 v2/v3 磁带 sequential baseline fidelity ≥0.85（实测 ~0.91；对照修前 0.798）。
- **不虚高**：perturbed delta 仍由 CF 自身结果驱动——还原 `_symbol_state` 不改 EV/cooldown 战绩累计（构造 fixture 验证：perturbed 臂开仓数/结果不因还原 `_symbol_state` 而异常）。
- **红线守卫** `tests/test_cf_red_line_guard.py` 维持。
- 全量 pytest 基线 1252 不回退。

## 5. 非目标 / 坦白

- 不动 EV/cooldown 战绩累计（CF 自累计语义不变）。
- A-minimal 的 `last_open_time` 两臂对称偏差是已知边际限制，不在本 change 追求精确（A-full 留作未来可选）。
- 不改 live Judge/生产 config/choppy 地板 1.50、无需 event_backtest。
- 修完 sequential ~0.91 跨 0.8，实验室端到端首次可信；但单旋钮放宽地板大概率仍 `no_actionable_direction`（reject 被多 gate 过度决定）——那将是**可信结论**而非死锁/发散。
