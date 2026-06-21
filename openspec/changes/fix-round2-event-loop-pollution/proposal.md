# Proposal: fix-round2-event-loop-pollution

## Why

全量回归长期挂 8 个失败，已稳定带过多个基线（1302→1359），是阻碍"全绿"信号的预先存在技术债。

失败用例（全部为 event-loop 跨测试污染，**非任何 change 引入**）：
- `test_round2_probe_long_dispatcher.py`（4 个：`TestProbeLongDispatcherGate`）
- `test_round2_request_id_position.py`（4 个：`TestDuplicateOpenRejected`）

症状：全量运行报 `RuntimeError: There is no current event loop in thread 'MainThread'`；**隔离单跑全 PASS**，base-ref 亦同批失败 → 确认是测试间状态污染，与被测产品代码无关。

## 根因

两个文件的每个测试方法用 **同步** 写法驱动协程：

```python
result = asyncio.get_event_loop().run_until_complete(judge._gate_and_publish_open(...))
```

运行环境为 Python 3.9。当全量运行中**前序 async 测试**（经 pytest-asyncio）创建并关闭了线程的事件循环、或将 current loop 置空后，`asyncio.get_event_loop()` 在主线程不再隐式新建循环，而是抛 `RuntimeError: no current event loop`。隔离单跑时没有前序污染，`get_event_loop()` 仍会隐式建循环，故能通过——这正是"隔离 PASS / 全量 FAIL"的来源。

## 修复目标

将这 8 个测试改为项目已确立的 async 测试范式（先例 commit `445d8e4` reviewer-symbol：测试改 `async def` 消除 loop 污染）：

- `pytest.ini` 已配置 `asyncio_mode = auto`，`pytest-asyncio 1.2.0` 已安装——async 测试由 pytest-asyncio 托管独立事件循环，不再共享/争用全局 loop。
- 每个 `def test_x` → `async def test_x`，`asyncio.get_event_loop().run_until_complete(coro)` → `await coro`，清理不再使用的 `import asyncio`。

## 范围与非目标

- **范围**：仅 2 个测试文件，无产品代码改动，无新 capability，无接口/架构变更，无 delta spec。
- **非目标**：不动被测的 `judge._gate_and_publish_open` / `executor._execute_decision` 逻辑（它们隔离单跑已证明正确）；不改 conftest / pytest.ini（配置已就绪）。

## 验收

全量回归从 `1359 passed / 8 failed` → `1367 passed / 0 failed`（8 个 round2 转绿），零新退化。
