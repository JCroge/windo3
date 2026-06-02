## ADDED Requirements

### Requirement: `/status` 命令必须显示 per-symbol halt 数量与 symbol 列表

`/status` 输出 MUST 包含一行表示当前 root executor `_halted_symbols` 的状态。无 halt 时输出 0；有 halt 时输出数量 + symbol 列表（最多 5 个，超出用 `…+N` 省略）。来源 MUST 是 `data/<ns_>agent_health.json`（30s 延迟可接受）。

#### Scenario: 无 per-symbol halt
- **WHEN** agent_health.json 中 `halted_symbols = {}`
- **AND** TG 收到 `/status`
- **THEN** 输出 MUST 含 "Per-symbol halt: 0" 或等价表述

#### Scenario: 有一个 per-symbol halt
- **WHEN** agent_health.json 中 `halted_symbols = {"XLM-USDT-SWAP": {...}}`
- **AND** TG 收到 `/status`
- **THEN** 输出 MUST 含 "Per-symbol halt: 1"
- **AND** MUST 含 "XLM"（symbol 简写或全名）

#### Scenario: 多个 halt 截断展示
- **WHEN** halted_symbols 含 7 个 symbol
- **AND** TG 收到 `/status`
- **THEN** 输出 MUST 显示前 5 个 + "…+2" 类似省略标记

#### Scenario: agent_health.json 缺失时 fallback
- **WHEN** `data/agent_health.json` 不存在
- **AND** TG 收到 `/status`
- **THEN** Per-symbol halt 行 MUST 输出降级文案（如 "Per-symbol halt: ?（health 文件缺失）"）
- **AND** 其他 status 字段不受影响

### Requirement: `/status` 命令必须显示 agent 注册数与任务存活数

`/status` 输出 MUST 包含一行表示已注册 agent 数 + 任务存活数 + 异常任务数。来源同样为 `agent_health.json`。

#### Scenario: 健康状态正常
- **WHEN** agent_health.json 中 `agents_registered=17, tasks_alive=17, tasks_failed=0`
- **AND** TG 收到 `/status`
- **THEN** 输出 MUST 含 "Agents: 17 注册 / 17 任务存活 / 0 异常" 或等价表述

#### Scenario: 有异常任务
- **WHEN** agent_health.json `tasks_failed=2`
- **AND** TG 收到 `/status`
- **THEN** 输出 MUST 含 "2" 异常计数（应明显可见，便于运维察觉）

### Requirement: `/status` 命令必须显示 bus DLQ 计数

`/status` 输出 MUST 包含一行 bus DLQ size。来源同样为 `agent_health.json` 的 `bus_dlq_size` 字段。无 DLQ attribute 时输出 0。

#### Scenario: DLQ 为空
- **WHEN** agent_health.json `bus_dlq_size=0`
- **THEN** 输出 MUST 含 "Bus DLQ: 0"

#### Scenario: DLQ 有积压
- **WHEN** agent_health.json `bus_dlq_size=3`
- **THEN** 输出 MUST 含 "Bus DLQ: 3"

### Requirement: Orchestrator 必须周期性写 agent_health.json

`agents/orchestrator.py` 的 Orchestrator MUST 在已有 tick 周期内（≤30s）写一次 `data/<ns_>agent_health.json`，schema 含 `ts / agents_registered / tasks_alive / tasks_failed / halted_symbols / bus_dlq_size`。

数据来源：
- `agents_registered / tasks_alive / tasks_failed`：Orchestrator 直读 `_tasks` / `_research_agents` / `_trading_agents`
- `halted_symbols`：MultiExecutor agent 周期性 publish `halts_snapshot{halted_symbols=...}` 总线事件，Orchestrator 订阅并缓存最新值
- `bus_dlq_size`：Orchestrator 直读 `MessageBus.get_instance()._dlq` 长度（缺失 attribute 时为 0）
- `ts`：Orchestrator 写入时戳

MultiExecutor MUST NOT 直接写 `agent_health.json`（避免双写 race）。

#### Scenario: 文件按 namespace 派生
- **WHEN** `STATE_NAMESPACE=testnet`
- **AND** Orchestrator 写 health
- **THEN** 文件路径 MUST 是 `data/testnet_agent_health.json`（与 `state_paths.get_state_paths()` 一致）

#### Scenario: 写入 schema 完整
- **WHEN** health 被写
- **THEN** JSON MUST 含全部 6 字段（ts, agents_registered, tasks_alive, tasks_failed, halted_symbols, bus_dlq_size）
- **AND** `halted_symbols` MUST 来自 Orchestrator 缓存的最新 halts_snapshot 事件
- **AND** `bus_dlq_size` MUST 来自 `MessageBus.get_instance()._dlq` 长度（缺失 attribute 时 0）

#### Scenario: 写入失败不阻塞主循环
- **WHEN** health 写入因磁盘错误失败
- **THEN** 异常 MUST 被吞掉并 logger.warning
- **AND** Orchestrator 主循环 MUST NOT 中断

#### Scenario: halts_snapshot 事件未到达时 health.halted_symbols 默认为空 dict
- **WHEN** Orchestrator 启动后还没收到任何 halts_snapshot 事件
- **AND** 被触发写 health
- **THEN** health.halted_symbols MUST 为 `{}`（不阻塞写入，不假死）

### Requirement: MultiExecutor agent 必须周期性 publish halts_snapshot 事件

`agents/trading/executor.py` 的 MultiExecutor agent MUST 在已有 tick / `_run_reconciliation` 周期内（≤30s）publish 一次 `halts_snapshot` bus 事件，payload 含 `halted_symbols=executor.get_halted_symbols()`。Orchestrator 订阅该 topic 用于写 agent_health.json。

#### Scenario: halts_snapshot payload 含 halted_symbols 浅拷贝
- **WHEN** MultiExecutor 周期触发
- **AND** `executor.get_halted_symbols()` 返回 `{"XLM-USDT-SWAP": {...}}`
- **THEN** publish 的 `halts_snapshot` payload MUST 含 `halted_symbols={"XLM-USDT-SWAP": {...}}`

#### Scenario: 周期≤30s
- **WHEN** MultiExecutor 持续运行 60 秒
- **THEN** 期间 MUST 至少 publish 2 次 halts_snapshot
