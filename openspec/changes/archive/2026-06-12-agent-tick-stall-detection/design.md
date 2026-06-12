# Design — Agent Tick-Loop Stall Detection

> 高层决策见此；完整技术 RFC 见 Superpowers Design Doc（comet-design 阶段产出）。本 change 修改既有 capability `agent-health-supervisor`（#95）。

## 问题与现状

每个 BaseAgent 有两条独立 asyncio 循环：
- `_message_loop`：`bus.receive(timeout=0.5)` 0.5s 有界轮询 → `_last_alive_ts` 心跳（#95 已覆盖）。
- `_periodic_loop`：`while: await self.tick()` → **当前无心跳**。

tick() 挂死（`await` 在无超时的网络/锁上）不会 starve 事件循环（其它 agent 的 message loop 仍转 → `_last_alive_ts` 仍刷新），所以 #95 的 loop-alive 检测**看不见 tick 卡死**。

## 关键探索结论：单次 tick 健康时长有界

| agent | 单次 tick 健康时长 | 节奏来源 |
|---|---|---|
| 研判层 6 | 不 override tick（默认 1s） | orchestrator `_research_loop` 发 trigger，on_message 驱动 |
| MultiDataCollector / PositionAnalyst | ~1s | sleep 1s + 计数器分频（30s/300s/1800s/3600s） |
| MultiExecutor | ~5s | sleep 5s |
| TelegramNotifier | ~5-10s | poll + 5s timeout |
| PortfolioRiskGuard | ~10s | sleep 10s |
| PaperExecutor | ~30s | sleep 30s |
| **ReviewerAgent** | **~60s** | 60s 纯 sleep（最长） |

**没有任何 agent 单次 tick 健康执行超过 60s。** 因此扁平阈值 120s（2× 最长）零误报。

## 方案：测量"当前 tick 已执行多久"

```
BaseAgent._periodic_loop:
  while running:
    self._tick_enter_ts = time.time()   # tick 前
    await self.tick()
    self._tick_exit_ts = time.time()    # tick 后

supervisor (health_snapshot._loop_health 扩展):
  正在 tick 中 = _tick_enter_ts > _tick_exit_ts
  tick 挂死    = 正在 tick 中 AND (now - _tick_enter_ts) > AGENT_TICK_STALL_TIMEOUT_SEC(120)
```

为何不误报：健康 agent 的 `_tick_enter_ts` 在每次 tick 开始刷新；单次 tick ≤60s 后 `_tick_exit_ts` 追上，回到"不在 tick 中"。只有真卡死（tick 执行 >120s）才命中。`_tick_enter_ts <= 0`（未起跑）跳过。

## 关键决策

1. **扁平阈值，无 per-agent 配置**（锚定"最长健康单次 tick 60s"，与 #95 message-loop 锚定 0.5s 轮询同理）。

2. **并入 loop_health 维度（不单列第 5 维度）**：`loop_health` 加 `tick_stalled_count` / `tick_stalled`；loop 维度 unhealthy 判定扩展为 `stalled_count > 0 OR tick_stalled_count > 0`。复用 #95 已建的边沿告警状态机与 `/status`/`/health`，告警 detail 与 `/health` 明细**区分** message-loop 卡死 vs tick 卡死（ops 动作不同）。理由：tick 卡死本质是 agent loop 健康问题，归 loop 维度自然；避免新增维度的状态机/展示分叉。

3. **observability-only write-only**：沿用 #95 红线，严禁 gate/veto/halt；不需 event_backtest。

4. **不重构 agent tick()**：不动各 agent 的 sleep 位置/实现，纯外层埋点。

## 配置

| 参数 | 默认 | HARD | 含义 |
|---|---|---|---|
| `AGENT_TICK_STALL_TIMEOUT_SEC` | 120 | [30, 3600] | 当前 tick 执行超过此值算挂死（2× 最长健康单次 tick 60s） |
