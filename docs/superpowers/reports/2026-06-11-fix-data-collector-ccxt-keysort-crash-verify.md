# Verification Report: fix-data-collector-ccxt-keysort-crash

- **Date**: 2026-06-11
- **Mode**: full（11 tasks / 2 capabilities / 7 文件，超轻量阈值）
- **Branch**: `fix-data-collector-ccxt-keysort-crash`
- **base-ref**: `da3d3170c874f9d3572c12d6b2e499268ab777fb`

## Summary

| Dimension | Status |
|---|---|
| Completeness | 11/11 tasks ✓ · 2/2 capabilities 实现 |
| Correctness | 9/9 spec scenarios 由代码 + 测试覆盖 |
| Coherence | Design D1/D2/D3 全部遵循；pattern 一致 |

## 证据

- 复现脚本：`create_exchange(okx) + load_markets()` → `load_markets OK: 3860 markets`（崩溃消除，根因修复）。
- 全量测试：`python3 -m pytest -q` → **1098 passed / 4 deselected / 1 warning**（基线 1088 + 新增 10：`test_ccxt_compat` 4 / `test_base_setup_guard` 2 / `test_agent_task_failure_alert` 4）。
- build guard 6/6 PASS。

## Scenario 覆盖

### exchange-client-resilience
- **OKX null-id 市场容忍** → `utils/ccxt_compat.py:_safe_keysort`（`key=(kv[0] is not None, str(kv[0]))`，None 排首）；测试 `test_keysort_tolerates_none_key` / `test_markets_by_id_with_none_id_does_not_crash` + 真实 `load_markets`。
- **4 调用点全保护** → `utils/exchange_factory.py` import shim，patch 基类 `ccxt.Exchange.keysort`，okx/binance 子类继承；import-once 幂等（`_PATCH_FLAG`）。
- **正常数据不变** → `test_keysort_all_str_unchanged`。

### agent-fault-visibility
- **setup 失败记录 + 重抛** → `agents/base.py:run()` try/except → `logger.critical(...traceback...)` → `raise`；测试 `test_setup_failure_logged_with_traceback_and_reraised`。
- **正常 setup 不受影响** → `test_setup_success_does_not_log_critical`。
- **失败任务告警 + health 仍计数** → `orchestrator._collect_task_stats` + `_maybe_alert_task_failure` 发 `telegram_alert{type:agent_task_failed}`，`_write_agent_health` 仍写 `tasks_failed`；测试 `test_alert_published_once_then_deduped` / `test_collect_task_stats_maps_index_to_agent_name`。
- **同一失败不重发** → `_alerted_failed_tasks` dedup set；测试同上。
- **越界 → unknown-agent** → `_collect_task_stats` index 兜底；测试 `test_unknown_index_uses_unknown_agent_label`。

## Coherence

- D1 选 shim（非过滤 null-id）✓；D2 修在 `base.run` 非 collector ✓；D3 复用 `_maybe_alert_dlq_growth` 模板 + dedup、仅可见性不自动重启 ✓。
- Design Doc frontmatter（comet_change/role/canonical_spec）齐备；delta spec 与 design doc 无矛盾（filter/auto-restart 均为 design doc Open Questions 中明确 deferred 项，spec 未要求）。
- Pattern 一致：局部 `import traceback`（与既有 loop 写法一致）；新告警镜像既有 DLQ 告警；测试置仓库根（多数测试约定）。

## Issues

- **CRITICAL**: 无。
- **WARNING**: 无。
- **备注**: tasks 4.3「重启 `run_agents.py` 运行期确认」属部署动作，未在 live 实例执行；spec scenarios 已由代码 + 单测 + 真实 `load_markets` 复现覆盖，运行期确认交用户部署时执行（非 spec 缺口）。

## Final Assessment

**All checks passed — ready for archive.** 无 CRITICAL/WARNING。
