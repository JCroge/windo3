# Tasks

## 1. ccxt keysort 容 None shim (exchange-client-resilience)
- [x] 1.1 新增 `utils/ccxt_compat.py`：覆写 `ccxt.Exchange.keysort`，用 `key=lambda kv: (kv[0] is not None, str(kv[0]))` 排序（None 排首），安装一次（模块级幂等）
- [x] 1.2 `utils/exchange_factory.py` 顶部 `import utils.ccxt_compat`（确保任何 `create_exchange` 前 shim 已装）
- [x] 1.3 单测：`keysort({None: x, "a": y})` 不抛且 None 排首；全 str 键顺序与原 ccxt 一致；构造含 `id=None` 的 mock markets 走 `set_markets` 不抛

## 2. base.run() setup 失败不再静默 (agent-fault-visibility)
- [x] 2.1 `agents/base.py:run()` 把 `await self.setup()` 包 `try/except`，`logger.critical(f"Agent [{name}] setup 失败" + traceback.format_exc())` 后 `raise`
- [x] 2.2 单测：setup 抛异常 → 记录 CRITICAL 含 traceback 且异常重抛；正常 setup 不记录、继续进入 loops

## 3. orchestrator 失败任务主动告警 (agent-fault-visibility)
- [x] 3.1 抽出 `_collect_task_stats()`（纯函数 seam）收集 `(agent_name, repr(exc))`（按 index 映射 `all_agents`，越界用 `unknown-agent`）；`_write_agent_health` 复用
- [x] 3.2 新增 `_maybe_alert_task_failure(failed)`：对未告警过的失败任务发 `telegram_alert {type:"agent_task_failed", agent, error}`，用 `_alerted_failed_tasks` set 去重；`agent_health.json` schema 不变
- [x] 3.3 单测：失败任务发一次 alert、同一任务再 tick 不重发、未知 index 用 `unknown-agent` 仍发、cancelled 不计失败

## 4. 验证与收尾
- [ ] 4.1 复现脚本（create_exchange + load_markets，真实 OKX）现在返回成功、markets>0
- [ ] 4.2 全量 `python3 -m pytest -q` 通过（基线 1088 + 本次新增用例）
- [ ] 4.3 重启 `run_agents.py`：`data_collector` 打出"9维度数据采集就绪"+`[采集]`，`tasks_failed=0`，Judge 恢复产出决策（live/paper 可开仓）
