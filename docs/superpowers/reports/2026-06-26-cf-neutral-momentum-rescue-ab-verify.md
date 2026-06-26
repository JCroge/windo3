# Verify Report — cf-neutral-momentum-rescue-ab

**Date:** 2026-06-26
**Change:** cf-neutral-momentum-rescue-ab
**Mode:** full
**Branch:** cf-neutral-momentum-rescue-ab (base 70dbeb9, 7 commits)
**Design:** docs/superpowers/specs/2026-06-26-cf-neutral-momentum-rescue-ab-design.md

## Summary scorecard

| Dimension | Status |
|---|---|
| Completeness | 13/13 tasks `[x]`;5/5 spec requirements implemented |
| Correctness | 5/5 requirements covered;13 driver tests + 1 red-line guard,全量 1474 passed / 0 failed / 4 deselected |
| Coherence | 实现符合 design.md(信号口径 B、A/B 对照、合成退出、不实例化 Judge)+ delta spec 无矛盾 |

**Final:** All checks passed — implementation matches spec. **READY TO ARCHIVE.**(最终 whole-branch review on opus: READY TO MERGE,无 Critical/Important。)

## 需求 → 实现映射

| Spec Requirement | 实现 | 证据 |
|---|---|---|
| population = choppy/mixed + neutral(accept+reject) | `load_population` | `cf_neutral_momentum_rescue_ab.py:30`;`test_*`(纳入/趋势体制不纳入) |
| 谓词方向无关 + A/B 判别 + **不读 strength** | `rescue_predicate` + `_run_grid` A/B | `:53`/`:195`;`test_predicate_ignores_strength`(双向);源码无 `trend.strength` 读取(仅 docstring 提及) |
| 标准化合成退出 + 多退出假设 + 阈值网格 + CF 契约 entry_price + 无覆盖/无效跳过 | `derive_strategy_geometry`+`synthesize_settle_fields`+`settle_clusters`+`_run_grid` | `:66/:98/:146/:195`;`test_synthesize_long_geometry`(断言 `entry_ref` not in plan)、`test_settle_records_nodata_skipped`、`test_synthesize_invalid_dist_returns_none` |
| 诚实门 min_sample=30 不下调 | `bucket_verdict` | `:173`;`test_bucket_verdict_thin_sample_insufficient` |
| observability-only 红线守卫 + 不改运行时 | `tests/test_cf_red_line_guard.py:95` | 6 决策/风控模块禁 import 断言通过;驱动不 import/实例化 Judge、不 replay、不改 config |

## 验证检查（full）

1. tasks.md 全部 `[x]` — ✅(13/13)
2. 符合 design.md 高层决策(D1 信号口径不用 replay-toggle / D2 population / D3 谓词无关 / D4 策略几何 / D5 复用结算栈)— ✅
3. 符合 Design Doc — ✅
4. 能力规格场景全覆盖(逐 scenario 有测试或代码路径)— ✅
5. proposal.md 目标(测量是否值得放宽阀门)— ✅ 已产出结论
6. delta spec 与 design doc 无矛盾 — ✅(build 期 spec patch 已同步 design D1-D5,无漂移)
7. 关联 Design Doc 可定位 — ✅

无 CRITICAL / WARNING。

## 测量结论(本 change 的核心交付)

驱动真跑(population 9272 = choppy 9052 / mixed 220,策略中位几何 sl_dist=0.0357 tp1_dist=0.0563 R:R=1.58):

**信号方向性鼓舞,但不达 actionable、且对退出几何敏感。**

- **策略中位 & 固定 R:R=1.5 退出下:A(救援候选)6/6 网格格子净 R **一致为正**(+0.07 ~ +0.62 R/簇),B(对照)6/6 **一致为负**(−0.16 ~ −0.25 R/簇)。A>0 / B<0 的干净分离 = 谓词确实在挑出更好的子集。**
- **但 A 桶样本不足**:几乎全 INSUFFICIENT_SAMPLE(n=10~30),最多到 low_confidence(n=36~42),**无一格达 actionable**。按本 change 预登记判据(A 诚实门须通过),**不满足"阀门值得放宽"**。
- **退出几何敏感性(重要警示)**:换"更紧 SL"(sl_dist=0.01)后,A 优势**坍塌**(−0.16 ~ +0.07,多格转负),B 也更不负。即被救援的 neutral 多单**需要足够的止损空间**;在 choppy 噪声里用 1% 紧止损会把它们和假突破一起止掉,edge 消失。

**裁定(按预登记判据):不改门。** A 诚实门未通过(INSUFFICIENT/low_confidence),既非干净"有 edge"也非干净"无 edge",而是 **suggestive、样本不足**。

**后续建议(不在本 change 范围,供决策):**
1. 装周更 cron 累积 A 桶样本(同 `cf-choppy-neutral-tp1-floor-ab` 模式),等 A 达 n≥30 actionable 再重判。
2. 若未来放宽阀门,**必须配足够止损空间**(策略中位/1.5 R:R 口径有 edge,紧 SL 无)——这与 path_evidence 现有 range_pos≤0.92 + 12h 动量门一致,提示放宽时不应叠加更紧的 SL。
3. 本 change 已坐实:path_evidence 阀门"双重失效"诊断正确(neutral 标的 strength 封顶 ~50),且救援子集**并非随机更差**——这本身排除了"放回 neutral 多单必然亏"的担忧。

## 安全 / 红线

- observability-only write-only:驱动仅 import `resolve_counterfactual`+`summarize_bucket`,不 import/实例化 Judge、不 replay、不下单、不改 config/live。
- 红线守卫测试覆盖 6 决策/风控模块。
- 无硬编码密钥、无新增 unsafe。
- 全量回归 1474 passed / 0 failed / 4 deselected。
