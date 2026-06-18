# Proposal: 连亏熔断阈值 3 → 5

## Why

Reviewer 的每日硬熔断（daily hard stop）有两个并行触发条件：当日已实现亏损达 `daily_pnl_hard_stop`（-300 USDT），或**连续亏损笔数达 `consecutive_loss_limit`（当前为默认值 3）**。连亏 3 笔即全平熔断对小额单笔亏损过于敏感，留给策略的「翻盘空间」偏小。同时，`consecutive_loss_limit` 此前**无法经 config.yaml 配置**——`_load_yaml` 未映射该键，只能靠环境变量或改默认值。

## What

1. 将连亏熔断阈值从 3 放宽到 5（连亏达第 5 笔才触发熔断）。
2. 让 `consecutive_loss_limit` 可经 config.yaml 的 `risk` 节点配置，与现有 `max_trade_amount / max_drawdown / max_daily_loss` 风格一致、可追溯。

## Scope

- 仅调整既有风控旋钮的值 + 暴露其 yaml 配置入口。
- **不**新增 capability、**不**改架构、**不**改接口（`consecutive_loss_limit` 配置键早已存在于 RISK_DEFAULTS、环境变量与 Reviewer 消费链路）。
- `-300 USDT` 日亏线作为独立并行兜底不变。

## Out of scope

- 不改动 daily_pnl_hard_stop、组合风控、OKX 执行级熔断等其它熔断维度。
- 不改动连亏统计逻辑 `_track_consecutive_losses`（24h 窗口、负 PnL 计数）。
