## ADDED Requirements

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
