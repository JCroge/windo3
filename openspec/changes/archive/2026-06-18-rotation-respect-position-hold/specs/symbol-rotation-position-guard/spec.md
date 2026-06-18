## ADDED Requirements

### Requirement: 轮换保留已持仓标的于活跃集

标的轮换（SymbolRouter）SHALL 在轮换时将**仍有持仓**的标的保留在活跃标的集（active_symbols）中，而非将其移出并强平。持仓标的的出场决策 MUST 完全交由 PositionAnalyst（含其 SL/TP/趋势反转硬覆盖）与交易所挂单负责，SymbolRouter MUST NOT 因轮换对持仓标的发出平仓指令。

持仓真相源 SHALL 为 `utils.state_paths().positions`（与 MultiDataCollector、PositionAnalyst 共读的同一文件）。

#### Scenario: 持仓标的被研判轮出但仍被保留监控
- **WHEN** 轮换计算出某标的不在新研判选集中，但该标的在 positions 文件中仍有持仓
- **THEN** 该标的 SHALL 保留在 active_symbols 中（不进入 removed）
- **AND** SymbolRouter MUST NOT 对该标的发送 `trade_decision(action=close)`
- **AND** symbol_update 广播的 active_symbols MUST 包含该标的，使下游采集/分析/看护链路持续监控

#### Scenario: 无持仓标的维持原轮换平仓行为
- **WHEN** 轮换计算出某标的不在新研判选集中，且该标的无持仓
- **THEN** 该标的 SHALL 进入 removed
- **AND** SymbolRouter SHALL 对其发送 `trade_decision(action=close)`（与变更前行为一致）

### Requirement: 持仓查询 fail-safe 退化为旧行为

SymbolRouter 读取持仓的 `_get_position_symbols()` MUST 为 fail-safe：positions 文件缺失、不可读或 JSON 损坏时 SHALL 返回空列表且不抛异常。在持仓信息不可得时，系统 SHALL 退化为旧的轮换强平行为（持仓标的进入 removed 被平为 flat），以保证绝不产生"已开仓但无监控"的无人看管持仓。

#### Scenario: positions 文件缺失
- **WHEN** positions 文件不存在
- **THEN** `_get_position_symbols()` SHALL 返回空列表
- **AND** 轮换 SHALL 按"无持仓"路径处理所有 removed 标的（旧强平行为）

#### Scenario: positions 文件损坏
- **WHEN** positions 文件存在但内容无法解析为 JSON
- **THEN** `_get_position_symbols()` SHALL 返回空列表且记录 warning，不抛异常
- **AND** 轮换流程 SHALL 继续，不被阻断

### Requirement: 行为开关与运维可核对性

该保护行为 SHALL 受 config 开关 `rotation_close_held_enabled` 控制，经 `utils/config_loader.py` 接入（默认值、类型校验、环境变量覆盖、yaml 映射、启动 banner 展示）。默认值 SHALL 为 `false`（= 不强平持仓 = 新保护行为）。环境变量 `ROTATION_CLOSE_HELD_ENABLED=true` SHALL 可回滚至旧强平行为。启动 banner SHALL 展示该开关状态以便重启后核对。

#### Scenario: 开关默认关闭（保护生效）
- **WHEN** 未显式配置 `rotation_close_held_enabled`
- **THEN** 其值 SHALL 为 `false`
- **AND** 轮换对持仓标的 SHALL 执行保留（不强平）

#### Scenario: 开关开启回退旧行为
- **WHEN** `rotation_close_held_enabled` 配置为 `true`
- **THEN** 轮换对被轮出的持仓标的 SHALL 发送平仓指令（旧强平行为）

#### Scenario: 启动 banner 展示开关状态
- **WHEN** 交易进程启动并打印风控 banner
- **THEN** banner SHALL 包含「轮换强平持仓: 关闭/开启」一行，反映当前开关值
