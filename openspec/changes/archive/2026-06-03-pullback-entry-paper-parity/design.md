## Context

**Current State**：

```
trade_decision (action=open_short, plan.order_type=limit, entry_zone=[0.4043, 0.4047],
                limit_timeout_sec=1800, limit_no_fallback=True)
        │
        ├─→ MultiExecutor (live)
        │     └─→ executor._execute_limit_order
        │           ├─ 等 1800s 内 fill ✓ → return (amount, price, id)
        │           └─ 超时 + no_fallback=True → cancel_order + return None
        │                  ↓
        │           [Pullback] 未成交 不做市价fallback (root logger)
        │                  ↓
        │           _enqueue_drift_alert('pullback_unfilled', ...)
        │                  ↓
        │           risk_alert bus event
        │                  ↓
        │           TG _handle_risk_alert → ❌ 不在 critical_types，不推送
        │
        └─→ PaperExecutor
              └─→ _open_paper
                    └─ self._latest_price[symbol] 立刻成交 ✗
                       忽略 plan.order_type / entry_zone / limit_timeout_sec
```

**问题边界**：paper executor 缺 limit 撮合建模 + TG critical_types 缺 alert 路由。两件事同源（都在 06-02 引入 pullback policy 时被遗漏）。

**Constraints**：
- CLAUDE.md 红线：`paper_execution_result` 与 live `execution_result` 隔离，不能污染 live Reviewer 指标 — 不能让 paper 的 limit_unfilled 数据走进 trade_history.json
- `paper_executor` agent 只订阅 `trade_decision:* / price_tick:*`，与真实执行器零交互（`paper_executor.py:4`）— 实现不能依赖 live executor 的成交结果
- 单一函数收口：CLAUDE.md "保护单 owner 单一入口" 等条款都强调单点契约，本 change 也遵循

## Goals / Non-Goals

**Goals**：
- G1 paper 撮合行为与 live 同构：limit + 等待 + 命中/超时三态对齐
- G2 paper 在 `limit_no_fallback=True` 超时未成交时不开仓，并产出 `paper_unfilled` 风控告警
- G3 `pullback_unfilled` (live) 与 `paper_unfilled` (paper) 双向进入 TG critical_types，运维可见
- G4 paper_trades.jsonl / paper_positions.json 携带 `entry_method` 字段，便于后续做 idealized vs realistic 对比（即便本 change 不引入双轨）

**Non-Goals**：
- NG1 不调 `PULLBACK_ATR_ENTRY_TYPES`（issue #2，留给后续 change）
- NG2 不调 `PULLBACK_LIMIT_TIMEOUT_SEC` 数值（issue #4）
- NG3 不引入 paper 双轨模拟（idealized + realistic 并行）
- NG4 不修改 reviewer 对 paper 数据的处理 — 当前 reviewer 不消费 paper_execution_result，本 change 也不让它消费
- NG5 不模拟订单簿、不算滑点、不做 partial fill — paper 仅做"是否触达 entry_zone"判断，命中价格用区间中点

## Decisions

### D1：paper limit 等待用 tick 流被动判定，不用 sleep+poll

**Decision**：`_wait_paper_limit_fill` 不主动等待时间窗口，而是通过 `price_tick` 订阅触发 — 收到 tick 时检查所有 pending limit 是否触达。等待中的 limit 单存到 `_pending_limits[symbol]`，超时由 cleanup loop 处理。

**Why**：
- paper executor 是 asyncio 协作式，主动 sleep 30 分钟会阻塞其他消息处理
- price_tick 已经是订阅源（`paper_executor.py:30`），复用最自然
- 触达判定可用 `min(low) <= tick_price <= max(high)` 简单实现

**Alternative considered**：
- A. `asyncio.create_task(asyncio.sleep(...))` — 复杂、易泄漏 task、测试不好写
- B. 同步 `time.sleep` — 阻塞整个 paper executor，PASS

### D2：超时未命中时是否成交，由 `limit_no_fallback` 单字段决定

**Decision**：与 live `executor.py:2491-2499` 完全对称：
- `limit_no_fallback=True` → 拒单，记 `entry_method=limit_unfilled` 到 paper_trades.jsonl，发 `paper_unfilled` risk_alert
- `limit_no_fallback=False` → 用最新 tick 价 market 成交，记 `entry_method=market` (fallback 路径与初始 market 单走同一字段)

**Why**：
- 如果 paper 用不同语义，违背"与 live 同构"目标
- 单字段决策易测试、易理解

**Alternative considered**：
- A. paper 永远立成交（保留旧行为做 idealized baseline）— NG3 已排除
- B. paper 永远等到超时不 fallback — 违背 G1

### D3：`paper_unfilled` 与 `pullback_unfilled` 同时进入 TG critical_types，但作为不同 alert type

**Decision**：在 `telegram_notifier.py:207-220` `critical_types` 集合加 2 个字符串 (`pullback_unfilled`, `paper_unfilled`)；`_handle_risk_alert` 的 source 字段区分 paper/live，文案前缀分别用 `[实盘]` / `[模拟]`。

**Why**：
- 用户明确表态需要看见限价未成交事件
- paper/live 区分是 CLAUDE.md 红线，不能合并成单一 alert

**Alternative considered**：
- A. 共用 `pullback_unfilled` type 用 source 字段区分 — 容易与 live 数据混淆
- B. 仅 live 进 critical，paper 不告警 — 违背 G3

### D4：`entry_method` 字段加在 paper_trades.jsonl，旧记录无字段时 fail-safe 默认 `market`

**Decision**：reviewer / 后续分析工具读 `entry_method` 时，缺失视为 `market` (06-02 之前的行为)，避免 backfill。

**Why**：
- 避免一次性改动太大
- paper_trades.jsonl 是 append-only，历史不动

### D5：root executor 的 `[Pullback]` 日志透传到 Agent 层 logger

**Decision**：`agents/trading/executor.py` 的 `_handle_drift_alert` (或等价点) 在收到 `pullback_unfilled` drift_alert 时，调 agent logger 写一行 `[Pullback] {symbol} {side} 限价未成交（live）`，使 `agent_executor_*.log` 可见此事件。

**Why**：
- root logger 与 agent logger 是两个文件，运维只看 agent 日志会漏事件
- 不改 root executor 自己的日志（保持单点真相）

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| paper tick 流可能稀疏 (latest_price 慢更新)，导致 entry_zone touch 检测漏判 | 用 `_latest_price` 在 `on_price_tick` handler 中实时更新 + 等待循环里同时检查最近 N 个 tick；测试覆盖"价格瞬时穿越 entry_zone 后离开"case |
| `limit_no_fallback=False` 路径在 live 几乎不走（pullback policy 总是 True），paper 这分支可能成为 dead code | 测试明确覆盖该分支；review 该路径在 paper 是否有现实意义（保留以备将来非 pullback 限价单使用） |
| `entry_method=limit_unfilled` 的 paper 记录如果误进 reviewer，会污染 live 指标 | reviewer 当前不订阅 paper_execution_result（已验证 grep 0 hits）；本 change 不引入新订阅；测试加守卫 case 验证 reviewer 不消费 paper_unfilled |
| `paper_unfilled` 与 `pullback_unfilled` alert 风暴：每次 ma_aligned 信号都触发两条告警 | 实测频率：昨夜 16h 仅 1 次；如未来频率上升，可在 TG 加 60s 去重窗口（与 `pnl_resolved` 一致）—— 留作 follow-up，不在本 change |
| paper limit 的中点成交价与 live 实际 fill price 仍有偏差（paper 不模拟订单簿） | 在 D 文档明确 NG5；后续若需更精确，引入双轨模拟 (NG3) |

## Migration Plan

1. 部署不需要状态迁移：`paper_positions.json` / `paper_trades.jsonl` 旧记录无 `entry_method` 字段，下游 fail-safe 默认 `market`
2. 部署后第一笔 ma_aligned / momentum_probe_long 信号触发即可观察 paper 行为
3. Rollback：还原 paper_executor.py + telegram_notifier.py 即可，pending_limits 内存状态丢失但不影响下次正常 trade_decision

## Open Questions

- Q1: paper 的 30 分钟等待期间，如果同一 symbol 又来一个新的 trade_decision (open_long / close)，应该怎样处理？  
  **Tentative**：limit 等待期间 `_positions[symbol]` 还没建立，新的 open 应该被 `_open_paper` 头部"已有 pending limit" 检查拦截（视作 "open while pending" 等价于 "open while position exists"）。具体逻辑在 build 阶段确定。
- Q2: pending limit 在 paper executor 重启后是否需要持久化？  
  **Tentative**：不持久化。重启时 pending limit 丢失等价于"该笔信号在 paper 永远未成交"，对策略验证无害（live 也不会因为 paper 状态恢复 limit）。明确写到 design.md。
