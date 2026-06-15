## Context

`OneSecBarStore` 持续写 1s bar 到 `klines_1s.db` 但从不清理，config `tick_capture_retention_days` 形同虚设。修复镜像同仓库已验证的 `utils/decision_tape.py::DecisionTape` 节流 prune 模式，保持一致性。observability-only：klines_1s 严禁交易决策读取（红线 `tests/test_cf_red_line_guard.py`），本修复零决策路径变化。

## Goals / Non-Goals

**Goals**：klines_1s.db 有界（按 retention_days 滚动清理）；接通已有 config；fail-safe 不中断采集。
**Non-Goals**：不改表 schema；不改默认值；不引入新依赖；不动决策路径。

## Decision: 镜像 DecisionTape 的 throttled prune

`record_bar` 是热路径（每 symbol 每秒一次）。每次写都 prune 会让 DELETE 查询淹没采集。沿用 `DecisionTape` 的 `prune_every` 节流：

- `__init__(db_path, enabled=True, retention_days=30, prune_every=2000)`，新增 `self._writes_since_prune = 0`。
- `record_bar` 末尾：`self._writes_since_prune += 1; if self._writes_since_prune >= self.prune_every: self._writes_since_prune = 0; self._maybe_prune()`。
- `_maybe_prune()`：`cutoff = int((time.time() - self.retention_days*86400)*1000)`；`DELETE FROM klines WHERE open_time < cutoff`；整段 try/except，异常仅 `logger.warning` + `drop_count`，绝不抛出。

`prune_every=2000`：多 symbol 合计每秒 ~数条到十几条写入，2000 次约对应几分钟到十几分钟一次 prune，DELETE 廉价（带 `idx_sit` 索引，按 open_time 范围删）。

**替代方案**：独立定时任务/后台线程 prune —— 过重，引入并发与生命周期复杂度；record_bar 内联节流已足够且与 DecisionTape 一致。

## Decision: wall-clock cutoff

1s bar 实时写入，`open_time ≈ now`，故按 `time.time()` 裁剪正确。不依赖数据自身最大时间戳（避免空库/时钟回拨的边界）。

## Risks / Trade-offs

- **[prune 误删近期数据]** → cutoff 用 retention_days 明确换算，测试覆盖"界内行保留 / 超期行删除"边界。
- **[prune 异常中断采集]** → 整段 fail-safe，与现有 `record_bar` drop_count 一致；prune 失败只是暂不清理，不影响写入。
- **[throttle 导致短时超额]** → 最多多存 `prune_every` 次写入对应的时间窗，量级可忽略（MB 级）。

## Migration

改 2 个文件 + 测试 → pytest + 红线守卫通过 → 合并。已运行的 live 进程下次写满 prune_every 即开始清理；无需手动干预、无数据迁移。回滚：revert 即恢复无界（行为退化但不破坏）。
