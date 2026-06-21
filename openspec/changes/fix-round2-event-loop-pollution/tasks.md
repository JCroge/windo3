# Tasks: fix-round2-event-loop-pollution

- [x] 1. `test_round2_probe_long_dispatcher.py`：4 个测试方法改 `async def` + `await coro`，清理 `import asyncio`（保留 `import time`）；隔离单跑确认 4 PASS
- [x] 2. `test_round2_request_id_position.py`：4 个测试方法改 `async def` + `await coro`，清理 `import asyncio`；隔离单跑确认 4 PASS
- [x] 3. 全量回归验收：`1359 → 1367 passed / 0 failed`（8 个 round2 转绿），零新退化
