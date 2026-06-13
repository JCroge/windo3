# Verification Report: counterfactual-replay-foundation

**Date:** 2026-06-13
**Mode:** full (23 tasks, 3 capabilities, 31 files)
**Change:** 反事实策略实验室路线图 #1 — L1 可信被拒单回放 + 未来忠实回放原料地基
**Base-ref:** 7ea92f6 → HEAD (9 commits)

## Summary

| Dimension | Status |
|---|---|
| Completeness | 23/23 tasks `[x]`，3 capabilities 全实现 |
| Correctness | 13 requirements / 28 scenarios 全有实现 + 测试，1 项 WARNING（dual-track 价格选择 = L2 边界） |
| Coherence | 实现遵循 design doc D1-D5；delta spec 与 design doc 无漂移（深度设计的 5 处修订已双向回写）；红线守卫成立 |

**测试**：全量 `1185 passed / 4 deselected / 0 failed`；cf 专项 33 passed；compile 干净；`DECISION_TAPE_ENABLED=false TICK_CAPTURE_ENABLED=false` flags-off 子集零回归。

## Completeness

- **Tasks**：`tasks.md` 23/23 `[x]`，0 未勾。
- **Spec 覆盖**：
  - `decision-replay-tape`（4 req / 9 scenario）→ `utils/decision_tape.py` + Judge 接线（`agents/trading/judge.py:1980, 3003`）；测试 `test_decision_tape.py`(6) + `test_judge_decision_tape_wiring.py`(3)。✓
  - `counterfactual-pnl`（5 req / 12 scenario）→ `utils/counterfactual_pnl.py` + `utils/cf_honesty_gate.py` + `replay_report.py::build_cf_report`；测试 `test_counterfactual_pnl.py`(6) + `test_cf_honesty_gate.py`(5) + `test_cf_replay_report.py`(2)。✓
  - `tick-snapshot-capture`（4 req / 7 scenario）→ `utils/tick_capture.py` + collector 接线（`multi_data_collector.py`）；测试 `test_tick_capture.py`(4)。✓

## Correctness

逐 requirement 映射实现 + 测试，全部有据。关键正确性已由最终整体审查独立确认：
- CostModel 单一真相源复用，无第二份成本/资金费公式（`counterfactual_pnl.py` 调 `cm.round_trip_cost`）。
- SL/TP 触发判定：同根冲突 SL-first + `price_ambiguous`；long/short 比较算子正确（最终审查逐一核对）。
- 诚实 gate 三档 + Wilson + 固定种子 bootstrap（确定性可测）；单笔主导被 CI 暴露。
- 决策磁带 fail-safe（绝不抛进决策路径）+ `getattr` 防御（部分构造缺 tape 不破决策）+ prune 节流（默认每 500 写，避免热路径 O(n²)）。

**WARNING（非 CRITICAL，已命名为 L2 边界）**：
- `tick-snapshot-capture` 的"价格精度双轨（tick 优先、缺 1s 退化 1m）" scenario 结构上被支持（`resolve_counterfactual(record, bars)` 接受任意 bars），但 **缺端到端 driver** 把真实 `rejected_signal_events.jsonl` + klines 解析成 report rows 并选择 1s/1m 价格源。即各单元（tape/PnL/gate/report）单测齐全但未串成可运行回放。这是 L1 foundation 与 L2 回放驱动的边界，已在 `docs/to-do-list.md` 显式命名为 #2 首交付项。

## Coherence

- **design.md 高层决策**：实现遵循 D1（独立 decision_tape，不扩 journal）、D2（fail-safe writer）、D3（1s bar→独立 klines_1s.db）、D4（CostModel + SL-first + 双轨）、D5（诚实 gate 单点收口）。
- **Design Doc 一致**：深度设计的 5 处修订（D1 内联 LLM 抗过期 / 90d retention / Wilson+bootstrap 三档 / 1s bar / funding 近似）均双向回写到 delta spec，无 delta↔design 漂移。
- **红线**：`tests/test_cf_red_line_guard.py` 守卫；最终审查全量 grep 确认仅 `judge.py`（写 tape）+ `multi_data_collector.py`（写 tick）引用 CF 符号，无任何 gate/veto/halt/rank/daily-stop 读取。observability-only write-only 成立，与 `data-source-provenance`/`agent-health-supervisor` 同性质。
- **模式一致**：config_loader DEFAULTS/HARD_LIMITS/env_map、state_paths namespace 派生、CostModel 复用、fail-safe 写入均遵循既有项目惯例。

## SUGGESTION（记入 to-do，不阻塞归档）

- M1：`tick_capture_retention_days` 配置已加但 `OneSecBarStore` 未接 prune，1s 库当前无界增长（留 #2）。
- M2：`replay_report.py` 并存两套样本充分性阈值（旧 Phase 2 `<5` vs 诚实 gate `n<30`），CF 以诚实 gate 为准（建议加注释）。
- M3：`funding_approx` / bias band 字段略超前于消费者（端到端 driver 就绪后被消费）。

## Final Assessment

**无 CRITICAL。** 23/23 任务完成，13 req / 28 scenario 全有实现 + 测试，全量 1185 passed 零回归，红线成立，design↔spec 无漂移。1 个 WARNING + 3 个 SUGGESTION 均为已命名的 L1→L2 边界，不阻塞归档。**Ready for archive（带已记录的 #2 后续项）。**
