## ADDED Requirements

### Requirement: CF rolling 胜率(EV-gate 保真,镜像 Reviewer 窗口)
CF 组合 SHALL 维护一个与 live Reviewer 滚动窗口同语义的 rolling 胜率,供注入真实 Judge 的 EV gate 使用,避免冷启动死锁。窗口 SHALL 只吸收 CF 自身已结算的结果,SHALL NOT per-record 注入 reality 当时的演化计数。

#### Scenario: 窗口语义对齐 live
- **WHEN** CF 组合提供 `_recent_win_rate`
- **THEN** 其 SHALL 为最近 `rolling_window_size`(默认 20)笔 CF 已结算结果的 win 数 / 窗口长(与 Reviewer `_calculate_rolling_metrics` 一致),而非累计 `_recent_wins/_total_completed_trades`

#### Scenario: 从 CF 自身结果演化
- **WHEN** 某 CF 仓结算
- **THEN** 系统 SHALL 把该笔 win/loss 推入 rolling 窗口(FIFO 顶最老),使窗口随 CF 自身级联演化;SHALL NOT 用现实演化计数覆盖

#### Scenario: to_snapshot emit rolling 率
- **WHEN** CF 状态机 `to_snapshot`
- **THEN** `_recent_win_rate` SHALL 取 rolling 窗口率;`_recent_wins`/`_total_completed_trades` SHALL 保留累计语义(供 bayesian fallback);其它读者(cooldown 等)行为 SHALL 不变
