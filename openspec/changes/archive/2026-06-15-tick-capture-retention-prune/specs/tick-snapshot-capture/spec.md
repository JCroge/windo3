## MODIFIED Requirements

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
