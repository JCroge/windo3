## 1. tick_capture.py — retention 参数 + throttled prune

- [x] 1.1 写失败测试 `tests/test_tick_capture_prune.py`：超期行（open_time < now-retention）被删、界内行保留
- [x] 1.2 加 perturb 用例：节流——`prune_every` 次写入内不 prune，达阈值才 prune（计数断言）
- [x] 1.3 加 fail-safe 用例：prune 内部异常（如 db_path 指向损坏/只读）不抛出、不中断 record_bar
- [x] 1.4 `OneSecBarStore.__init__` 加 `retention_days=30` + `prune_every=2000` + `self._writes_since_prune=0`
- [x] 1.5 加 `_maybe_prune()`：`cutoff=int((time.time()-retention_days*86400)*1000)`；`DELETE FROM klines WHERE open_time < cutoff`；整段 try/except fail-safe（logger.warning + drop_count）
- [x] 1.6 `record_bar` 末尾 throttled 触发 `_maybe_prune`
- [x] 1.7 跑 `pytest tests/test_tick_capture_prune.py -q` 全过

## 2. collector 构造点铺 retention

- [x] 2.1 `agents/trading/multi_data_collector.py` OneSecBarStore 构造加 `retention_days=config.get('tick_capture_retention_days', 30)`

## 3. 验证与回归

- [x] 3.1 红线守卫 `pytest tests/test_cf_red_line_guard.py -q` 不回归
- [x] 3.2 编译 `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q utils/tick_capture.py agents/trading/multi_data_collector.py`
- [x] 3.3 全量 `python3 -m pytest -q`，基线在 1234 之上 + 新用例，无回归
