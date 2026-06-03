---
comet_change: pullback-entry-paper-parity
role: technical-design
canonical_spec: openspec
---

# Pullback Entry Paper Parity — Technical Design

## Context

OpenSpec 产物为事实源（proposal / design.md / specs/*）。本文档只补充落地细节，不重定义需求。

**问题**：06-02 commit `f512d1a` 引入 pullback ATR entry policy 后，paper executor 仍使用 `latest_price` 立即成交，与 live 限价 + 30 分钟死等行为脱钩；同时 `pullback_unfilled` 不在 TG `critical_types`。

**既有架构关键事实**（已 grep 验证）：
- `BaseAgent._periodic_loop` 已存在（`agents/base.py:80-90`），子类 override `tick()`；`paper_executor.tick()` 当前 `await asyncio.sleep(30)` + 5min 摘要 — 这是 cleanup loop 嵌入点。
- `paper_executor.on_message` 在 `price_tick` 分支已经更新 `_latest_price[symbol]` 并调 `_check_sl_tp`（`paper_executor.py:78-85`）— 这是 tick 驱动嵌入点。
- 单 event loop 串行调度，无并发安全问题。
- `_save_state` / `_load_state` 路径只读写 `_positions / _equity / _rejected_log`；`_pending_limits` 不加进序列化路径即天然 in-memory only。

## Decisions（落地细节）

### TD-1：双驱动嵌入点

```
on_message[price_tick]   ─→ _latest_price[symbol] 更新
                            └→ _check_sl_tp(symbol, price)        # 现有
                            └→ _wait_paper_limit_fill(symbol, price)  # 新增

tick()                    ─→ asyncio.sleep(30)
                            └→ 5min 摘要日志                       # 现有
                            └→ _scan_pending_limits()             # 新增
```

精度：tick 命中 = `price_tick` 频率（1-3s）；timeout 检测 = 30s 周期 → 1800s 超时误差 ±30s 可接受。

### TD-2：时间抽象 — freezegun

测试基础设施引入 **freezegun** 作为 dev/test 依赖：
- `requirements.txt` / `requirements.lock` 新增 `freezegun==1.5.1`（最新稳定版，纯 Python 无 C 扩展）
- 生产代码裸调 `time.time()`，不引入 `_now()` helper（保持简洁）
- 测试用 `@freeze_time("2026-06-03 00:00:00")` 装饰器 + `frozen_datetime.tick(delta=...)` 推进时间
- 与 `pytest-asyncio` 兼容（freezegun 1.5+ 原生支持）

**为什么不引入 `_now()` helper**：用户拍板倾向 freezegun；多增一层抽象违反 YAGNI；freezegun 已是事实标准。

### TD-3：可配置 staleness 阈值

```python
# paper_executor.py 模块顶部
DEFAULT_PAPER_LIMIT_TICK_STALENESS_SEC = 60

# __init__
self._tick_staleness_sec = float(
    (config or {}).get('paper_limit_tick_staleness_sec',
                       DEFAULT_PAPER_LIMIT_TICK_STALENESS_SEC)
)
```

**配置注入路径**（已查证）：
- `agents/orchestrator.py:82` `PaperExecutor(self.config)` 直接传 orchestrator 持有的 flat config dict
- 该 dict 由 `utils/config_loader.py` 构建：`DEFAULTS` (line 71+) → YAML / `.env` overrides → `_apply_env_overrides` ENV map
- 因此 paper 专用配置走 `DEFAULTS["paper_limit_tick_staleness_sec"] = 60` + ENV map `"PAPER_LIMIT_TICK_STALENESS_SEC": ("paper_limit_tick_staleness_sec", float)`
- `config.yaml` 当前是套利时代遗物，paper-specific 配置 **不**写到 yaml；走 `.env` + DEFAULTS 是项目惯例（参照 `EFFECTIVE_BALANCE_CAP / MIN_CONFIDENCE` 同类做法 line 87/89）

运维如需更宽松（如行情源不稳定）可在 `.env` 设 `PAPER_LIMIT_TICK_STALENESS_SEC=120` 即可，无需改代码。

### TD-4：pending 数据结构

```python
self._pending_limits: Dict[str, dict] = {}
# 一个 symbol 最多一笔 pending（去重保护）
# 字段：
#   created_at:  float (time.time())
#   deadline:    float (created_at + limit_timeout_sec)
#   side:        'long' | 'short'
#   action:      'open_long' | 'open_short'
#   plan:        dict (deep copy of decision.plan)
#   decision:    dict (subset: confidence, request_id, attribution, source)
#   entry_zone:  [low, high]  (低高已排序)
#   last_tick_ts: float | None (None 表示从未收到 tick)
```

`save_state` 路径**不**写 `_pending_limits` — 重启即丢失（与现有 spec Req6 一致）。

### TD-5：超时分支决策树

```
deadline 到达：
├─ limit_no_fallback == True
│  → _record_rejection(reason='paper_unfilled', entry_method='limit_unfilled')
│  → publish('risk_alert', {type:'paper_unfilled', source:'paper_executor', ...})
│  → 移除 pending
│
└─ limit_no_fallback == False
   ├─ now - last_tick_ts > _tick_staleness_sec  (含 last_tick_ts is None)
   │  → _record_rejection(reason='paper_unfilled_no_tick', entry_method='limit_unfilled')
   │  → publish('risk_alert', {type:'paper_unfilled', source:'paper_executor', subtype:'no_tick'})
   │  → 移除 pending
   │
   └─ tick fresh
      → 用 _latest_price[symbol] market 成交，entry_method='market'
      → log [PAPER] {symbol} {side} 限价超时 fallback market @ {price}
      → 移除 pending
      → 不发 risk_alert（fallback 是 success path，与 live executor.py:2548 对齐）
```

### TD-6：critical_types 与 source 分流

```python
# telegram_notifier.py:_handle_risk_alert
critical_types = (
    ...,                       # 现有
    'pullback_unfilled',       # 新增（live）
    'paper_unfilled',          # 新增（paper）
)

# 文案分流
source = payload.get('source', '')
prefix = '[模拟]' if source == 'paper_executor' else '[实盘]'
# source 缺失 → fallback 到 live + warning log（fail-safe）
```

### TD-7：root → agent logger 透传

`executor.py:2492` 的 `[Pullback] ... 不做市价fallback` 用 root logger（`self.logger`），但 root executor 与 agent_executor 是两套 logger 实例（不同文件输出）。

**实现**：`_enqueue_drift_alert('pullback_unfilled', ...)` 触发 `_drain_drift_alerts` 把 alert 发布到 bus；agent 层 `MultiExecutor` 已有 alert 监听，新增一行 agent logger info：
```python
self.logger.info(f"[Pullback] {symbol} {side} 限价 {limit_price} 在 {timeout_sec}s 内未成交（live）")
```

不修改 root executor.py 自身日志（保持单点真相）。

## Spec Patches（回写到 OpenSpec delta spec）

需要补充到 `openspec/changes/pullback-entry-paper-parity/specs/paper-executor/spec.md`：

### Patch 1：tick staleness 阈值与可配置

**Requirement: Paper Executor SHALL gate fallback by tick staleness**

```
WHEN limit_no_fallback=False AND deadline reached
  AND (last_tick_ts is None OR now - last_tick_ts > tick_staleness_sec)
THEN paper SHALL reject with reason='paper_unfilled_no_tick' instead of using stale price
```

阈值 `tick_staleness_sec` 默认 60s，可通过 `config.yaml` `paper_limit_tick_staleness_sec` 覆盖。

### Patch 2：cleanup loop 周期与精度

**Requirement: Paper Executor cleanup loop SHALL run at least every 30 seconds**

```
WHEN _pending_limits is non-empty
THEN paper executor's tick() SHALL invoke _scan_pending_limits within 30 seconds
AND timeout detection error SHALL NOT exceed 30 seconds
```

### Patch 3：save_state 不序列化 _pending_limits

**Scenario** added to existing Requirement: Pending limits SHALL NOT persist across restarts：

```
WHEN _save_state is invoked with non-empty _pending_limits
THEN the written paper_positions.json SHALL NOT contain any pending_limits field
AND no other persistence file SHALL contain pending limit state
```

## Test Strategy

新增依赖：`freezegun==1.5.1`（pip + lock）

测试文件：
- `tests/test_paper_limit_fill.py` — limit 撮合主路径（~12 case）
- `tests/test_telegram_pullback_alerts.py` — TG critical_types 与 source 分流（~6 case）

**测试技术要点**：
- `@freeze_time("2026-06-03 12:00:00") as frozen` + `frozen.tick(delta=timedelta(seconds=1801))` 推进时间
- 直接 `await pe.on_message({type:'price_tick', ...})` 模拟 tick；`await pe.on_message({type:'trade_decision', ...})` 模拟决策
- 直接 `await pe.tick()` 触发 cleanup（不等真实 30s 周期）
- 用 `pe.bus = MockBus()` 抓取 publish 的 alert 验证

**关键 case 矩阵**：

| case | tick 序列 | 时间推进 | 期望 |
|---|---|---|---|
| 立即命中 | t=0 p=0.4045 | 不推进 | filled @ mid, entry_method=limit_filled |
| 超时未命中 no_fb=T | 无 | t+=1801 | paper_unfilled, _positions 空 |
| 超时 + market no_fb=F | t=0 p=0.4100 | t+=1801 | market@0.4100, entry_method=market |
| 超时 + 无 tick no_fb=F | 无 | t+=1801 | paper_unfilled_no_tick |
| 超时 + 老 tick no_fb=F | t=0 p=0.4100 | t+=200 (>60s staleness) | paper_unfilled_no_tick |
| 瞬时穿越 | 0.4042 → 0.4045 → 0.4060 | per tick | filled @ mid |
| pending 期间第二 open | - | - | 第二个 trade_decision 跳过 |
| pending 期间 close | - | - | pending 被移除 |
| 重启 | save → load | - | _pending_limits 空 |
| save_state 不含 pending | _save_state | - | paper_positions.json 不含 pending 字段 |
| market plan 维持立成交 | - | - | entry_method=market |
| limit plan 缺 entry_zone | - | - | fail-safe market |

TG 测试：
- live pullback_unfilled → [实盘] 前缀 TG send
- paper paper_unfilled → [模拟] 前缀 TG send
- 缺 source → 默认 [实盘] + warning log
- 同时双发 → TG 收到两条独立消息
- 其他 critical_types 不回归

## Risks（增量，不重复 OpenSpec design.md）

| Risk | Mitigation |
|---|---|
| freezegun 引入新依赖（项目目前 0 处使用） | 仅 dev/test 依赖；纯 Python 无构建复杂度；锁定 1.5.1 最新稳定 |
| `tick_staleness_sec` 配置阈值过低导致 fallback 路径常拒单 | 默认 60s 在生产 multi_data_collector 1-3s 频率下宽松；config 可调 |
| 30s cleanup 周期使 1800s timeout 误差最大 30s | acceptance：30 分钟 ±30s 可接受（live 也是 polling） |

## Migration

无状态迁移；`paper_positions.json` / `paper_trades.jsonl` 旧记录无 `entry_method` 时下游 fail-safe 默认 `market`。

## Open Questions（resolved during this design）

- ~~Q1：pending 期间新 trade_decision 处理~~ → resolved by spec Req5
- ~~Q2：重启是否持久化~~ → resolved：不持久化（spec Req6 + Patch 3）

新增 resolved：
- Q3：staleness 阈值是否可配置 → 是，`paper_limit_tick_staleness_sec`，默认 60s
- Q4：测试时间控制方案 → freezegun
