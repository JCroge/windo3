## 1. 核心：补录双确认（persist-2-ticks）

- [ ] 1.1 `executor.py` 加 `_pending_resync` 状态（`{sym: first_seen_count_or_ts}`），构造期初始化
- [ ] 1.2 `sync_positions` 补录分支（line ~2729 else）前置双确认：交易所新见持仓先标 `pending_resync` 计 1 tick，不补录
- [ ] 1.3 连续达 N（默认 2，可配 `position_resync_confirm_ticks`）tick 仍见 → 补录并清 `pending_resync`
- [ ] 1.4 该 symbol 某 tick 未在交易所出现 → 清 `pending_resync`（幽灵过滤）
- [ ] 1.5 冷却期内（`_close_cooldown` 未过）直接跳过、不计入双确认 tick（第一道防线保留）

## 2. 症状硬化：protection-unknown 告警去重退避

- [ ] 2.1 `migrate_missing_sl` / protection_state→unknown 的 ERROR 对 symbol+reason 去重（首次记，后续同因 tick 不重复刷或大幅降频）
- [ ] 2.2 `_halt_symbol(migrate_missing_sl)` 已 halt 即不重复触发（幂等）

## 3. 症状硬化：幽灵移除后 halt 自愈

- [ ] 3.1 `sync_positions` 移除某 symbol（已不在交易所）时，若该 symbol 有 `migrate_missing_sl` halt → 自动清除 + 记自愈日志
- [ ] 3.2 仅清 `migrate_missing_sl` 因的 halt；其它 fail-closed 因的 halt 不被误清（守卫）

## 4. 测试

- [ ] 4.1 单测：幽灵（sync tick1 见 / tick2 消失）→ 不补录、本地无记录、不触发 halt
- [ ] 4.2 单测：真仓（连续 2 tick 见）→ 第 2 tick 补录
- [ ] 4.3 单测：冷却期内交易所仍上报 → 跳过、不计双确认 tick
- [ ] 4.4 单测：protection-unknown ERROR 同 symbol+reason 连续 N tick → ERROR 仅首次/降频、halt 不重复
- [ ] 4.5 单测：migrate_missing_sl halt → sync 移除 symbol → halt 自动清；非该因 halt 不被清
- [ ] 4.6 不回归 `position-sync-resilience` transient-error 重试测试 + 既有 halt/reconcile 测试
- [ ] 4.7 main() 登记新用例，全量回归零退化

## 5. 文档

- [ ] 5.1 更新 CLAUDE.md 风控红线 position-sync 相关条目（双确认 + 告警去重 + halt 自愈）
- [ ] 5.2 comet-design 产出 Superpowers Design Doc
