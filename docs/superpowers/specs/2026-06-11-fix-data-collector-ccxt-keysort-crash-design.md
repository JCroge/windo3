---
comet_change: fix-data-collector-ccxt-keysort-crash
role: technical-design
canonical_spec: openspec
---

# Design: data_collector ccxt keysort 崩溃修复

> 需求（WHAT）以 OpenSpec 为准：`openspec/changes/fix-data-collector-ccxt-keysort-crash/`（proposal + 2 个 delta spec：`exchange-client-resilience`、`agent-fault-visibility`）。本文件只讲 HOW。

## 问题根因（已复现）

OKX `fetch_markets()` 当前返回 3860 条市场，其中 2 条 `id=None`/`symbol=None` 的畸形 `future`。ccxt `load_markets → set_markets → keysort` 执行 `dict(sorted(markets_by_id.items()))`，Python3 无法比较 `None < str` → `TypeError`。

```
load_markets() → set_markets() → keysort(markets_by_id)
  ccxt/base/exchange.py:1064  dict(sorted(dictionary.items()))
  TypeError: '<' not supported between instances of 'NoneType' and 'str'
```

`MultiDataCollector.setup()` 第 51 行 `load_markets` 无 try/except → `collector.run()` 在 setup 即死（日志只有“启动”无“就绪”）。`Orchestrator._health_loop` 调 `task.exception()` 仅为计数 `tasks_failed`，同时把异常“取回”→ asyncio 不再打印 “Task exception was never retrieved” → **全程无 traceback**。后果：无 `market_data` → 无 `tech_analysis` → Judge 零决策 → live/paper 不开仓，每次重启必复现（今日 6 次重启：启动×6 / 就绪×0，`tasks_failed=1` 恒定）。

## 三处修复

### ① ccxt keysort 容 None shim — `utils/ccxt_compat.py`

新增模块，导入时（幂等）覆写 `ccxt.base.exchange.Exchange.keysort`：

```python
# 伪代码意图
def _safe_keysort(self, dictionary):
    return dict(sorted(dictionary.items(), key=lambda kv: (kv[0] is None, str(kv[0]))))
Exchange.keysort = _safe_keysort
```

`utils/exchange_factory.py` 顶部 `import utils.ccxt_compat`，保证任何 `create_exchange` 调用前 shim 已装。

**取舍**：选 shim 而非过滤 null-id 市场——通用、一处保护全部 4 个调用点（data_collector / market_scanner / judge / telegram_notifier）+ `OrderCapabilities` warmup + 未来任何 None 键；`markets_by_id` 是查找 dict，排序顺序与正确性无关，None 排首安全。不升级 ccxt（规避“ccxt 升级须 testnet 重验收”红线）。

**边界**：
- 必须 import-once 幂等（重复 import 不叠加 patch）。
- 全 str 键时排序结果与原 ccxt 一致（`(False, k)` 元组排序退化为按 `str(k)`）。
- patch 目标是基类 `Exchange.keysort`，okx/binance 子类都继承，一处生效。

### ② base.run() setup 不再静默 — `agents/base.py`

`run()` 把 `await self.setup()` 包裹：

```python
try:
    await self.setup()
except Exception:
    self.logger.critical(f"Agent [{self.name}] setup 失败\n{traceback.format_exc()}")
    raise
```

**取舍**：修在 base 而非 collector——静默死亡的根在通用 `run()`→setup 路径 + health-loop 取回异常的交互，base 层一处覆盖所有 agent。**re-raise 不吞**：吞掉会让 collector 无 markets、symbol 校验全废，比“响亮失败”更糟；re-raise 保留 `tasks_failed` 语义，但现在带 traceback。

### ③ orchestrator 失败任务告警（仅可见性）— `agents/orchestrator.py`

`_health_loop` 既有 task 扫描里，对 `done & not cancelled & exception() is not None` 的 task 收集 `(agent_name, repr(exc))`：按 index 映射 `all_agents`（`self._tasks[:len(all_agents)]` 与 `all_agents` 对齐，尾部是 research/cmd/health），越界 → `unknown-agent`。新增 `_maybe_alert_task_failure(failed)`，对未告警过的失败任务发 `telegram_alert`：

```python
{"level": "critical", "type": "agent_task_failed", "agent": <name>, "error": <repr>, "message": ...}
```

`_alerted_failed_tasks` set 去重（每个失败任务身份只告警一次，防 30s tick flap）。`agent_health.json` schema 不变。**仅可见性，不自动重启**（重启语义列为后续项）。

## 风险 / 缓解

| 风险 | 缓解 |
|---|---|
| monkeypatch vendored ccxt | 隔离在单文件 `ccxt_compat.py`，行为对非 None 键不变，删 import 即回滚 |
| task→agent index 映射漂移 | 越界 `unknown-agent` 兜底；映射逻辑紧贴 `_tasks` 构造处，改动同审 |
| 告警 flap 刷屏 | `_alerted_failed_tasks` dedup，每身份一次 |
| OKX 自行修好废市场 | shim 仍正确无害，作为永久守卫保留 |

## 测试策略

- **keysort 单测**（`exchange-client-resilience`）：`keysort({None:1,"a":2})` 不抛且 None 排首；全 str 键顺序与原 ccxt 一致；构造含 `id=None` 的 mock markets 走 `set_markets` 不抛。
- **base.run setup 单测**（`agent-fault-visibility`）：setup 抛异常 → `logger.critical` 含 traceback 且异常重抛；正常 setup 不打、进入 loops。
- **health_loop 告警单测**（`agent-fault-visibility`）：失败 task 发一次 `agent_task_failed`；同一任务再 tick 不重发；未知 index 用 `unknown-agent` 仍发。
- **端到端**：复现脚本（真实 OKX `create_exchange + load_markets`）现返回成功、markets>0；全量 `pytest`（基线 1088 + 新增）；重启 `run_agents.py` 验证 `data_collector` 出“9维度数据采集就绪”+`[采集]`、`tasks_failed=0`、Judge 恢复决策。

## 迁移 / 回滚

无状态/schema 迁移。新增唯一对外行为是 `agent_task_failed` 告警（additive）。回滚 = revert commit。

## 非目标

不升级 ccxt；不改交易策略/风控/Judge 逻辑；不自动重启死任务（visibility-first）；除新告警类型外不动消息契约。
