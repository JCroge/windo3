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
