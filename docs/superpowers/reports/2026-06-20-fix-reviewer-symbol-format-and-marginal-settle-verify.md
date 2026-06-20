# Verification Report: fix-reviewer-symbol-format-and-marginal-settle

验证日期：2026-06-20
验证模式：full（实际代码面 = reviewer.py +7 / track_marginal60.py +100 / 新测试 +62 / CLAUDE.md +1）

## Summary

| Dimension | Status |
|---|---|
| Completeness | 18/18 tasks ✅，2/2 requirement 实现 ✅ |
| Correctness | reviewer 归一 + tracker 读 lifecycle 实现；review NEEDS_FIX→**I-1+M-1 已修** ✅ |
| Coherence | 符合 Design Doc + 高层 design.md；消费侧收口、匹配键不动 ✅ |

**全量回归**：`1343 passed / 8 failed / 4 deselected`（8 failed=既有 round2 asyncio 污染，非本 change）。1343 = 1338 + 5 新用例。**零新退化。**（I-1 修复后新测试不再毒化 `test_external_close_final_cause`，顺序无关全绿。）

## Completeness

- Tasks 18/18 `[x]`。
- 2 requirement 全实现：
  - reviewer trade record symbol 归一 → `_process_trade_result`(reduce/close) + `_apply_pnl_resolution` 三处套 `to_internal`
  - 边缘单从权威 lifecycle 结算 → `settle_fill_from_lifecycle` + `load_lifecycle`（读 `live_position_lifecycle.json`）

## Correctness

| 项 | 结论 |
|---|---|
| reviewer 写 trade_record/日志 symbol 经 to_internal 归一、幂等、None fail-safe | ✅ 单测覆盖 |
| **匹配键安全**：to_internal 仅包裹 trade_record['symbol']/日志，pnl_resolution upsert 按 entry_request_id/position_id 不过 to_internal | ✅ reviewer grep 确认（review 核对） |
| pending-skip 日志刻意保留原始格式（观测上游 leak） | ✅ 正确判断未误改 |
| tracker 读 lifecycle、fill+lifecycle 双归一、symbol+side+opened_at≈fill_ts(±300s) join | ✅ |
| epoch 对齐（fill_ts 日志本地时间 vs opened_at time.time()） | ✅ `_log_ts_to_epoch` 同机墙钟一致，真跑 dt≈0s |
| 去重消费（used_keys 防一条 lifecycle 被多 fill 重复结算） | ✅ 真跑拦下 UNI 1s 内重复 fill |
| pending/缺失/窗外 → 未结算 | ✅ **M-1 加 reconcile_status 守卫** |

**Code review（subagent 两阶段）**：初版 NEEDS_FIX，两项已修：
- **I-1（Critical）**：新测试 `asyncio.run()` 关默认 loop 毒化 `test_external_close_final_cause`（Py3.9 组合运行 5 failed）。修复：测试改 `async def`（pytest-asyncio `asyncio_mode=auto` 托管）。修后顺序无关 38 passed。
- **M-1（Minor）**：`settle_fill_from_lifecycle` 加 `reconcile_status=pending → 未结算` 守卫，字面对齐 spec Scenario。

**真跑验证 + 一个决策**（`python3 scripts/track_marginal60.py`）：
- 边缘60单已结算 **n=3 → n=5**（XLM 用权威 −10.09 非日志 −7.76；ETH/UNI/XRP 等格式被挡的现已结算）。
- **决策（已采纳，记录在此）**：M-1 守卫后 n 从 7→5——2 单 `status=closed` 且有非空 pnl 但 `reconcile_status=pending`（XLM +0.77 / NEAR −0.27，OKX 未 reconcile-final），按"只计权威 matched"剔除。这与本 change 主旨"读权威源"一致：pending ≠ 权威终值，待 reconcile 解析为 matched 后自动纳入。采纳 matched-only 为更严谨口径。
- 剩余未结算单均正确判定（2 phantom 无真实 close / short 单不属多单池 / 1s 重复 fill 去重），非错配。

## Coherence

- 符合 Design Doc：消费侧 reviewer 入口 + tracker 读时双归一；reviewer 是 live 路径但只统一记录格式不改匹配键；tracker observability-only 纯读+打印。
- 复用 canonical `utils/symbol.py::to_internal`（契约文档"所有 key 都该过它"）。
- 不回填历史 data/；不改 close path/executor/realized_pnl_resolver。

## Issues

**CRITICAL**：无（I-1 已修）
**WARNING**：无（M-1 已修）
**SUGGESTION**（非阻塞）：`json.load(open(path))` 文件句柄未显式 close（脚本短命无害）。

## Final Assessment

**All checks passed. Ready for archive.** review NEEDS_FIX 的 I-1（测试 loop 污染）+ M-1（pending 守卫）均已修；全量 1343 passed 零新退化（loop 污染消除）；边缘单已结算样本 n=3→n=5（matched-only 权威口径）；匹配键安全、observability-only、不回填历史均核对通过。
