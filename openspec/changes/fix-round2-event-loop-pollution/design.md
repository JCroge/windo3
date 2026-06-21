# Design: fix-round2-event-loop-pollution

## 修复方案（单方案，沿用已确立范式）

对两个文件 `test_round2_probe_long_dispatcher.py`、`test_round2_request_id_position.py` 的每个测试方法做机械转换：

| 改前 | 改后 |
|------|------|
| `def test_x(self):` | `async def test_x(self):` |
| `result = asyncio.get_event_loop().run_until_complete(coro)` | `result = await coro` |
| `asyncio.get_event_loop().run_until_complete(coro)`（无返回值用） | `await coro` |
| 顶部 `import asyncio`（若改后不再被引用） | 删除 |

`pytest.ini` 的 `asyncio_mode = auto` 使 pytest-asyncio 自动识别 `async def test_*` 并为其提供**独立、隔离**的事件循环，不再依赖/争用线程全局 loop，从根上消除 `get_event_loop()` 抛错。

## 为什么不选其他方案

- **`asyncio.run(coro)` 替代**：每次新建+关闭 loop 虽也可行，但偏离项目既有范式（reviewer-symbol 用 async def），一致性差，且在已有 `asyncio_mode=auto` 环境下多此一举。
- **改 conftest 强制重置 loop**：扩大改动面、引入全局副作用，违背 hotfix"单点修复"原则。

## 注意点（保真）

- `test_round2_probe_long_dispatcher.py` 仍需保留 `import time`（`test_probe_long_gate_blocks_when_pending` 用 `time.time()`），仅清理 `import asyncio`。
- 断言、mock、`_make_judge()` / `_make_executor_agent()` 工厂、被测入口调用全部**不变**——只改协程的驱动方式，不改测试语义。
- 类内 async 方法：pytest-asyncio auto 模式对 `class Test*` 下的 `async def test_*` 同样托管，无需加 `@pytest.mark.asyncio` 装饰器（与先例一致）。
