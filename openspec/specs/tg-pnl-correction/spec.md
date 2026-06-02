## ADDED Requirements

### Requirement: TG `/pnl <SYMBOL> <NET_PNL> [reason]` 必须为 pending external close 写 PnL correction

TG 命令 `/pnl` MUST 接收 `<SYMBOL>` 与 `<NET_PNL>`（USDT，可正可负 float），可选 `[reason]`。命令内部 MUST 调用 `LiveLedger.find_pending_external_closes()` 找该 symbol 的未 supersede pending 候选，候选恰好 1 条时 MUST 调用 `LiveLedger.apply_pnl_resolution()` 写 `external_close_correction` 事件，`source='manual_tg_review'`。

#### Scenario: 候选恰好 1 条时写 correction
- **WHEN** Ledger 有 1 条 XLM-USDT-SWAP pending external_close 未被 supersede
- **AND** TG 收到 `/pnl XLM 0.42`
- **THEN** MUST 调用 `apply_pnl_resolution`，resolution 含 `realized_pnl_net_usdt=0.42` 和 `pnl_status='final'`
- **AND** ledger 写入 `event_type='external_close_correction'` 事件，`source='manual_tg_review'`，`supersedes_event_id` 指向原 pending
- **AND** TG 回消息确认（含 symbol、net_pnl、新 event_id 或 supersede 信息）

#### Scenario: 候选 0 条时拒绝并提示
- **WHEN** Ledger 没有 XLM-USDT-SWAP 的 pending external_close
- **AND** TG 收到 `/pnl XLM 0.42`
- **THEN** MUST NOT 调用 `apply_pnl_resolution`
- **AND** TG 回消息表明 "未找到 XLM 的 pending external close"

#### Scenario: 候选多于 1 条时拒绝并提示用 /pnl_id
- **WHEN** Ledger 有 2 条 XLM-USDT-SWAP pending external_close 未被 supersede
- **AND** TG 收到 `/pnl XLM 0.42`
- **THEN** MUST NOT 调用 `apply_pnl_resolution`
- **AND** TG 回消息列出候选 event_id 列表
- **AND** 提示用户使用 `/pnl_id <event_id> <net_pnl>`（即便 `/pnl_id` 暂未实现，引导消息保留）

#### Scenario: NET_PNL 解析失败拒绝
- **WHEN** TG 收到 `/pnl XLM abc`
- **THEN** MUST NOT 查 ledger
- **AND** TG 回用法提示 `用法: /pnl <SYMBOL> <NET_PNL_USDT> [reason]`

#### Scenario: 缺参拒绝
- **WHEN** TG 收到 `/pnl XLM`（缺 NET_PNL）
- **THEN** MUST 回用法提示

#### Scenario: 仅授权 chat_id 可执行
- **WHEN** TG 收到 `/pnl XLM 0.42` 但 chat_id ≠ 配置的 `_chat_id`
- **THEN** 命令 MUST 静默忽略

### Requirement: TG `/pnl_id <event_id> <NET_PNL> [reason]` 必须按 event_id 精确匹配 pending

当 `/pnl <SYMBOL>` 因多候选拒绝时，`/pnl_id` MUST 提供按 event_id 精确匹配的回退命令。命令 MUST 调用 `LiveLedger.find_pending_external_closes()` 并按 `event_id == 参数 event_id` 过滤；命中恰好 1 条时写 correction，0 条时拒绝。

#### Scenario: event_id 命中恰好 1 条 pending 时写 correction
- **WHEN** Ledger 含 pending event_id="abc-123" 未 supersede
- **AND** TG 收到 `/pnl_id abc-123 0.42`
- **THEN** MUST 调用 `apply_pnl_resolution`，resolution 含 `realized_pnl_net_usdt=0.42`，`source='manual_tg_review'`
- **AND** 写入 correction event，`supersedes_event_id='abc-123'`
- **AND** TG 回消息确认（含原 event_id 与新 net_pnl）

#### Scenario: event_id 不存在或已 supersede 拒绝
- **WHEN** Ledger 不含活跃 pending event_id="abc-123"（已 supersede 或不存在）
- **AND** TG 收到 `/pnl_id abc-123 0.42`
- **THEN** MUST NOT 调用 `apply_pnl_resolution`
- **AND** TG 回消息表明 "未找到活跃的 pending event_id=abc-123"

#### Scenario: 缺参或 NET_PNL 解析失败
- **WHEN** TG 收到 `/pnl_id abc-123`（缺 NET_PNL）或 `/pnl_id abc-123 abc`
- **THEN** MUST NOT 查 ledger
- **AND** TG 回用法提示 `用法: /pnl_id <event_id> <NET_PNL_USDT> [reason]`

#### Scenario: 仅授权 chat_id 可执行
- **WHEN** TG 收到 `/pnl_id abc-123 0.42` 但 chat_id ≠ 配置的 `_chat_id`
- **THEN** 命令 MUST 静默忽略

### Requirement: TG `/pnl` 与 `/pnl_id` 必须共用候选解析 helper

`/pnl` 与 `/pnl_id` MUST 共享一个内部 helper（如 `_resolve_pending_for_pnl_correction(filter_fn)`），仅在候选过滤函数上不同（symbol-based vs event_id-based）。共享 helper MUST 实施一致的"候选恰好 1 条才写 correction、0 或多候选拒绝"语义。

#### Scenario: helper 接受 filter 函数
- **WHEN** helper 被调用，filter 仅返回 1 条 pending
- **THEN** MUST 进入 correction 写入分支

#### Scenario: helper 多候选时返回拒绝
- **WHEN** helper 被调用，filter 返回 ≥2 条 pending
- **THEN** MUST 返回拒绝结果（含候选 event_id 列表）
- **AND** 调用方根据上下文给出具体提示（`/pnl` 提示用 `/pnl_id`；`/pnl_id` 不会出现该分支因为 event_id 唯一）

### Requirement: TG `/pnl` 写入必须幂等

相同 (symbol, net_pnl, pending_event_id) 的多次 `/pnl` 提交 MUST NOT 在 ledger 累计写多份 correction event。`apply_pnl_resolution` 现有契约按 `position_id + close_match_key + sorted(order_ids)` 去重，TG 命令 MUST 信任该去重而不绕过。

#### Scenario: 重复 /pnl 提交相同 net_pnl 不重复写
- **WHEN** 第一次 `/pnl XLM 0.42` 已写 correction event
- **AND** 第二次 TG 收到 `/pnl XLM 0.42`（candidate 仍是同一 pending—— wait,实际上第一次 correction 已经把 pending superseded,第二次 find_pending_external_closes 会过滤掉它,自然 0 候选)
- **THEN** 第二次 MUST 走"0 候选"分支并提示用户该 pending 已被 correction
- **AND** ledger MUST NOT 多写 correction event

#### Scenario: 不同 net_pnl 二次提交在同 pending 上失败
- **WHEN** 第一次 `/pnl XLM 0.42` 已写 correction
- **AND** TG 收到 `/pnl XLM 0.50`
- **THEN** 第二次走"0 候选"分支（pending 已 superseded）
- **AND** 用户被引导明确用 `/pnl_id` 或人工处理（避免重复 correction 导致 daily PnL 错算）

### Requirement: TG `/pnl` 必须传入 reason 字段到 correction 事件

可选 `[reason]` 参数 MUST 写入 correction 事件的 `pnl_pending_reason` 或 `manual_correction_reason` 字段（具体字段名以 ledger 现有 schema 一致为准），便于事后审计。

#### Scenario: 带 reason 写入字段
- **WHEN** TG 收到 `/pnl XLM 0.42 OKX bills late`
- **THEN** correction event 的相应字段值 MUST 含 "OKX bills late"

#### Scenario: 不带 reason 仍能写入
- **WHEN** TG 收到 `/pnl XLM 0.42`
- **THEN** correction event 写入成功
- **AND** reason 字段为空字符串或缺失但不抛错
