# Verification Report: deterministic-replay-golden-master (L2)

**Date:** 2026-06-13
**Mode:** full (23 tasks, 3 capabilities, 23 files)
**Change:** 反事实策略实验室路线图 #2 — 确定性全带回放 + golden master
**Base-ref:** ad24914 → HEAD (10 commits)

## Summary

| Dimension | Status |
|---|---|
| Completeness | tasks.md 全勾，3 capabilities（decision-state-snapshot / deterministic-replay-harness / replay-report-driver）全实现 |
| Correctness | 13 requirements / 28 scenarios 全有实现 + 测试。**最终整体审查发现 1 CRITICAL（accept 路径回放崩溃）已修复并新增坐实测试**；2 MINOR 一修一记录 |
| Coherence | L2 核心论点成立——回放复用真实代码、无任何生产逻辑重写（RegimeManager 还原真实实例非 stub）；红线 observability-only write-only 成立 |

**测试**：全量 `1201 passed / 4 deselected / 0 failed`；compile 干净；`DECISION_TAPE_ENABLED=false` flags-off 零回归。

## Completeness
- tasks.md 23/23 `[x]`。
- `decision-state-snapshot`：`Judge._capture_state_snapshot`（白名单 ~14 状态 + `_pending_open_ts`）+ `decision_tape._jsonable` + `build_bundle` 扩 `state_snapshot_before_decision`/`replayable`；测试 `test_decision_state_snapshot.py`。
- `deterministic-replay-harness`：`utils/decision_replay.py`（`restore_state` 含真实 RegimeManager 还原 / `replay_decision` mock 3 外部 await + 真实 `_make_decision` + 真实 CandidateRanker + 驱动延迟 flush / `compare_decision` 三层）；测试 `test_decision_replay.py`（含真实 open_long 端到端复现 + determinism）+ `test_golden_compare.py`。
- `replay-report-driver`：`cf_replay_driver.py`（rejected jsonl + klines 24h 窗口 → resolve → build_cf_report）；测试 `test_cf_replay_driver.py`。

## Correctness
逐 requirement 有实现 + 测试。关键正确性经最终整体审查独立核对：

- **CRITICAL（已修复）**：harness 初版只能回放 hold/reject，accepted open 因 `_candidate_ranker` 未设 + ranked accept 走延迟 task 发布而崩溃/截不到，且原测试只覆盖 hold 给假绿。**修复**：`_install_config_flags` 构造真实 `CandidateRanker`；`replay_decision` override `_schedule_rank_flush` 为 no-op 并在决策后同步驱动真实 `_flush_ranked_candidates`。**坐实**：新增 craft 的真实强势 bullish tech fixture，合法产出 open_long 端到端复现（`test_replay_accepted_open_does_not_crash_and_captures_open`）+ determinism（`test_replay_accepted_open_is_deterministic` 经 `compare_decision`）。harness/judge 未被弱化，只 craft 输入。
- **MINOR（已修复）**：`_pending_open_ts`（state 非 config）改为快照捕获 + 还原，使 `_sweep_stale_pending` 忠实。
- **MINOR（记录为 follow-up）**：`_llm_consecutive_failures`/`_llm_degraded_alerted`/`_processed_resolution_ids` 在 `_install_config_flags` 硬置初值——对**单决策**回放无害（注入 LLM 不失败、无 resolution 事件）；**序列回放**（L3+）时需从快照还原。
- golden 三层比对：离散字节级、连续 <0.5%、reasoning 仅信息——复现钉决策逻辑不钉自由文本。

## Coherence（L2 核心论点）
- **无生产逻辑重写**：最终审查全量确认 harness 不重写任何 Judge/RegimeManager/cooldown 逻辑。曾有一处 `_RegimeStub` 重写 `is_short_allowed`（commit ebef406 已改为还原**真实** `RegimeManager`）；`_make_decision`/`CandidateRanker`/`ArchetypeCooldown` 均为真实代码/实例。这是"线上代码即回测代码"的兑现，根治 event_backtest 决策层发散。
- **红线**：`utils.decision_replay` / `cf_replay_driver` 仅被 tests 引用；决策/风控路径零引用（`test_cf_red_line_guard.py::test_decision_paths_do_not_read_replay_products` 守卫；Judge 经 `_capture_state_snapshot` **写**快照允许，禁止的是**读**回放产物）。
- **确定性**：`_make_decision` 路径外部 await 仅 3 个全 stub + `time.time` patch + 无 random（uuid 仅 request_id，被 compare 排除）。
- design↔delta spec 无漂移（深度设计 3 处 Spec Patch 已回写）。

## Follow-up（不阻塞归档）
- 真实数据 golden 终验：N≥50 条带状态 record 跑 driver 期望 replayable 100% 复现（待埋点累积，runbook）。
- 序列回放（L3）需把 LLM-degraded 计数 / resolution dedup 纳入快照还原。

## Final Assessment
**无 CRITICAL 遗留。** 最终审查发现的 accept-replay CRITICAL 已修复 + 新增端到端坐实测试，golden-master 对最重要的开仓决策类已证明可复现且确定性。23/23 任务完成，全量 1201 passed 零回归，红线成立，无生产逻辑重写。**Ready for archive（带已记录的真实数据终验 + 序列回放 follow-up）。**
