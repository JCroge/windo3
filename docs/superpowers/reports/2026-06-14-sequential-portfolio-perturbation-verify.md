# Verification Report: sequential-portfolio-perturbation (L3b)

**Date:** 2026-06-14
**Mode:** full（3 capabilities）
**Change:** 反事实策略实验室 #3 第二步 / 收官层 — 序列组合态扰动重演
**Base-ref:** 9d1ed0f → HEAD

## Summary

| Dimension | Status |
|---|---|
| Completeness | tasks.md 全勾，3 capabilities（counterfactual-portfolio-sim / sequential-perturbation-driver / perturbation-delta-report）全实现 |
| Correctness | requirements/scenarios 全有测试；**最终整体审查 APPROVE，无 Critical/Important**；控制器修了一个级联正确性 bug |
| Coherence | 完全隔离（CF 绝不触真实状态/总线）；级联真实建模（seed-once + 各臂自累计）；红线 observability-only write-only 成立 |

**测试**：全量 `1217 passed / 4 deselected / 0 failed`；compile 干净。

## Completeness
- tasks.md 全勾。
- `counterfactual-portfolio-sim`：`utils/cf_portfolio.py::CounterfactualPortfolio`（slot/equity/EV/独立 cooldown/daily-stop + L1 估算退出反馈 + to_snapshot）。
- `sequential-perturbation-driver`：`utils/sequential_perturbation.py::run_arm`（时间序 + resolve_due + 注入 CF 状态 + 真实 replay_decision + apply_decision）。
- `perturbation-delta-report`：`build_delta_report`（两臂 delta + baseline 序列保真自检 + divergence + 诚实标注）。
- 测试 `test_cf_portfolio.py`(5) + `test_sequential_perturbation.py`(4)。

## Correctness（含最终审查 + 控制器修复）
- **控制器修的级联正确性 bug（commit 91d10b9）**：子代理初版 `_inject_cf_state` 每条 record 注入 reality 当时演化 EV 计数 → 人为抬高 baseline_fidelity 到 1.0、掩盖 perturbed 级联。**修复**：CF EV 状态 = 序列起点 `_seed_cf_prior`（recs[0] 录制先验）+ 各臂自累计；`_inject_cf_state` 只注 regime（市场状态）。修 `_seed_cf_prior` 原地 update 保留 cooldown defaultdict 类型（否则 record_result KeyError）。**最终审查确认修复完好未回归**。
- **时间因果**：`resolve_due(ts)` 在决策前结到期 → 持仓 PnL 只影响后续决策；`resolved_ts = created_at + hold_hours*3600`，按时间序结算。
- **delta + 保真闸**：baseline_fidelity = baseline-sim 决策 vs 录下决策一致率，< 0.8 标 untrustworthy + delta=None；两臂同估算 → 系统偏差在 delta 抵消；divergence_ratio。
- **最终整体审查结论**：APPROVE，**无 Critical/Important**；3 个 minor 非阻塞可选（缺 bar 边界因果安全、`regime` 死参、fidelity 分母含非可回放）。

## Coherence
- **隔离气密**（审查 grep 坐实）：`cf_portfolio`/`sequential_perturbation` 仅被 tests + 彼此引用，决策/风控路径零引用；CF `ArchetypeCooldown` 是独立实例；`replay_decision` 只 capture 不 publish 真实 bus；无真实 cooldown/daily-stop 读。
- **级联真实建模**：seed-once + 各臂自累计，非 reality 注入。
- 守卫 `test_cf_red_line_guard.py` 加 `cf_portfolio`/`sequential_perturbation` 禁读断言。
- design↔delta spec 无漂移（baseline 序列保真自检 Spec Patch 已回写）。

## 保真限制（已标注，非缺陷）
- 退出仅 SL/TP/24h（漏 trailing/partial/risk-close ~10-20%）；误差沿序列累积。
- 结论以 **delta** 为主（两臂同估算抵消系统偏差），绝对值标估算。
- baseline_fidelity 低则整 sim 不可信 → 拒答。

## Final Assessment
**无 CRITICAL。** 3 capabilities 全实现 + 测试，最终整体审查 APPROVE（隔离气密 + 级联真实），控制器修的级联 bug 经审查确认完好，全量 1217 passed 零回归。**Ready for archive。** 反事实策略实验室 L1→L2→L3a→L3b **全部完成**；仅 L4（旋钮扫描 + 自动方向推荐）为后续。
