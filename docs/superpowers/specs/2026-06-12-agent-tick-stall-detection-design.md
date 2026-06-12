---
comet_change: agent-tick-stall-detection
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-12-agent-tick-stall-detection
status: final
---

# Agent Tick-Loop Stall Detection — Design

> 本文是技术设计（HOW）；权威 spec（WHAT）以 OpenSpec change `agent-tick-stall-detection` 的 delta + 归档后的 `openspec/specs/agent-health-supervisor/spec.md` 为准。

- **日期**：2026-06-12
- **change**：`agent-tick-stall-detection`（comet full workflow）
- **capability**：MODIFIED `agent-health-supervisor`（#95 延伸）
- **基线**：1135 passed
- **性质**：observability-only write-only，零决策路径，不需 event_backtest（沿用 #95 红线）。

## 1. 背景

#95 Agent Health Supervisor 的 loop-alive 维度用 `BaseAgent._last_alive_ts`（`_message_loop` 0.5s 有界轮询心跳）检测 **message 循环**卡死。每个 agent 还有第二条独立 asyncio 循环 `_periodic_loop`（`while: await self.tick()`），**当前无心跳**。

tick() 挂死（`await` 阻塞在无超时的网络/锁上）**不 starve 事件循环**——其它 agent 的 message loop 仍转，`_last_alive_ts` 仍刷新——所以 #95 看不见 tick 卡死。这是真实失效模式：ReviewerAgent 停止复盘、MultiDataCollector 停止采集，但 `/status`/`/health` 仍显示该 agent 健康。

## 2. 关键探索结论：单次 tick 健康时长有界

探索全部 16 agent 的 tick() 实现（见 proposal）：

- 研判层 6 agent **不 override tick**（默认 1s）；4h 节奏由 orchestrator `_research_loop` 发 `research_trigger` 驱动，on_message 消费，**不阻塞 tick**。
- 周期 agent 的 sleep 在 tick 开头/全体，单次 tick 健康时长：采集/持仓 ~1s（sleep 1s + 计数器分频）、executor ~5s、TG ~5-10s、riskguard ~10s、paper ~30s、**reviewer ~60s（最长）**。

**没有任何 agent 单次 tick 健康执行超过 60s**（3600s 复评、1800s 新闻都是 1s tick + 计数器，不是单次 tick sleep 那么久）。

⇒ 扁平阈值 120s（2× 最长）零误报，**无需 per-agent 配置**——与 #95 message-loop 心跳锚定 0.5s 轮询同样的优雅性，只是这次锚定"最长健康单次 tick 60s"。

## 3. 方案

### 3.1 埋点（base.py）

`_periodic_loop` 在 tick 前后盖戳（+2 实例字段，零业务侵入）：

```python
async def _periodic_loop(self):
    while self._running and not self._should_stop:
        try:
            self._tick_enter_ts = time.time()
            await self.tick()
            self._tick_exit_ts = time.time()
        except asyncio.CancelledError:
            break
        except Exception as e:
            ... # 现有 except 不变；exit 不在异常路径盖（tick 异常退出不算"完成一次健康 tick"，下轮 enter 会刷新）
```

> 注：`_tick_exit_ts` 只在 tick 正常返回后盖。tick 抛异常时 enter 已盖但 exit 未盖 → 短暂"mid-tick"，但下一轮 loop 立刻重盖 enter（异常路径 sleep 1s 后继续），不会误判（除非异常本身 >120s，那是真问题）。

### 3.2 检测（health_snapshot.py `_loop_health` 扩展）

```python
正在 tick 中 = enter > exit
tick 挂死    = enter > 0 AND 正在 tick 中 AND (now - enter) > tick_stall_timeout_sec
```

`loop_health` 新增 `tick_stalled_count` + `tick_stalled: [{name, tick_sec}]`。`build_health_snapshot` 新增 `tick_stall_timeout_sec` 参数。

### 3.3 配置

| 参数 | 默认 | HARD | 含义 |
|---|---|---|---|
| `AGENT_TICK_STALL_TIMEOUT_SEC` | 120 | [30,3600] | 当前 tick 执行超过此值算挂死 |

### 3.4 告警与展示（关键决策：并入 loop 维度）

- loop 维度 unhealthy 判定扩展：`stalled_count > 0 OR tick_stalled_count > 0`。
- 复用 #95 已建的边沿告警状态机（`_maybe_alert_health_transitions`），**不新增第 5 维度**。
- 告警 message 与 `/health` 明细**区分** message-loop 卡死（`_last_alive_ts`）vs tick 卡死（`_tick_enter_ts`）——ops 动作不同（前者事件循环级，后者单 agent 周期工作级）。
- `/status` 总括的 loop stall 计数把 tick 卡死也计入。

**为何并入而非单列**：tick 卡死本质是"agent loop 健康"问题，归 loop 维度语义自然；复用状态机/展示避免分叉；`/status` 总括保持简洁。代价是 loop 维度内要区分两种 stall，靠 detail 文案解决。

## 4. 红线遵循

- observability-only write-only：tick-stall 只进 snapshot/告警/展示，严禁 gate/veto/halt/rank 读取。
- 不改各 agent tick() 实现（不重构 sleep 位置）；纯外层埋点。
- 不动 message-loop 心跳逻辑。
- 不需 event_backtest 同构。

## 5. 测试矩阵

| 测试 | 覆盖 |
|---|---|
| `test_base_agent_heartbeat.py` | tick 前盖 enter、后盖 exit；正常 tick 后 exit>=enter；mid-tick 时 enter>exit |
| `test_health_snapshot.py` | tick-stall 检出 / 边界相等不算（严格 >）/ mid-tick 未超时不算 / between-ticks 不算 / 未起跑跳过 / 配置阈值存在 |
| `test_health_alert_transitions.py` | tick-stall 触发 loop 边沿告警；message vs tick detail 区分 |
| `test_health_telegram_display.py` | `/health` 明细列 tick 卡死 agent；`/status` 总括计入 |

## 6. 落地文件

| 文件 | 改动 |
|---|---|
| `agents/base.py` | +2 字段 + `_periodic_loop` 盖戳 |
| `utils/health_snapshot.py` | `_loop_health` 加 tick-stall + builder 参数 |
| `utils/config_loader.py` | 1 阈值 |
| `agents/orchestrator.py` | loop unhealthy 判定 + 告警 detail 区分 + 传阈值 |
| `agents/trading/telegram_notifier.py` | `/health` 明细 + `/status` 计入 |
| tests | 4 文件扩展 |
