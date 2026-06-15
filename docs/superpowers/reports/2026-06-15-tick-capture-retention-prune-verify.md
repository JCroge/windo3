# Verification Report: tick-capture-retention-prune (hotfix)

**Date:** 2026-06-15
**Change:** `openspec/changes/tick-capture-retention-prune/`
**Workflow:** hotfix（build_mode: direct）
**Verify mode:** full（scale 脚本按 11 子任务 + 9 文件判 full；实际生产面仅 2 文件）

## Summary

| Dimension | Status |
|---|---|
| Completeness | 11/11 tasks `[x]`；delta capability `tick-snapshot-capture` retention 缺口已补 |
| Correctness | delta 3 scenario 全覆盖（4 新测试）；全量 1238 passed（1234+4） |
| Coherence | 实现符合 design.md（镜像 DecisionTape 节流 prune）；observability-only 零决策路径 |

**结论：无 CRITICAL/WARNING，Ready for archive。**

## 验证检查项

1. **tasks.md 全勾** ✅ 11/11。
2. **改动与 tasks 一致** ✅ 生产改动：`utils/tick_capture.py`（+retention_days/prune_every 参数、`_maybe_prune`、record_bar throttled 调用）、`agents/trading/multi_data_collector.py`（构造点 +1 行铺 `retention_days`）；测试 `tests/test_tick_capture_prune.py`（4 case）；delta spec。
3. **编译通过** ✅ `compileall` utils + agents + tests 无输出。
4. **测试通过** ✅ 全量 `1238 passed / 4 deselected`；红线守卫 `test_cf_red_line_guard.py` 4 passed 不回归。
5. **无安全问题** ✅ 无硬编码密钥、无新增 unsafe；prune 仅 DELETE 自有 observability db。

## delta spec scenario 覆盖（tick-snapshot-capture「tick 路径与 retention 受控」）

| Scenario | 测试 |
|---|---|
| flag 关停无残留 | `test_prune_disabled_store_noop`（disabled → 不写不 prune 不抛） |
| retention 滚动清理超期数据 | `test_prune_deletes_expired_keeps_in_window`（超期删/界内留）+ `test_prune_throttled_by_prune_every`（节流计数） |
| 清理失败不中断采集 | `test_prune_failure_does_not_break_record`（prune 异常不传播、bar 已落盘、drop_count+1） |

## 设计一致性

- 实现严格镜像 `utils/decision_tape.py::DecisionTape` 的 `prune_every`/`_writes_since_prune`/`_maybe_prune` 节流模式（design.md Decision）。
- prune 在 record_bar 写入 commit **之后**触发 → prune 失败绝不丢失刚写的 bar；`_maybe_prune` 自带 try/except，record_bar 外层 except 二次兜底，双层 fail-safe。
- wall-clock cutoff（1s bar 实时写入，open_time≈now，裁剪正确）。
- observability-only：未引入任何决策路径对 klines_1s 的读取，红线不变。

## 已知边界（非缺陷）

- prune 节流：最多多存 `prune_every`（默认 2000）次写入对应时间窗的 bar，MB 级可忽略。
- 已运行 live 进程：下次写满 prune_every 即开始清理，无需手动干预、无数据迁移。

**Ready for archive。**
