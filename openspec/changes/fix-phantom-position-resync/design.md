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
