## ADDED Requirements

### Requirement: 前向 1 秒聚合 bar 采集
系统 SHALL 提供独立的轻量采集模块，从上线日起以 1 秒聚合 bar（OHLC+volume）持久化价格数据到独立 `klines_1s.db`（复用 kline schema，不污染主 klines.db），与 9 维行情采集解耦。

#### Scenario: 上线即采集 1s bar
- **WHEN** tick 采集 feature flag 开启
- **THEN** 系统 SHALL 持续采集并持久化在交易标的的 1 秒聚合 bar 到 `klines_1s.db`，带时间戳

#### Scenario: 不污染主 klines.db
- **WHEN** 1s bar 写入
- **THEN** 系统 SHALL 写独立 `klines_1s.db`，主 `data/klines.db` 内容不受影响

#### Scenario: 独立于行情主链路
- **WHEN** tick 采集模块发生故障或被关停
- **THEN** `multi_data_collector` 的 9 维行情采集与决策主链路 SHALL 不受影响

### Requirement: 有界写入不阻塞
系统 SHALL 以有界方式（批量 flush / 采样）写入 tick 快照，不得阻塞采集或决策循环。

#### Scenario: 批量 flush
- **WHEN** tick 快照高频到达
- **THEN** 写入 SHALL 批量/异步进行，单条写入不得同步阻塞主循环

### Requirement: 价格精度双轨数据源
系统 SHALL 让反事实 PnL 的价格判定优先使用 tick 快照（上线后的时段），缺 tick 时退化用 1m K 线。

#### Scenario: tick 优先
- **WHEN** 某被拒单的存续时段存在 tick 快照
- **THEN** SL/TP 触发判定 SHALL 用 tick 精度，避免 1m 同根 SL/TP 不可判

#### Scenario: 缺 tick 退化 1m
- **WHEN** 某时段无 tick 快照（上线前历史）
- **THEN** 系统 SHALL 退化用 1m K 线 + SL-first 保守假设，并计入偏差带

### Requirement: tick 路径与 retention 受控
系统 SHALL 经 `utils/state_paths.py` 派生 tick 文件路径，支持 retention 与 feature flag 关停，并 SHALL 按 `tick_capture_retention_days`（默认 30）实际滚动清理超期 1s bar，使 `klines_1s.db` 有界增长。

#### Scenario: flag 关停无残留
- **WHEN** tick 采集 feature flag 关闭
- **THEN** 系统 SHALL NOT 采集或写 tick 文件，且不影响其余功能

#### Scenario: retention 滚动清理超期数据
- **WHEN** 1s bar 持续写入，且存在 `open_time` 早于 `now - tick_capture_retention_days*86400` 的旧 bar
- **THEN** 系统 SHALL 节流删除这些超期 bar（不必每次写入都清），保留窗口内 bar 不动，使 `klines_1s.db` 不无界增长

#### Scenario: 清理失败不中断采集
- **WHEN** retention 清理过程抛异常（如 DB 锁/IO 错误）
- **THEN** 异常 SHALL NOT 传播进 1s 采集路径，记录 fail-safe 计数告警，后续写入与采集正常继续
