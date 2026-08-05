## Purpose

Keep OKX protective-order ownership and migration boundaries traceable so live exposure remains protected across restarts, manual protection, and sidecar ownership.

## Requirements

### Requirement: OKX 真实新挂保护单必须使用 owner-tag clOrdId

`executor.py` 中所有真实下发到 OKX 的新保护单（attached SL、独立 replace SL、legacy 独立 SL）SHALL 使用 `_make_owner_tag_clord_id()` 生成 `attachAlgoClOrdId` / `algoClOrdId`，使本地状态丢失或多 bot 同账户场景下可以按 owner prefix 证明归属。`_make_sl_clord_id()` MUST 仅保留作为历史兼容标识器（cleanup 路径仍然支持），并且 MUST NOT 再被新挂单调用。

#### Scenario: _replace_protective_sl 使用 owner-tag prefix
- **WHEN** `executor._replace_protective_sl(symbol, position, new_sl)` 在 OKX 上挂新 SL
- **THEN** 传给 `_place_protective_sl` 的 `clord_id` 必须满足 `_is_owner_clord_id(clord_id) == True`
- **AND** `clord_id` 不得以 `sl` 前缀开头（除非 `sl` 是 owner prefix `ca<ns><bot>` 后的偶然字符，仍需 `_is_owner_clord_id` 通过）

#### Scenario: open_position_with_plan 的 attached SL 使用 owner-tag
- **WHEN** `open_position_with_plan` 构造 `tp_sl_params`（含 `attachAlgoClOrdId`）下单
- **AND** `stop_loss` 参数为有效价格
- **THEN** `attachAlgoClOrdId` 必须使 `_is_owner_clord_id` 返回 True

#### Scenario: legacy _open_position 独立 SL 使用 owner-tag 并写入 position
- **WHEN** legacy `_open_position()` 调用 `_place_protective_sl`
- **THEN** 必须传入 owner-tag `clord_id`
- **AND** 挂单成功后 `position['sl_algo_clord_id']` 必须等于该 owner-tag clord_id（不再是 None）

#### Scenario: 非 OKX 交易所路径不受影响
- **WHEN** `exchange_id != 'okx'`
- **THEN** `_replace_protective_sl` / `open_position_with_plan` / `_open_position` 不强制生成 owner-tag clOrdId（保持现有行为）

### Requirement: 缺 BOT_INSTANCE_ID 时启动告警

live 多 bot 同账户场景需要 `BOT_INSTANCE_ID` 环境变量来区分不同 bot 的 owner prefix。当 `STATE_NAMESPACE='live'`（或推断为 live）且 `BOT_INSTANCE_ID` 未配置或为空字符串时，启动 banner MUST 打印 WARNING，使运维知晓 cross-bot 归属无法通过 clOrdId 证明。testnet/paper 模式下 SHALL NOT 触发该告警。

#### Scenario: live 模式缺 BOT_INSTANCE_ID 时 banner 打 WARNING
- **WHEN** `STATE_NAMESPACE='live'` 且 `BOT_INSTANCE_ID` 为空
- **THEN** 启动 banner 必须包含 `WARNING` 字样和提示 `BOT_INSTANCE_ID not configured`

#### Scenario: live 模式有 BOT_INSTANCE_ID 时 banner 不打 WARNING
- **WHEN** `STATE_NAMESPACE='live'` 且 `BOT_INSTANCE_ID="bot-A"`
- **THEN** 启动 banner 不得包含 `BOT_INSTANCE_ID not configured` WARNING

#### Scenario: testnet/paper 模式不打 BOT_INSTANCE_ID WARNING
- **WHEN** `STATE_NAMESPACE='testnet'` 或 `STATE_NAMESPACE='paper'`，`BOT_INSTANCE_ID` 为空
- **THEN** banner 不得包含 `BOT_INSTANCE_ID not configured` WARNING（避免 testnet 误报）

### Requirement: Main migration SHALL preserve protection on sidecar-owned present exposure
Main OKX algo migration SHALL preserve pending TP/SL protection for symbols that are currently sidecar-owned and have present or unknown exchange exposure, even when Main has no local position for that symbol. Manual or ambiguous OCO/conditional algos SHALL NOT be canceled as orphan residuals in this state.

#### Scenario: Manual OCO survives sidecar-owned migration
- **WHEN** Main OKX algo migration scans a symbol with no local Main position
- **AND** the sidecar owner registry has an open owner row matching that symbol and side
- **AND** exchange position state for that symbol is present or unknown
- **AND** a pending manual OCO algo exists without a sidecar owner tag
- **THEN** Main SHALL preserve the algo
- **AND** it SHALL record the preservation or ambiguity in the migration summary

#### Scenario: Manual conditional SL survives sidecar-owned migration
- **WHEN** Main OKX algo migration scans a sidecar-owned symbol with present or unknown exchange exposure
- **AND** a pending conditional SL algo exists without a recognized Main owner tag
- **THEN** Main SHALL preserve the algo
- **AND** it SHALL NOT count the algo as an orphan SL cancellation

#### Scenario: Exchange-flat orphan cleanup is not weakened
- **WHEN** Main OKX algo migration scans a symbol with no local Main position
- **AND** there is no active sidecar owner row for the symbol or exchange state is confirmed flat
- **THEN** existing orphan cleanup behavior MAY still cancel residual Main-owned or unowned algos according to the migration policy

### Requirement: Tactical V2 SHALL use deterministic owner-tagged TP and SL protection
Every filled Tactical V2 position SHALL have full-quantity exchange-owned TP and SL protection whose client identities are deterministic derivatives of the intent id and satisfy the existing bot owner-tag format. The position state SHALL persist the entry client id, each protection client id, returned exchange algo ids, protected quantity, trigger prices, and reconciliation state. The ownership model MUST support exchanges that expose the TP and SL under one OCO algo id or separate algo ids without changing the Tactical lifecycle contract.

#### Scenario: Tactical fill installs identifiable full protection
- **WHEN** a Tactical V2 entry is confirmed filled for a quantity
- **THEN** the system SHALL submit full-quantity TP and SL protection with deterministic Tactical V2 owner-tagged client identities
- **AND** the persisted position SHALL contain enough identity to prove ownership after restart

#### Scenario: Main and sidecar retain distinct bot owners
- **WHEN** Main and the legacy sidecar run concurrently during shadow observation or drain
- **THEN** Main/V2 SHALL use the configured `BOT_INSTANCE_ID`
- **AND** the sidecar SHALL force its executor to use the distinct `SIDECAR_BOT_INSTANCE_ID` even when Main's owner is present in the shared environment

#### Scenario: Partial fill protects only confirmed quantity
- **WHEN** a Tactical V2 entry partially fills and its remainder is canceled
- **THEN** TP and SL protection SHALL cover the confirmed filled quantity only
- **AND** the system SHALL NOT place protection for or chase the unfilled quantity

#### Scenario: Combined and separate algo representations reconcile equivalently
- **WHEN** OKX returns one parent algo id for attached TP and SL or returns independently addressable TP and SL algo ids
- **THEN** the reconciler SHALL persist the observed representation
- **AND** it SHALL prove both required protection legs without assuming a fixed exchange response shape

### Requirement: Unverified Tactical protection SHALL fail closed
A Tactical fill SHALL NOT be considered safely open until both its full-quantity TP and SL legs are verified against exchange state. If verification or ownership proof fails, the system SHALL stop new Tactical admission, cancel any provably owned residual entry or protection orders, and attempt to close confirmed unprotected exposure through the owner-bound safety path. The integrity halt MUST remain until reconciliation proves account, position, and protection state.

#### Scenario: One missing protection leg triggers integrity handling
- **WHEN** a filled Tactical V2 position has a verifiable SL but no verifiable full-quantity TP, or a verifiable TP but no verifiable full-quantity SL
- **THEN** the system SHALL mark protection incomplete and activate the non-expiring Tactical integrity halt
- **AND** it SHALL attempt an owner-bound safe close of confirmed exposure rather than continue normal admission

#### Scenario: Unknown ownership does not cause broad cancellation
- **WHEN** an exchange protection order or position could belong to Main, legacy sidecar, another bot, or a manual operator
- **THEN** Tactical V2 SHALL preserve the ambiguous object and halt new Tactical admission for reconciliation
- **AND** it SHALL NOT cancel or close the object without ownership proof

#### Scenario: Reconciliation clears halt only after proof
- **WHEN** an integrity halt is active because protection or exposure was ambiguous
- **THEN** elapsed time alone SHALL NOT clear the halt
- **AND** admission MAY resume only after reconciliation proves every affected owner flat or fully protected

### Requirement: Tactical exit paths SHALL serialize and reconcile idempotently
Exchange TP/SL fills, local max-hold closes, global safety closes, and restart recovery SHALL coordinate through the existing normalized-symbol exit lock and owner identity. Exchange fills SHALL be authoritative. Before a local close, the system MUST reconcile remaining exchange quantity and cancel or amend only proven Tactical protection. Repeated observations of one close MUST converge on one final resolution rather than submit duplicate reduce-only closes.

#### Scenario: Exchange TP races max hold
- **WHEN** exchange TP fills while the local max-hold path is waiting for the same symbol exit lock
- **THEN** the local path SHALL reconcile the remaining quantity after acquiring the lock
- **AND** it SHALL NOT submit a second close when the exchange position is already flat

#### Scenario: Restart during close does not duplicate resolution
- **WHEN** the process restarts after an exchange close but before local close state is final
- **THEN** recovery SHALL reconcile exchange position, protection orders, and the deterministic intent identity
- **AND** it SHALL publish or retain one final PnL resolution for that close

#### Scenario: Final PnL delivery survives a publisher crash
- **WHEN** a final Tactical PnL correction is persisted before, during, or after downstream publication
- **THEN** the correction SHALL remain in a durable outbox until a publication acknowledgement is persisted
- **AND** restart recovery SHALL re-deliver it without applying the same `resolution_id` to the governor more than once

#### Scenario: Shared safety close retains Tactical attribution
- **WHEN** a global safety path closes a proven Tactical V2 position
- **THEN** owner-bound protection cleanup SHALL run under the serialized exit path
- **AND** the close SHALL be attributed as Tactical `risk_forced` rather than as a Main strategy exit
