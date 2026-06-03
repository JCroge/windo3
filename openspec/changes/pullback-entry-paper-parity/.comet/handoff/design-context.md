# Comet Design Handoff

- Change: pullback-entry-paper-parity
- Phase: design
- Mode: compact
- Context hash: 05ac2c3c8e9e195f65a0e446dd4adb24303103ebc3a60cad2b6ab01545181f13

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/pullback-entry-paper-parity/proposal.md

- Source: openspec/changes/pullback-entry-paper-parity/proposal.md
- Lines: 1-36
- SHA256: e8146972a95ac94e5be46c4a45b6cd129252ff575771b6798ea4777abdefad0a

```md
## Why

06-02 commit `f512d1a` 引入 Pullback ATR Entry Policy（Judge 在 `entry_type ∈ {ma_aligned, momentum_probe_long}` 时把 plan 改写为 ±0.05% 窄区间 + 30 分钟 limit + `limit_no_fallback=True`），但 Paper Executor 没有同步建模 limit 撮合：

- `agents/trading/paper_executor.py:152-209` `_open_paper` 直接用 `self._latest_price` 立刻成交，完全忽略 `plan.order_type / entry_zone / limit_timeout_sec / limit_no_fallback`
- 同时 `pullback_unfilled` risk_alert 不在 `agents/trading/telegram_notifier.py:207-220` 的 `critical_types` 集合，运维看不见限价未成交事件

直接证据：06-03 08:16 WLD-USDT short 单 Paper @0.3815 立刻成交并产生 -8.02 USDT 浮亏，Live 在 0.4045 ±0.05% 区间死等 30 分钟未触达后 silent rejected。同一笔 trade_decision，paper 和 live 行为完全脱钩 → Reviewer 拿到的 paper 数据无法用于策略验证 + 运维感知不到 live 限价被绕过。

CLAUDE.md 红线"`paper_execution_result` 与 live `execution_result` 隔离，不能污染 live Reviewer 指标"仍然有效；本 change 在不打破隔离的前提下，让 paper 撮合行为与 live 同构。

## What Changes

- 新增 `paper_executor._wait_paper_limit_fill()` 单一入口：读 `plan.order_type / entry_zone / limit_timeout_sec / limit_no_fallback`，用 `latest_price` tick 流判断超时窗口内是否 touch 过 entry_zone
- `_open_paper` 在 `order_type == 'limit'` 时改走 `_wait_paper_limit_fill`：命中则在 entry_zone 中点成交；未命中且 `limit_no_fallback=True` → 记 `paper_unfilled` 拒单不开仓；未命中且 `limit_no_fallback=False` → 用最新价 market 成交（与 live `executor.py:2548-2553` fallback 对齐）
- `paper_trades.jsonl` / `paper_positions.json` 新增 `entry_method ∈ {market, limit_filled, limit_unfilled}` 字段
- Paper Executor 在 `paper_unfilled` 时通过 message bus 发 `risk_alert{type='paper_unfilled', source='paper_executor'}`（与 live `pullback_unfilled` topic 同名但 source 区分）
- Telegram `critical_types` 加入 `pullback_unfilled` 和 `paper_unfilled`，保证运维可见
- Root `executor.py:2492` 的 `[Pullback] ... 不做市价fallback` 日志同步透传到 Agent 层 logger（agent_executor.log 当前看不到）

## Capabilities

### New Capabilities
- `paper-executor`: paper 影子账户撮合、风险拦截、独立账本与告警；本次新增的内容是 limit 撮合模拟（等待 entry_zone 命中、超时拒单 / market fallback）以及 `entry_method` 账本字段
- `risk-alert-routing`: Telegram `_handle_risk_alert` 的 `critical_types` 集合与 paper/live 区分路由；本次新增 `pullback_unfilled` / `paper_unfilled` 两个 alert type 的 critical 路由

### Modified Capabilities
- 无（两个 capability 都是新建，避免触动 `protective-sl-owner-tag` / `entry-drift-policy` 等既有 spec）

## Impact

- 代码：`agents/trading/paper_executor.py`（新增 limit 等待逻辑、entry_method 字段）、`agents/trading/telegram_notifier.py`（critical_types 扩展）、`executor.py`（pullback_unfilled 日志透传，无行为变更）
- 测试：新增 `tests/test_paper_limit_fill.py`（~20 case）；不回归 `tests/test_pullback_atr_policy.py` / `tests/test_limit_no_fallback.py`
- 状态文件：`paper_trades.jsonl` / `paper_positions.json` 新增 `entry_method` 字段，旧记录无该字段时下游 fail-safe 默认为 `market`
- 消息总线：新增 `risk_alert{type='paper_unfilled'}`，订阅者只有 Telegram；不影响 Reviewer / RiskGuard / Judge
- 不影响：live execution_result.v2 schema、reviewer 对 paper 数据的处理（CLAUDE.md 红线保留）、pullback policy 触发面（`PULLBACK_ATR_ENTRY_TYPES`）、pullback timeout 数值（`PULLBACK_LIMIT_TIMEOUT_SEC`）
```

## openspec/changes/pullback-entry-paper-parity/design.md

- Source: openspec/changes/pullback-entry-paper-parity/design.md
- Lines: 1-128
- SHA256: e2032fa1de3d9083f622f192bf60421cbe61cef49b3dd2c7b92ba6e38722c882

[TRUNCATED]

```md
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

```

Full source: openspec/changes/pullback-entry-paper-parity/design.md

## openspec/changes/pullback-entry-paper-parity/tasks.md

- Source: openspec/changes/pullback-entry-paper-parity/tasks.md
- Lines: 1-91
- SHA256: c057d8e11a7f7b20995df576cf8af078c7fc998ab35d8da418c4cbf07455fcd3

[TRUNCATED]

```md
## 0. 依赖与配置

- [ ] 0.1 `requirements.txt` / `requirements.lock` 新增 `freezegun==1.5.1` 测试依赖
- [ ] 0.2 `paper_executor.py` 模块顶部新增常量 `DEFAULT_PAPER_LIMIT_TICK_STALENESS_SEC = 60`
- [ ] 0.3 `__init__` 读取 `config['paper_limit_tick_staleness_sec']` 到 `self._tick_staleness_sec`，缺省走 default
- [ ] 0.4 `utils/config_loader.py` `DEFAULTS` 字典新增 `"paper_limit_tick_staleness_sec": 60`；`_apply_env_overrides` ENV map 新增 `"PAPER_LIMIT_TICK_STALENESS_SEC": ("paper_limit_tick_staleness_sec", float)`；`.env.example` 在 paper 配置区段加注释（可选项，默认 60s）；`format_banner` 输出可选展示该字段
- [ ] 0.5 `VALID_RANGES`（如适用）新增 `"paper_limit_tick_staleness_sec": (1.0, 600.0)` 边界校验

## 1. Paper Executor 限价撮合骨架

- [ ] 1.1 在 `agents/trading/paper_executor.py` 增加 `self._pending_limits: Dict[str, dict]` 内存状态（key=symbol，value={created_at, deadline, side, action, plan, decision, entry_zone, last_tick_ts}）
- [ ] 1.2 修改 `_open_paper`：检测 `plan.order_type == 'limit'` 且 `entry_zone` 有效时，写入 `_pending_limits[symbol]` 而非立即成交；写入前检查 `_pending_limits` / `_positions` 重复
- [ ] 1.3 实现单一函数 `_wait_paper_limit_fill(symbol, tick_price)`：计算 `min(low) <= tick_price <= max(high)` 命中判定；命中则在 entry_zone 中点开仓，写 `entry_method='limit_filled'`，移出 `_pending_limits`；同时刷新 `last_tick_ts = time.time()`
- [ ] 1.4 在 `on_message[price_tick]` 现有分支末尾对 `_pending_limits[symbol]` 调用 `_wait_paper_limit_fill`（仅当 symbol 有 pending）
- [ ] 1.5 实现 `_scan_pending_limits()` 扫描所有 pending：超时（`now >= deadline`）走 timeout 分支；在 `tick()` 末尾调用（30s 周期）
- [ ] 1.6 timeout 分支按决策树分流（见 design TD-5）：no_fallback=True → 拒单 + `risk_alert{type='paper_unfilled'}`；no_fallback=False + tick fresh → market 成交 + log；no_fallback=False + tick stale/None → `paper_unfilled_no_tick` 拒单

## 2. Paper 账本字段扩展

- [ ] 2.1 `_open_paper` 立成交路径在 position 字典写入 `entry_method='market'`
- [ ] 2.2 `_wait_paper_limit_fill` 命中路径写入 `entry_method='limit_filled'`
- [ ] 2.3 timeout no_fallback 分支拒单记录写入 `_rejected_log` 含 `entry_method='limit_unfilled'`
- [ ] 2.4 timeout fallback 分支写入 `entry_method='market'`（与立成交路径同字段）
- [ ] 2.5 `_close_paper` 在 close 记录的 `paper_trades.jsonl` 行携带 `entry_method`（从 position 字典透传）
- [ ] 2.6 `paper_positions.json` 持久化时确保 `entry_method` 字段被保存

## 3. Trade Decision 重复保护

- [ ] 3.1 在 `_open_paper` 头部检查：若 `_pending_limits[symbol]` 已存在，记 info log 并 return（不开新单）
- [ ] 3.2 在收到 `action='close'` 且 `_pending_limits[symbol]` 存在时，移除 pending 并记 info log
- [ ] 3.3 `_open_paper` 持仓已存在分支保留原有跳过逻辑，新增对 pending 的相同保护

## 4. Pending Limits 不持久化

- [ ] 4.1 paper_executor 启动时不读取/重建 `_pending_limits`（保持 in-memory only）
- [ ] 4.2 优雅停机不持久化 `_pending_limits` 到磁盘（确认 save_state 路径不写入）
- [ ] 4.3 在 design.md Open Question Q2 标注为已解决

## 5. Telegram critical_types 扩展

- [ ] 5.1 `agents/trading/telegram_notifier.py:_handle_risk_alert` 的 `critical_types` 集合加入 `'pullback_unfilled'` 和 `'paper_unfilled'`
- [ ] 5.2 `_handle_risk_alert` 按 `payload.source` 区分 paper/live，paper 用 `[模拟]` 前缀、live 用 `[实盘]` 前缀（或与现有命名一致）
- [ ] 5.3 缺 source 字段时 fail-safe 默认 live 行为 + warning 日志
- [ ] 5.4 消息体携带 `symbol / side / entry_zone / request_id / timeout_sec`

## 6. Live alert source 字段一致性

- [ ] 6.1 检查 `executor.py:_enqueue_drift_alert('pullback_unfilled', ...)` 是否携带 `source` 字段；缺失则在 alert payload 构造点统一加入 `source='executor'`
- [ ] 6.2 paper_executor 发布 `paper_unfilled` 时显式带 `source='paper_executor'`

## 7. Pullback 日志透传到 agent_executor.log

- [ ] 7.1 `agents/trading/executor.py` 处理 drift alert 时在 agent logger 写一行 `[Pullback] {symbol} {side} 限价未成交（live）`，使 `agent_executor_*.log` 可见
- [ ] 7.2 不删除 root `executor.py:2492` 原有日志（保持单点真相）

## 8. 单元测试

- [ ] 8.1 新建 `tests/test_paper_limit_fill.py` 文件骨架，参考 `tests/test_pullback_atr_policy.py` 风格；引入 `from freezegun import freeze_time`
- [ ] 8.2 case: limit plan 进入 `_pending_limits` 不立即成交（覆盖 D1 + Req1 Scenario 1）
- [ ] 8.3 case: market plan 维持立成交，`entry_method='market'`（覆盖 Req1 Scenario 2）
- [ ] 8.4 case: limit plan + 缺 entry_zone 走 fail-safe market 成交（覆盖 Req1 Scenario 3）
- [ ] 8.5 case: tick 价进入 entry_zone 触发 fill at 中点 + `entry_method='limit_filled'`（覆盖 Req2 Scenario 1）
- [ ] 8.6 case: tick 瞬时穿越 entry_zone 仍判定成交（覆盖 Req2 Scenario 2）
- [ ] 8.7 case: tick 全程在 entry_zone 外，timeout no_fallback=True → `paper_unfilled` + `risk_alert` 发布（覆盖 Req2 Scenario 3 + Req3 Scenario 1）；用 `freeze_time` + `frozen.tick(seconds=1801)` 推进时间
- [ ] 8.8 case: timeout no_fallback=False + 有 fresh tick → market 成交（覆盖 Req3 Scenario 2）
- [ ] 8.9 case: timeout no_fallback=False + 无 tick → 拒单 `paper_unfilled_no_tick`（覆盖 Req3 Scenario 3 + 新 Req7 Scenario 1）
- [ ] 8.10 case: timeout no_fallback=False + 老 tick (>staleness 阈值) → 拒单 `paper_unfilled_no_tick`（覆盖新 Req7 Scenario 1）
- [ ] 8.11 case: 自定义 `paper_limit_tick_staleness_sec=120` 配置生效（覆盖新 Req7 Scenario 3）
- [ ] 8.12 case: pending 期间收到第二个 open_short 被跳过（覆盖 Req5 Scenario 1）
- [ ] 8.13 case: pending 期间收到 close 移除 pending（覆盖 Req5 Scenario 2）
- [ ] 8.14 case: 重启后 `_pending_limits` 为空（覆盖 Req6 Scenario 1）
- [ ] 8.15 case: `_save_state` 写入的 paper_positions.json 不含 pending_limits 字段（覆盖 Req6 Scenario 2）
- [ ] 8.16 case: cleanup loop 在 30s 内处理 deadline 到达的 pending（覆盖新 Req8 Scenario 1）
- [ ] 8.17 case: legacy paper_trades.jsonl 行无 `entry_method` 时下游 fail-safe 默认 market（覆盖 Req4 Scenario 4）
- [ ] 8.18 新增 `tests/test_telegram_pullback_alerts.py`：`pullback_unfilled` 和 `paper_unfilled` 都触发 TG send，缺 source 走 fail-safe（覆盖 risk-alert-routing spec 全部 Scenario）
- [ ] 8.19 case: paper 与 live 同时触发未成交，TG 收到两条独立消息且前缀区分

## 9. 回归与基线

- [ ] 9.1 跑 `python3 -m pytest -q tests/test_pullback_atr_policy.py tests/test_limit_no_fallback.py` 确保不回归
```

Full source: openspec/changes/pullback-entry-paper-parity/tasks.md

## openspec/changes/pullback-entry-paper-parity/specs/paper-executor/spec.md

- Source: openspec/changes/pullback-entry-paper-parity/specs/paper-executor/spec.md
- Lines: 1-154
- SHA256: dadf283da2a566ab70a4a4838cb6adeabd6b9581823aaa85ed18753de26a9d27

[TRUNCATED]

```md
## ADDED Requirements

### Requirement: Paper Executor SHALL respect plan order_type when opening positions

When `paper_executor._open_paper(symbol, action, plan, decision)` receives a `plan` with `order_type='limit'`, the paper account SHALL NOT immediately fill at `latest_price`. Instead, the position SHALL be queued as a pending limit and resolved by `_wait_paper_limit_fill` based on `entry_zone`, `limit_timeout_sec` and `limit_no_fallback` fields. When `plan.order_type` is missing, `'market'`, or any other value, the existing immediate-fill behavior SHALL be preserved (fail-safe default).

#### Scenario: Limit plan defers to wait_paper_limit_fill
- **WHEN** `_open_paper` receives `plan={order_type:'limit', entry_zone:[low, high], limit_timeout_sec:1800, limit_no_fallback:True, ...}` and no existing position
- **THEN** the position SHALL NOT appear in `_positions[symbol]` immediately
- **AND** an entry SHALL be added to `_pending_limits[symbol]` with `created_at`, `plan` snapshot, `decision` snapshot, `entry_method='limit_pending'`
- **AND** no `paper_trades.jsonl` record SHALL be appended yet

#### Scenario: Market plan keeps legacy immediate fill
- **WHEN** `_open_paper` receives `plan={order_type:'market', ...}` or `plan` without `order_type` field
- **THEN** the position SHALL be created in `_positions[symbol]` at `latest_price` in the same call
- **AND** the position record SHALL include `entry_method='market'`

#### Scenario: Limit plan with missing entry_zone falls back to market
- **WHEN** `_open_paper` receives `plan={order_type:'limit'}` but `entry_zone` is `[]`, `[0, 0]`, or absent
- **THEN** the system SHALL log a warning and fall back to market behavior with `entry_method='market'` (fail-safe — never silently drop a trade_decision)

### Requirement: Paper Executor SHALL detect entry_zone hits via price_tick stream

`_wait_paper_limit_fill` SHALL evaluate pending limits whenever a new `price_tick` arrives for the symbol. A pending limit is considered filled when the tick price falls within `[min(entry_zone), max(entry_zone)]` inclusive, even momentarily. Fill price SHALL be the midpoint of `entry_zone`. The check SHALL also run on a periodic cleanup loop to handle timeout regardless of tick activity.

#### Scenario: Tick price inside entry_zone triggers fill
- **WHEN** a pending limit exists with `entry_zone=[0.4043, 0.4047]`
- **AND** a `price_tick` arrives with `price=0.4044`
- **THEN** the position SHALL be added to `_positions[symbol]` with `entry_price=0.4045` (midpoint), `entry_method='limit_filled'`
- **AND** the pending entry SHALL be removed from `_pending_limits`
- **AND** a `paper_trades.jsonl` open event SHALL NOT be written (only on close, consistent with current behavior)

#### Scenario: Tick price crosses entry_zone instantaneously
- **WHEN** a pending limit exists with `entry_zone=[0.4043, 0.4047]`
- **AND** two consecutive ticks arrive at 0.4042 then 0.4060 (touching 0.4045 only momentarily between)
- **AND** the system observed at least one tick within `[0.4043, 0.4047]`
- **THEN** the position SHALL be filled at midpoint 0.4045 with `entry_method='limit_filled'`

#### Scenario: Tick price never enters entry_zone
- **WHEN** a pending limit exists with `entry_zone=[0.4043, 0.4047]`
- **AND** all ticks during `limit_timeout_sec` window are outside the zone
- **THEN** the position SHALL NOT be filled by tick-driven path
- **AND** at the timeout the cleanup loop SHALL trigger the timeout branch (next requirement)

### Requirement: Paper Executor SHALL handle limit timeout per limit_no_fallback

When `_wait_paper_limit_fill` reaches `created_at + limit_timeout_sec` without entry_zone hit, behavior SHALL match `executor.py:_execute_limit_order` semantics:
- If `plan.limit_no_fallback == True` (pullback policy default): the pending limit SHALL be removed without opening a position. A rejection record SHALL be appended to `_rejected_log` and a `risk_alert` SHALL be published with `type='paper_unfilled'`.
- If `plan.limit_no_fallback == False`: the pending limit SHALL be filled at the latest tick price as a market fallback. The position SHALL be created with `entry_method='market'` (fallback path collapses into market for downstream simplicity) and a separate `paper_limit_fallback_used` log entry.

#### Scenario: Pullback policy timeout (no_fallback=True)
- **WHEN** a pending limit times out with `limit_no_fallback=True`
- **THEN** no `_positions[symbol]` entry SHALL be created
- **AND** `_rejected_log` SHALL receive a record with `reason='paper_unfilled'`, `request_id` from decision, and `entry_method='limit_unfilled'`
- **AND** a bus event SHALL be published: `topic='risk_alert'`, `payload={type:'paper_unfilled', source:'paper_executor', symbol, side, entry_zone, request_id}`

#### Scenario: Non-pullback limit timeout (no_fallback=False)
- **WHEN** a pending limit times out with `limit_no_fallback=False`
- **AND** the latest tick price is available
- **THEN** the position SHALL be created in `_positions[symbol]` at the latest tick price with `entry_method='market'`
- **AND** an info log SHALL note `paper_limit_fallback_used` for traceability
- **AND** no `paper_unfilled` risk_alert SHALL be published (this is success, not rejection)

#### Scenario: Non-pullback limit timeout with no tick available
- **WHEN** a pending limit times out with `limit_no_fallback=False` but `_latest_price[symbol]` is missing
- **THEN** the pending limit SHALL be removed and a `paper_unfilled` rejection SHALL be recorded with `reason='paper_unfilled_no_tick'` (fail-safe — never use a stale entry_zone midpoint as fallback)

### Requirement: Paper account records SHALL include entry_method field

Every paper position record (in `_positions`, in `paper_positions.json` after persistence, and in `paper_trades.jsonl` close events) SHALL include an `entry_method` field with one of the values: `'market'`, `'limit_filled'`, `'limit_unfilled'`. Records produced before this change SHALL be treated as `entry_method='market'` by any downstream reader (fail-safe default).

#### Scenario: Market open writes entry_method=market
- **WHEN** a position is opened via the immediate-fill path
- **THEN** `_positions[symbol]['entry_method']` SHALL equal `'market'`
- **AND** the eventual `paper_trades.jsonl` close record SHALL include `entry_method='market'`

#### Scenario: Limit fill writes entry_method=limit_filled
- **WHEN** a position is opened via tick-triggered limit fill
- **THEN** `_positions[symbol]['entry_method']` SHALL equal `'limit_filled'`
- **AND** the eventual `paper_trades.jsonl` close record SHALL include `entry_method='limit_filled'`
```

Full source: openspec/changes/pullback-entry-paper-parity/specs/paper-executor/spec.md

## openspec/changes/pullback-entry-paper-parity/specs/risk-alert-routing/spec.md

- Source: openspec/changes/pullback-entry-paper-parity/specs/risk-alert-routing/spec.md
- Lines: 1-38
- SHA256: 079928af559e092eceee5eaa3cf0a00f5cc40d55a5ec73578908c17d60190907

```md
## ADDED Requirements

### Requirement: Telegram critical_types SHALL include pullback_unfilled and paper_unfilled

The `critical_types` set in `agents/trading/telegram_notifier.py:_handle_risk_alert` SHALL include both `'pullback_unfilled'` (live) and `'paper_unfilled'` (paper). Risk alerts of these types SHALL produce user-visible Telegram messages, not be silently dropped.

#### Scenario: Live pullback_unfilled triggers TG message
- **WHEN** `executor.py:_execute_limit_order` cancels a limit with `no_fallback=True` and emits `_enqueue_drift_alert('pullback_unfilled', symbol, side, limit_price, timeout_sec)`
- **AND** the resulting `risk_alert` bus event reaches `_handle_risk_alert`
- **THEN** the alert SHALL pass the `critical_types` check
- **AND** a Telegram message SHALL be sent with prefix `[实盘]` (or equivalent) and include `symbol / side / limit_price / timeout_sec`

#### Scenario: Paper paper_unfilled triggers TG message
- **WHEN** `paper_executor._wait_paper_limit_fill` times out a pending limit with `limit_no_fallback=True` and publishes `risk_alert{type='paper_unfilled', source='paper_executor'}`
- **THEN** the alert SHALL pass the `critical_types` check
- **AND** a Telegram message SHALL be sent with prefix `[模拟]` (or equivalent) so users can distinguish from live events
- **AND** the message SHALL include `symbol / side / entry_zone / request_id`

#### Scenario: Other alert types unaffected
- **WHEN** any pre-existing critical alert type (e.g., `flash_move`, `max_drawdown`, `protection_failed`, `tp_invariant_breach`) is published
- **THEN** routing behavior SHALL remain identical to the pre-change baseline (no regression)

### Requirement: Risk alerts SHALL distinguish paper vs live by source field

Every `risk_alert` payload SHALL include a `source` field. Paper-originated alerts use `source='paper_executor'`; live-originated alerts use `source='executor'` (or whatever the existing live path emits). Telegram message formatting SHALL key off `source` to apply paper/live prefix and SHALL NOT collapse the two into one indistinguishable message.

#### Scenario: Paper alert has source=paper_executor
- **WHEN** any `paper_unfilled` alert is constructed by paper_executor
- **THEN** the payload SHALL include `source='paper_executor'`

#### Scenario: Live alert has live source
- **WHEN** any `pullback_unfilled` alert is constructed by live executor (root or agent layer)
- **THEN** the payload SHALL include `source='executor'` (or another non-paper identifier consistent with existing live alerts)

#### Scenario: TG message prefix reflects source
- **WHEN** `_handle_risk_alert` formats a message for `pullback_unfilled` or `paper_unfilled`
- **THEN** the message SHALL include a paper-vs-live distinguishing prefix derived from `source`
- **AND** absence of `source` SHALL default to live behavior with a warning log (fail-safe — never silently treat unknown source as paper)
```

