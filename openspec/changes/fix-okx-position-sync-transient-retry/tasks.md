# Tasks

## 1. fetch_positions 瞬时重试 (position-sync-resilience)
- [x] 1.1 `executor.py` 新增模块常量 `_POS_SYNC_RETRY_ATTEMPTS = 3` / `_POS_SYNC_RETRY_BACKOFFS = (0.5, 1.0)` + 私有方法 `_fetch_positions_with_retry()`：循环 attempts，捕获 `ccxt.NetworkError` 记 WARNING（带 `type(e).__name__`）+ `time.sleep` 退避；最后一次失败 `raise`；成功即返回
- [x] 1.2 `sync_positions()` 第 2639 行改调 `self._fetch_positions_with_retry()`；外层 `except` 的 ERROR 改为 `f"仓位同步失败: {type(e).__name__}: {e}"`（终态行为不变：保留本地持仓 / `_last_sync_result=[]` / 返回 copy）
- [x] 1.3 单测 `test_position_sync_retry.py`（mock exchange）4 case PASS：
  - 瞬时错误后成功 → WARNING 记录、无 ERROR、sync 正常完成
  - 瞬时错误耗尽 → 抛出 → 外层记一条 ERROR（含类型）、本地持仓保留
  - 非瞬时异常（AuthenticationError）→ 不重试、立即一条 ERROR

## 2. 验证与收尾
- [x] 2.1 全量 `python3 -m pytest -q` 通过 —— 实测 `1102 passed / 4 deselected / 1 warning`（基线 1098 + 本次 4）
- [x] 2.2 编译检查 `python3 -m compileall -q executor.py` 通过
