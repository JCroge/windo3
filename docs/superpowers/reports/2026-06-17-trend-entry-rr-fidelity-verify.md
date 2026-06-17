# Verification Report: trend-entry-rr-fidelity

> 完整验证(verify_mode=full)。日期 2026-06-17。base-ref 582a0639。分支 trend-entry-rr-fidelity。

## Summary

| Dimension | Status |
|-----------|--------|
| Completeness | 22/22 tasks ✅;6/6 requirements 实现 |
| Correctness | 6/6 requirements 覆盖,30 个新单测,CF + rejected 流 A/B 真跑 |
| Coherence | 符合 design doc + delta spec;build 期一处验证工具漂移已修(design doc 回测章节对齐 CF 实验室) |

**全量回归**:`1285 passed / 4 deselected / 1 warning`(基线 1270 → +15,零回退)。
**编译**:`compileall` agents/utils/core PASS。
**openspec validate**:valid。

## Requirement → 实现 → 测试 映射

| # | Requirement (capability) | 实现 | 测试 |
|---|---|---|---|
| 1 | 干净趋势授予对齐地板 (trend-aligned-rr-floor) | `judge.py:_select_rr_floor` path-evidence OR 分支(入场前 entry_context,禁前视),授 `long_aligned_path_evidence` 1.30 | `test_rr_floor_policy.py::test_path_evidence_*`(授予/真choppy不误授/过热不授/开关关) |
| 2 | 被拒流记录 lever1 tech 输入 | `judge.py:_record_rejected_plan` 构造 tech_context + `counterfactual_ledger.record_rejection(tech_context=)` additive/fail-safe | `tests/test_rejected_tech_context.py`(含/缺 fail-safe) |
| 3 | 趋势对齐判定可观测可配置 | rr_policy/rr_floor_reason 记证据项 + `path_evidence_aligned_enabled` 开关 | `test_path_evidence_switch_off_keeps_default` |
| 4 | effective_rr 按阶梯加权 (ladder-weighted-rr) | `judge.py:_compute_ladder_rr`(w=[.5,.25,.25],剩余封顶+1R)+ `_effective_rr_for_plan` 开关 + `_build_plan` 接线 | `test_ladder_weighted_rr.py`(≥真实TP1/剩余封顶/缺档归一/开关) |
| 5 | 与旧口径同假设不引入概率折扣(v1) | Option B:无概率折扣,只离场比例权重(build 期修正反向缺陷) | `test_ladder_ge_tp1_when_all_positive`(守不反向) |
| 6 | 全样本 A/B 背书(CF 实验室)与灰度 | `cf_rr_fidelity_ab.py`(CF四臂)+ `cf_lever2_rejected_ab.py`(rejected流忠实)+ `_install_config_flags` 注入 + ab_result.md | `tests/test_rr_fidelity_knob_injection.py`(旋钮真生效) |

可观测:`effective_rr_tp1` / `effective_rr_ladder` / `ladder_rr_enabled` / `ladder_weights` 并存决策记录。

## 科学结论(A/B,诚实)

- **CF 重放四臂(decision_replay_tape,fidelity 0.9624)**:lever1 divergence=0(目标人群空——满足路径条件者皆已 bullish bias,现 aligned 已覆盖);lever2 divergence=0.656(旋钮生效)但 CF 开仓恒 2、delta=0(CF 退出无阶梯 + 组合 slot/EV 瓶颈)。→ **inconclusive,不予采信**。
- **lever2 忠实 A/B(rejected_signal_events,真实目标人群)**:16244 被拒 → 81.3% 经 ladder R:R 翻转 → 557 簇 → klines 可结算 72 簇,胜率 53.8%,**含亏单净 +0.21R/簇**(保守 TP1 口径)。→ **单笔正期望,但样本薄(13% 覆盖/近 3 天)→ suggestive 非 conclusive**。
- **灰度决策**:lever1+lever2 **均默认关**,不动实盘。后续拆出:① P2 bias 根治 / ① lever1 A/B(待埋点累积) / ② v2 概率校准 / ② 组合 slot 瓶颈诊断。

## 过程中拦截的自欺陷阱(可信度证据)

1. **低 R:R 风控绕过**(红线):新 policy 漏接 `low_rr_policies`(两处含 `_apply_regime_policy`),会让 1.30 地板单绕过缩仓/降杠杆 → 已修+回归守卫。
2. **概率折扣反向**:初版 v1 概率折扣只缩分子不缩风险分母,把 HYPE R:R 从 1.14 压到 0.86(反了),且子 agent 改测试基线掩盖 → 回滚,改 Option B。
3. **旋钮假阴性**:CF 重放走 `_install_config_flags` 镜像不走 `__init__`,新 flag 未注入则旋钮无效 → 已注入+守卫测试。
4. **A/B 不予采信如实记录**:未用"divergence 有数字"假装验证成功。

## Issues

- CRITICAL:无。
- WARNING:无(build 期验证工具漂移已按用户选择更新 design doc 解决)。
- SUGGESTION:lever2 忠实 A/B 的 klines 覆盖仅 13%,数据累积后应重跑以脱离近 3 天窗口偏差(已登记后续 change)。

## Final Assessment

All checks passed. 6/6 requirements 实现并测试,A/B 真跑产出诚实(含不予采信)结论,两杠杆默认关不动实盘,全量回归零回退。Ready for archive。
