# Entry Drift Hybrid Policy — Acceptance

> Change: `entry-drift-hybrid-policy`
> Design: `docs/superpowers/specs/2026-06-01-entry-drift-hybrid-policy-design.md`
> Plan: `docs/superpowers/plans/2026-06-01-entry-drift-hybrid-policy.md`
> Baseline: `954 passed / 4 deselected / 1 warning` (was 921; net +33)

## AC-1：Judge plan 字段扩展

**测试**：`tests/test_judge_plan_anchor_fields.py` (4 case)
- entry_ref / sl_pct / tp_pct 三字段在 long 与 short 都正确生成
- 比例使用 `price_round` 后的实际存储值，保证 `sl_pct == |stored_sl - stored_entry_ref| / stored_entry_ref`

## AC-2：Drift gate 4 档分类

**测试**：`tests/test_entry_drift_hybrid_policy.py::test_classify_drift_*` (11 case)
- 边界包含规则：drift=0.5% 仍 accept，drift=2% 仍 small，drift=5% 仍 medium
- 5/30 XLM 真实复盘：entry_ref=0.2179, live=0.2336, drift=7.2% → abandon, reason=drift_too_large

## AC-3：重算函数 SL/TP 同比例平移 + medium floor 加成

**测试**：`test_recompute_*` (5 case)
- long & short 双向比例平移
- medium band floor +0.20 拦截 R:R=2.0 plan
- recalc_pass 时 R:R=2.4 通过 floor 2.2
- 不修改原 plan（deepcopy）

## AC-4：Gate 1 abandons XLM replay

**测试**：`test_gate1_abandons_xlm_replay`
- create_order 不被调用，open_position_with_plan 返回 None
- 入队 entry_drift_abandoned 告警

## AC-5：Gate 2 基准始终原 plan.entry_ref

**测试**：`test_gate2_basis_is_original_entry_ref_not_segmented`
- 累计 drift 6% 必须从 Judge 决策时点起算，不能被分段成 1% + 5% 两次 small/medium 通过

## AC-6：partial_tp_1 双源真相 invariant

**测试**：`test_set_position_tp_*` (3 case) + `test_update_trailing_invariant_breach_halts_symbol`
- 写时 setter assert 一致；旁路写入触发 _update_trailing 顶部 invariant → halt symbol + risk_alert.tp_invariant_breach
- 空 levels 也被拒绝

## AC-7：可观测性 — jsonl + risk_alert + attribution

**测试**：
- `test_ledger_records_entry_drift_decision` — jsonl event 包含 event/symbol/gate/band/drift_pct
- `test_agent_publishes_drift_alerts_after_open` — risk_alert 通过 agent layer 发布到 bus
- `test_execution_result_carries_attribution_entry_drift` — attribution.entry_drift 嵌套写到 execution_result.v2

## AC-8：Plan 字段缺失 fail-safe

**测试**：
- `test_classify_drift_missing_*` (3 case，缺 entry_ref/sl_pct/tp_pct 各一)
- `tests/test_event_backtest_drift_compat.py::test_old_plan_skips_drift_gate_failsafe`
- 缺字段 → accept 路径不破坏 + plan_missing_entry_ref 告警入队

## AC-9：删除冗余路径

**Code review**：
- `executor.py:1991-1997`（TP 机械修正）— 已删除
- `executor.py:2203-2205`（limit 2% 校准）— 已删除
- `executor.py:2259-2262`（fallback 0.5% 检查）— 已删除
- `executor.py:1983-1988`（SL 方向修正）— 改为 invariant + halt + risk_alert.sl_invariant_breach

## AC-10：OKX testnet 冒烟（运维侧）

**手动**：
1. mock 价格漂移触发 small drift → recalc_pass 开仓，确认 SL/TP 写新值
2. mock 价格漂移触发 abandon → 不下单，确认 risk_alert + jsonl 落地
3. 检查 attribution.entry_drift 在真实 execution_result.v2 上正确

## 红线遵循

- ✅ 单一真相源：所有 drift 判定走 `_classify_entry_drift`
- ✅ TP 双源真相：单一 setter `_set_position_tp` + 读时双保险 invariant
- ✅ close/reduce 不受影响：本 change 只动 open 路径
- ✅ 状态文件命名空间无影响
- ✅ LLM 不参与 drift 判定（纯规则）
- ✅ Gate 2 基准始终原 plan.entry_ref，防分段累加规避

## 提交清单

1. `6356476` spec(entry-drift): delta spec for hybrid drift gate
2. `5875c5c` feat(judge): emit entry_ref/sl_pct/tp_pct anchor fields in _build_plan
3. `2a55f25` feat(executor): add drift threshold constants and DriftDecision dataclass
4. `bb806df` feat(executor): _recompute_plan_for_drift with proportional SL/TP shift
5. `eef8253` feat(executor): _classify_entry_drift drift gate single-source classifier
6. `20cc0bb` feat(executor): _set_position_tp single sink + partial_tp invariant halt
7. `fbbbbe9` feat(executor): wire Gate 1 drift gate; replace SL fix with invariant; remove mechanical TP fix
8. `f6e7f9a` feat(executor): wire Gate 2 in fallback path with orig_plan baseline; drop legacy 0.5%/2% checks
9. `1262ea2` feat(ledger): record_entry_drift_decision observational event
10. `a58f97c` feat(agent-executor): drain drift alerts to risk_alert; pipe drift reasons into execution_result.v2
11. `c35ba06` feat(agent-executor): expose attribution.entry_drift on reject and accept paths
12. `2be3380` test(event-backtest): legacy plans without anchor fields fail-safe accept
13. `1fd8233` docs(claude-md): record entry-drift-hybrid-policy baseline + red-line rules
