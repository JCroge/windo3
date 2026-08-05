## Purpose

Make Telegram `/status` a compact operational health view that distinguishes agent health, per-symbol execution halts, global halt state, bus DLQ health, and Tactical circuit state.

## Requirements

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

### Requirement: bus DLQ 增长必须主动告警
系统 SHALL 保证：Orchestrator 周期性健康循环（已有 `_health_loop` / `_write_agent_health`，约 30s）在算出
`dlq_size = len(bus._dead_letter)` 后，MUST 与上一次记录的 `_prev_dlq_size` 比较；当
`dlq_size > _prev_dlq_size`（出现新死信，说明有 enqueue 失败或重要 topic 无订阅者）时，MUST
经现有 `telegram_alert` 通道主动 publish 一条告警事件（含当前 dlq_size 与本次增量 delta），
不得仅把 DLQ 计数静默写入 `agent_health.json`。比较基准 `_prev_dlq_size` MUST 在每次健康
tick 后更新，使告警按 30s cadence 天然限流、不重复刷屏。

#### Scenario: DLQ 增长触发告警
- **WHEN** 某次健康 tick 算出 `dlq_size=3` 且 `_prev_dlq_size=0`
- **THEN** MUST publish `telegram_alert{type='bus_dlq_growth', dlq_size=3, delta=3}`
- **AND** 随后 `_prev_dlq_size` MUST 更新为 3

#### Scenario: DLQ 未增长不告警
- **WHEN** 某次健康 tick 的 `dlq_size <= _prev_dlq_size`
- **THEN** MUST NOT publish bus_dlq_growth 告警

### Requirement: `/status` SHALL distinguish global halt, per-symbol halt, and Tactical circuit

Telegram `/status` SHALL display global halt state, per-symbol halt state, and Tactical V2 circuit state as separate status lines. A global protection halt MUST NOT be presented in a way that implies the Tactical circuit is paused. Tactical circuit state SHALL be read only from the freshness-checked Tactical V2 operational snapshot; missing legacy risk-guard circuit data MUST NOT be interpreted as a healthy V2 circuit.

#### Scenario: global protection halt while Tactical circuit is not paused
- **WHEN** `halt_state.halted == true` with reason `okx_sl_algo_unresolved:WLD-USDT-SWAP`
- **AND** a fresh Tactical V2 snapshot reports no timed pause or integrity halt
- **THEN** `/status` MUST show global halt as active with the OKX protection reason
- **AND** `/status` MUST show Tactical circuit as not paused
- **AND** the message MUST NOT imply Tactical loss circuit caused the halt

#### Scenario: Tactical circuit paused while global halt is clear
- **WHEN** `halt_state.halted == false`
- **AND** a fresh Tactical V2 snapshot reports a future pause deadline or active integrity halt
- **THEN** `/status` MUST show global halt as inactive
- **AND** `/status` MUST show Tactical circuit as paused with its pause reason

#### Scenario: status data missing degrades safely
- **WHEN** the Tactical V2 snapshot is missing, unreadable, malformed, or stale
- **THEN** `/status` MUST still show global halt and per-symbol halt state
- **AND** Tactical circuit line MUST degrade to an unknown or `STALE` marker rather than failing the command

### Requirement: Tactical V2 SHALL publish one atomic operational status snapshot
The Tactical V2 engine SHALL atomically write a namespace-aware status snapshot at least every 30 seconds and after material lifecycle or governor transitions. The snapshot SHALL include `updated_at`, mode and version, configured margin and slot limit, active/pending/free slot counts and symbols, rolling 24-hour final PnL, active loss streak, timed pause and integrity-halt state, episode outcome counts, protection and reconciliation health, and shadow/live parity mismatch counts. This snapshot SHALL be a read model only and MUST NOT become an admission or exit authority.

#### Scenario: Material transition updates the snapshot
- **WHEN** an intent enters pending, fills, terminates, closes, changes a circuit state, or changes protection integrity
- **THEN** Tactical V2 SHALL atomically refresh the operational snapshot
- **AND** the snapshot SHALL describe state derived from the durable Tactical ledger and current reconciliation result

#### Scenario: Telegram status cannot change risk state
- **WHEN** Telegram reads or formats the Tactical V2 snapshot
- **THEN** it SHALL NOT mutate a slot, episode, PnL record, pause, or integrity halt
- **AND** Tactical admission SHALL continue to use the persistent governor rather than Telegram data

### Requirement: `/status` SHALL display compact Tactical V2 execution state
Telegram `/status` SHALL render the Tactical V2 snapshot as a compact section containing mode/version, `100U x 3` configuration, active/pending/free slots, rolling 24-hour final PnL versus the `-15U` admission threshold, loss streak and 60-minute circuit state, episode outcomes, active/pending symbols, protection/reconciliation health, and shadow/live parity mismatch count. Admission pauses SHALL be labeled as blocking new Tactical opens while existing positions remain managed.

#### Scenario: Healthy Tactical V2 state is fully visible
- **WHEN** a fresh snapshot reports live V2 mode, one active slot, one pending slot, no circuit, and verified protection
- **THEN** `/status` SHALL show Tactical V2 live, `100U x 3`, `1 active / 1 pending / 1 free`, rolling PnL, streak, and circuit clear
- **AND** it SHALL show the active and pending symbols plus protection and parity state

#### Scenario: Rolling loss pause is distinguished from forced close
- **WHEN** rolling 24-hour final Tactical PnL is at or below `-15U`
- **THEN** `/status` SHALL show new Tactical admission paused by rolling loss
- **AND** it SHALL state or clearly imply that existing Tactical positions remain managed rather than force-closed by this threshold

#### Scenario: Integrity halt is visible and non-timed
- **WHEN** the snapshot reports unresolved ownership or protection ambiguity
- **THEN** `/status` SHALL show Tactical integrity halt with the affected symbols or count
- **AND** it SHALL NOT display an automatic expiry time for that halt

### Requirement: Tactical status freshness SHALL fail visibly
Telegram SHALL treat the Tactical snapshot as stale when `updated_at` is older than the configured freshness threshold, defaulting to 90 seconds. A missing, malformed, stale, or non-finite Tactical snapshot SHALL render `STALE` or unknown values and MUST NOT be presented as healthy. Failure to read Tactical data MUST NOT prevent existing global halt, per-symbol halt, agent, or DLQ status from rendering.

#### Scenario: Stale snapshot is not shown as healthy
- **WHEN** the Tactical snapshot is older than 90 seconds under the default configuration
- **THEN** `/status` SHALL label the Tactical section `STALE`
- **AND** it SHALL NOT claim that slots, circuit, protection, or parity are current

#### Scenario: Non-finite PnL degrades safely
- **WHEN** the Tactical snapshot contains a non-finite rolling PnL value
- **THEN** `/status` SHALL render Tactical PnL as unknown or invalid
- **AND** the Telegram command SHALL continue rendering other status sections
