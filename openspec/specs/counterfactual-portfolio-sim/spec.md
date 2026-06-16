## ADDED Requirements

### Requirement: 反事实组合状态机
系统 SHALL 维护一个反事实组合状态机，字段对齐 L2 决策快照白名单（slot 占用、`_open_positions`/`_position_slots`、EV 计数、cooldown、probe、`_symbol_state`、balance、regime），与真实系统状态完全隔离。

#### Scenario: CF 状态独立于真实
- **WHEN** CF 状态机更新（开仓/平仓/计数）
- **THEN** 其 SHALL 只改 CF 实例字段，SHALL NOT 改真实 Judge/Executor/RiskGuard 状态

#### Scenario: 状态可注入真实 Judge
- **WHEN** 序列驱动要做下一个决策
- **THEN** CF 状态机 SHALL 能以 L2 `restore_state` 接受的快照格式提供当前状态

### Requirement: CF 持仓生命周期 + L1 估算 PnL 反馈
系统 SHALL 对 CF 开仓用 L1 `resolve_counterfactual`（SL/TP/24h）估算退出与净 PnL，并把估算 PnL 喂回 CF 状态（资金、EV 计数、archetype cooldown、daily-stop 累加器）。

#### Scenario: CF 开仓推进生命周期
- **WHEN** 扰动决策开一个 CF 仓
- **THEN** 系统 SHALL 记录 CF 持仓，并在其存续窗口用 `resolve_counterfactual` 求 SL/TP/24h 退出与 `net_usdt`

#### Scenario: 估算 PnL 喂回独立 cooldown
- **WHEN** 一个 CF 仓退出
- **THEN** 系统 SHALL 调 `ArchetypeCooldown.record_result(archetype, pnl)` 于**独立 CF cooldown 实例**，SHALL NOT 读或写真实 cooldown

#### Scenario: 不读真实 daily-stop/cooldown
- **WHEN** CF 决策需要 cooldown/daily-stop 状态
- **THEN** 系统 SHALL 只读 CF 实例状态，SHALL NOT 读真实 `is_cooled()` / daily-stop 状态

### Requirement: CF daily-stop 模拟
系统 SHALL 维护 CF 当日已实现 PnL 累加器，按 Reviewer 阈值常数（daily_pnl_hard_stop / consecutive_loss_limit）触发 CF 当日停开。

#### Scenario: CF 当日亏损触发停开
- **WHEN** CF 当日累计已实现 PnL 跌破 daily_pnl_hard_stop（或连续亏损达 consecutive_loss_limit）
- **THEN** 系统 SHALL 停止当日剩余 CF 开仓，次日重置

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
