# Comet Design Handoff

- Change: fix-phantom-position-resync
- Phase: design
- Mode: compact
- Context hash: 4593c8f8e5b6c4b4945ffa8be544767925c2c9de41f4b39609af17f8e970703a

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/fix-phantom-position-resync/proposal.md

- Source: openspec/changes/fix-phantom-position-resync/proposal.md
- Lines: 1-30
- SHA256: 54eec1a461abef5cfd874c4673d0e21e595d1d1d91459d8dce752f4a598ccd3a

```md
## Why

`sync_positions`（`executor.py:2667`）在平仓后会从交易所滞后快照**重新补录已平仓位**为幽灵持仓。实证（2026-06-20 XRP）：02:16:33 干净平仓（lifecycle −0.83 matched），02:17:49（76 秒后）sync 从交易所读到 XRP 残留持仓、补录回本地（`executor.py:2747`），幽灵无 SL algo → `[Migrate]` reconcile 标 `protection_state=unknown` + `_halt_symbol(reason='migrate_missing_sl')`（`executor.py:667/669`）→ ERROR 每 ~32s 刷屏 **131 次/~69 分钟**（02:17–03:26），XRP 被 per-symbol halt，直到 03:26 交易所读数变平移除、03:27 **人工 Telegram /resume** 清 halt。

已有"刚平仓冷却期不重新补录"守卫（`executor.py:2719/2720` 查 `_close_cooldown[sym]`），但窗口=**60 秒**（`executor.py:928 & 1828` `time.time()+60`），OKX 平仓后持仓上报延迟 >76s 超窗 → 冷却失效。**系统性复发**：近 3 天 3 次（06-18 UNI / 06-19 XLM / 06-20 XRP），每次都在平仓后。本次零真实风险（幽灵仓、仓位真平），但该签名**区分不了"幽灵"与"真·丢 SL 的实仓"**，且 ERROR 无去重刷屏、halt 不自愈需人工介入。

## What Changes

- **核心：补录前双确认（persist-2-ticks）**——`sync_positions` 对本地不存在、交易所新出现的持仓，不立即补录，先标 `pending_resync`；仅当**连续 2 个 sync tick 都见到**该持仓才补录。幽灵（API 滞后残留）在下一个 tick 即消失被自然过滤，真仓延迟一个 tick（~32s）补录（补的是已存在仓位、非新开，可接受）。保留现有 `_close_cooldown` 作第一道防线。
- **症状硬化（与核心正交）**：
  - protection-unknown / `migrate_missing_sl` 的 ERROR 与 `_halt_symbol` **去重 + 退避**（同 symbol 同因每 sync tick 不重复刷，首次告警后静默/降频）。
  - 幽灵移除后 **per-symbol halt 自愈**：`migrate_missing_sl` 触发的 halt 在该 symbol 被 sync 移除（已不在交易所）时自动清除，不需人工 /resume。
- **非目标**：20x 杠杆（已查明=`_calc_risk_budget` 恒定风险公式按设计输出、max_loss 仍 bounded 5%，非 bug，排除）；不改保护单 owner / SL 挂单逻辑本身。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `position-sync-resilience`: 新增"幽灵持仓补录防护（双确认）"、"protection-unknown 告警去重退避"、"幽灵移除后 halt 自愈"三组 requirement。

## Impact

- `executor.py`：`sync_positions` 补录分支加 `pending_resync` 双确认状态机；`migrate_missing_sl` 的 ERROR/halt 去重退避；sync 移除幽灵时清对应 `migrate_missing_sl` halt。
- 可能新增 config（双确认所需 tick 数、可配；默认 2）。
- 测试：双确认（幽灵 1-tick 消失不补录 / 真仓 2-tick 补录）、ERROR 去重、halt 自愈；不回归既有 `position-sync-resilience` transient-error 重试。
- observability/安全向：减少误 halt 与人工介入；不放松任何真实保护（真·无保护仓位仍 halt）。
```

## openspec/changes/fix-phantom-position-resync/design.md

- Source: openspec/changes/fix-phantom-position-resync/design.md
- Lines: 1-49
- SHA256: 13ad35cc6421a256c04a3b7114b2a046dd0fdad448115cb9a4d2182153f6d953

```md
## 高层架构决策（深度技术设计见 comet-design 的 Superpowers Design Doc）

### 根因

```
close(02:16:33) → _close_cooldown[XRP]=now+60s
sync(02:17:49, +76s) → 冷却已过期(60<76) → 交易所滞后仍上报 XRP → 补录幽灵(无 SL)
  → [Migrate] protection_state=unknown + _halt_symbol(migrate_missing_sl)
  → ERROR ×131 / ~69min + per-symbol halt → 人工 /resume(03:27)
```

60s 固定冷却被 OKX >76s 上报延迟击穿；近 3 天复发 3 次（UNI/XLM/XRP）。

### 方案：双确认（persist-2-ticks）+ 症状硬化

```
现状: 交易所新见持仓 → 立即补录
改后: 交易所新见持仓 → 标 pending_resync(记 tick) → 连续 N=2 tick 都见 → 才补录
      幽灵下个 tick 即消失 → 清 pending_resync, 永不补录
      真仓持续上报 → 满 2 tick → 补录(延迟~32s, 补的是已存在仓位非新开)
```

### 关键决策

1. **双确认 vs 加长冷却 vs fills 核查**（用户已确认双确认）：双确认不赌固定窗（鲁棒于任意 OKX 滞后）、不加网络调用（轻），幽灵靠"撑不过 2 tick"自然过滤。冷却 `_close_cooldown` 作第一道防线保留（多数幽灵被 60s 冷却挡住，双确认兜住超窗的）。
2. **状态机最小化**：新增 `_pending_resync: {sym: first_seen_tick_or_ts}`，仅在补录分支（`executor.py:2729` else 分支）前置；幽灵消失即从 dict pop。不碰移除分支、不碰已存在仓位的数量校正分支。
3. **症状硬化正交**：ERROR/halt 去重退避（同 symbol+reason 不重复）+ 幽灵移除时清 `migrate_missing_sl` halt。两者独立于核心，但同源（都因幽灵），一并修以彻底消除本次故障表现。
4. **安全不放松**：真·无保护仓位（连续 2 tick 确认的真实持仓、reconcile 后仍无 SL）仍 halt；自愈仅限"幽灵已移除"这一明确安全态。

### 红线 / 约束

- 不放松任何真实保护：双确认只延迟"补录"，不延迟对真实无保护仓位的 halt。
- halt 自愈仅限 `migrate_missing_sl` 且 symbol 已被 sync 移除；其它 fail-closed halt 不动。
- 不改 `_calc_risk_budget`（20x 按设计、max_loss bounded 5%，非本 change）。
- 不改保护单 owner-tag / SL 挂单 / close path 逻辑。

### 验证策略

- 单测构造 sync 序列：幽灵（tick1 见 / tick2 消失）→ 不补录；真仓（tick1+tick2 见）→ 补录；冷却期内 → 跳过。
- ERROR 去重：同 symbol 连续 N tick protection-unknown → ERROR 仅首次/降频。
- halt 自愈：migrate_missing_sl halt → sync 移除 symbol → halt 清除；非该因 halt 不被清。
- 不回归 `position-sync-resilience` transient-error 重试既有测试。
- `sync_positions` 是 live 执行路径——需补同构单测，避免只靠 mock；本 change 不改决策公式故不强制 event_backtest。

### 非目标

- 20x 杠杆（已查明按设计）。
- 不重构 sync_positions 整体、不改 reconciler / realized_pnl_resolver。
- 不引入 fills 网络核查（双确认已够，避免加调用）。
```

## openspec/changes/fix-phantom-position-resync/tasks.md

- Source: openspec/changes/fix-phantom-position-resync/tasks.md
- Lines: 1-32
- SHA256: 1db59ab51c64da2ec2bed1e8a36be5099c2cb9edbc40c84d4bf0511bf84df5db

```md
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
```

## openspec/changes/fix-phantom-position-resync/specs/position-sync-resilience/spec.md

- Source: openspec/changes/fix-phantom-position-resync/specs/position-sync-resilience/spec.md
- Lines: 1-43
- SHA256: 57c1312b1559b9c2b05639e21f4de68abea3443c5b7908c61cd9c56c18a3213b

```md
## ADDED Requirements

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
```

