## Why

`utils/tick_capture.py::OneSecBarStore` 只有 `record_bar` 插入，**无 prune 方法、构造也不收 retention 参数**；config `tick_capture_retention_days`（默认 30，range 1-3650，env `TICK_CAPTURE_RETENTION_DAYS`，见 `config_loader.py:81/191/332`）**已存在但从未被任何代码使用**。后果：`klines_1s.db` 无界增长（实测 ~3MB/天）。

这同时是 `tick-snapshot-capture` capability「tick 路径与 retention 受控」requirement 的实现缺口——spec 声明"支持 retention"，但实现从未滚动清理。这是反事实实验室 L1 地基的已知遗留边界。

## What Changes

- `OneSecBarStore.__init__` 新增 `retention_days=30` + `prune_every=2000` 参数。
- 新增 `_maybe_prune()`：`DELETE FROM klines WHERE open_time < cutoff_ms`（cutoff = `(time.time() - retention_days*86400)*1000`），fail-safe（异常仅 log，绝不抛进采集路径）。
- `record_bar` 内 throttled 触发：每 `prune_every` 次写入调一次 `_maybe_prune`（热路径廉价，镜像 `DecisionTape` 的 `prune_every` 模式）。
- `agents/trading/multi_data_collector.py` 构造点把 `retention_days=config.get('tick_capture_retention_days', 30)` 铺进去。

非目标：不改 `klines` 表 schema（仅 DELETE 旧行）；不改 retention 默认值（30 天）；不动任何决策路径。

## Capabilities

### New Capabilities
（无）

### Modified Capabilities
- `tick-snapshot-capture`: 「tick 路径与 retention 受控」requirement 补一条 retention 实际滚动清理的验收场景（此前只声明"支持 retention"，无清理行为契约）。

## Impact

- 代码：`utils/tick_capture.py`（retention 参数 + `_maybe_prune` + throttled 调用）、`agents/trading/multi_data_collector.py`（构造点铺 retention_days）。
- 测试：新增 prune 测试（超期行被删、界内行保留、节流计数、fail-safe）。
- 运行：observability-only write-only，零决策路径变化；`klines_1s.db` 改为有界（默认 30 天 ≈ ~90MB 上限）。
- 红线：`tests/test_cf_red_line_guard.py` 不回归（klines_1s 仍严禁决策读取）。
