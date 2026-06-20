## ADDED Requirements

### Requirement: Position sync tolerates transient exchange errors

`sync_positions` SHALL retry `fetch_positions()` on transient exchange network errors (`ccxt.NetworkError` and its subclasses `RequestTimeout` / `ExchangeNotAvailable` / `DDoSProtection`) up to a bounded number of attempts with backoff, before treating the cycle as failed. Each transient retry SHALL be logged at WARNING with the exception type name. The system MUST escalate to an ERROR log only after retries are exhausted (or for a non-transient exception), and the ERROR MUST include the exception type name. On terminal failure the local position state MUST be preserved (unchanged from prior behavior).

#### Scenario: Transient error then success
- **WHEN** `fetch_positions()` raises a `ccxt.NetworkError` on the first attempt and succeeds on a retry
- **THEN** the retry is logged at WARNING (with the exception type)
- **AND** `sync_positions` completes normally using the successful result
- **AND** no ERROR is logged

#### Scenario: Transient error exhausts retries
- **WHEN** `fetch_positions()` raises `ccxt.NetworkError` on every attempt
- **THEN** the helper raises after the bounded attempts
- **AND** exactly one ERROR is logged including the exception type name
- **AND** local positions are preserved and `_last_sync_result` is empty

#### Scenario: Non-transient error is not retried
- **WHEN** `fetch_positions()` raises a non-network exception (e.g. `ccxt.AuthenticationError`)
- **THEN** it is not retried
- **AND** it surfaces immediately as a single ERROR including the exception type name

### Requirement: 幽灵持仓补录双确认

`sync_positions` 对**本地不存在、交易所新出现**的持仓 SHALL NOT 立即补录，而是先标记 `pending_resync` 并记录该 tick；仅当**连续 N 个 sync tick（默认 N=2）都见到**该持仓时才补录本地。交易所平仓后上报延迟产生的幽灵持仓在下一个 sync tick 即消失，被双确认自然过滤，不进入本地。现有 `_close_cooldown` 平仓冷却作为第一道防线保留。

#### Scenario: 幽灵持仓不被补录

- **WHEN** 某 symbol 刚平仓，下一个 sync tick 交易所滞后仍上报该持仓（幽灵），但再下一个 tick 已消失
- **THEN** 第一个 tick 标 `pending_resync` 不补录，第二个 tick 该持仓消失 → 清除 `pending_resync`、本地始终无幽灵记录、不触发 protection-unknown/halt

#### Scenario: 真实残留持仓延迟一个 tick 补录

- **WHEN** 交易所确有一个本地缺失的真实持仓，连续 N 个 sync tick 都上报
- **THEN** 满足连续确认后补录本地（延迟约一个 sync tick），补录后正常进入 reconcile 归属 SL algo

#### Scenario: 冷却期内不进入双确认

- **WHEN** symbol 在 `_close_cooldown` 窗口内且交易所仍上报该持仓
- **THEN** 冷却期内直接跳过补录（第一道防线），不计入双确认 tick

### Requirement: protection-unknown 告警去重退避

`migrate_missing_sl` / `protection_state→unknown` 的 ERROR 日志与 `_halt_symbol` SHALL 对同一 symbol+同一原因去重退避：首次触发告警 + halt，后续 sync tick 同因不重复刷 ERROR、不重复 halt（已 halt 即静默或大幅降频），直到状态变化（恢复保护或移除持仓）。

#### Scenario: 同因不重复刷屏

- **WHEN** 某 symbol 持续处于 protection-unknown（如幽灵未消、或真实丢 SL 未恢复）跨多个 sync tick
- **THEN** 首次记 ERROR + halt，后续同因 tick 不再每次刷 ERROR（去重或退避降频），halt 不重复触发

### Requirement: 幽灵移除后 halt 自愈

由 `migrate_missing_sl`（持仓无对应交易所 SL algo）触发的 per-symbol halt，SHALL 在该 symbol 被 `sync_positions` 移除（确认已不在交易所）时自动清除，不需人工 Telegram `/resume`。其它原因的 halt（真实对账冲突、保护单失败）不在此自愈范围，仍走既有 fail-closed 流程。

#### Scenario: 幽灵移除自动清 halt

- **WHEN** 幽灵持仓触发 `migrate_missing_sl` halt 后，sync 确认该 symbol 已不在交易所并移除本地记录
- **THEN** 自动清除该 symbol 的 `migrate_missing_sl` halt，无需人工 /resume；记录自愈日志

#### Scenario: 非幽灵 halt 不被误清

- **WHEN** per-symbol halt 由真实对账冲突 / 保护单失败 / 其它 fail-closed 原因触发
- **THEN** 不在自愈范围，维持 halt 直到既有恢复路径（reconcile / 人工 /resume）处理
