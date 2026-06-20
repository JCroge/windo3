# Verification Report: ev-decouple-forward-ab

验证日期：2026-06-20
验证模式：full（scale：18 tasks / 1 capability / 13 changed files，实际代码面 = `cf_ev_decouple_ab.py` + 2 测试文件）

## Summary

| Dimension | Status |
|---|---|
| Completeness | 18/18 tasks ✅，4/4 requirement 实现 ✅ |
| Correctness | 分类/结算/诚实门/红线全实现；**code review 揪出 1 Critical（结算契约）已修 + 加集成测试** ✅ |
| Coherence | 符合 Design Doc + 高层 design.md；observability 红线守卫不回归 ✅ |

**全量回归**：`1331 passed / 8 failed / 4 deselected`。8 failed = `test_round2_probe_long_dispatcher`(4) + `test_round2_request_id_position`(4)，既有 round2 asyncio 污染、隔离单跑全 PASS、非本 change 引入。1331 = 1319 + 12 新增（11 驱动单测含 1 集成测试 + 1 红线守卫）。**零新退化。**

## Completeness

- Tasks 18/18 全 `[x]`。
- 4 requirement 全实现：
  - 解耦放行分类（gate-toggle 两臂 + baseline 自检）→ `classify_accepts`
  - 前向结算与桶对比（CF 为主、real PnL 交叉）→ `dedup_clusters`+`settle_clusters`+`fuzzy_join_real_pnl`
  - 诚实门与 coverage 透明 → `bucket_verdict`(summarize_bucket min_sample=30)+nodata 计数
  - observability-only write-only 红线 → 红线守卫 `test_decision_paths_do_not_read_ev_decouple_ab`

## Correctness

| 项 | 结论 |
|---|---|
| gate-toggle 经 perturbation override 切 `ev_winrate_gate_enabled`、不重写门逻辑 | ✅（reviewer 核 decision_replay.py:99-106） |
| baseline 自检失真排除（二元 accept/reject） | ✅ |
| 簇去重 symbol+side+>1h 取最早 | ✅（与 cf_lever2_rejected_ab 一致） |
| 结算 TP1 保守 R（tp→tp1/sl_dist, sl→−1, expired→0, 含亏单） | ✅ |
| **结算 record 契约**（entry_price/created_at/side/sl/tp） | ✅ **修复后**——见下 |
| 诚实门 min_sample=30 不下调、领先裁定、薄样本拒答 | ✅ |
| real PnL symbol+ts 模糊 join、标注无 request_id/pending 不计 | ✅ |

**Code review Critical（已修复）**：初版 `settle_clusters` 把 live plan 原始 dict（字段 `entry_ref`、无 `created_at`）传给 `resolve_counterfactual`（硬下标 `record["entry_price"]`）→ 真跑必 `KeyError`，且被 mock-resolve 测试掩盖。修复：`extract_settle_fields` 构造结算专用 record（`entry_price`/`created_at`/`side`/`stop_loss`/`take_profit`），并新增**不 mock resolve 的集成测试** `test_settle_clusters_real_resolve`（合成 bars 验证 tp/sl 真实跑通）锁死契约。修复 commit `e8d7dd8`。

**真跑验证**（`python3 cf_ev_decouple_ab.py`）：
```
replayable accept: 69 | baseline 自检: 忠实 54 / 失真排除 15
解耦放行: 38 | 双门皆过: 16 | 拒因 {'ev_gate': 38}
解耦放行桶: 10簇 tp=1/sl=5/exp=4  净R -0.350 R/簇  诚实门 INSUFFICIENT_SAMPLE(n=6)
双门皆过桶:  5簇 tp=0/sl=4/exp=1  净R -0.800 R/簇  诚实门 INSUFFICIENT_SAMPLE(n=4)
```
结论：两桶均 INSUFFICIENT_SAMPLE（诚实门拒答）；**suggestive 读数不支持"解耦放行更差"假设**——解耦放行桶 −0.350 反而优于双门皆过桶 −0.800，但样本太薄不作结论。符合设计预期（薄样本拒答、当下不给红绿灯）。

## Coherence

- 符合 Design Doc：分类头 + 结算半身（镜像 cf_lever2_rejected_ab）+ 诚实门领先裁定 + real PnL 次要交叉，全部落实。
- 红线守卫 `tests/test_cf_red_line_guard.py` 新增禁读断言 PASS；决策/风控路径未 import 新驱动。
- observability-only write-only：驱动只读磁带/klines/lifecycle + 打印报表，不下单/不改 config/不 publish 真实 bus。

## Issues

**CRITICAL**：无（结算契约 Critical 已在 build 阶段修复 + 集成测试覆盖）
**WARNING**：无
**SUGGESTION**（非阻塞）：`_reject_reason` 对走 `_publish_hold` 的非 EV reject 退化为 `hold_other`（当前解耦放行拒因实测 38/38 全 ev_gate，无影响；若日后混入其它门略损诊断力，可 fallback 读 reasoning）。

## Final Assessment

**All checks passed. Ready for archive.** 无 CRITICAL/WARNING；结算契约 Critical 已修复并加集成测试守护，全量回归零新退化，真跑产出诚实读数（诚实门拒答、当下不给定论）。
