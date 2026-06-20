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
