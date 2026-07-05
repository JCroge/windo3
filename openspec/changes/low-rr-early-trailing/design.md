## Context

当前 executor.py 的 `_update_trailing()` 统一处理所有持仓的退出逻辑：BE@+0.8R → 锁利+0.3R@+1.0R → trailing（TP1 后激活）。该设计适合主仓位（R:R ≥ 1.5，TP1 可达），但 low_rr 槽的 R:R 为 1.21-1.48，TP1 命中率仅 17.9%（38,648 笔回测），大量方向正确的单在 TP1 前利润回吐后止损。

Position dict 当前不记录 slot 类型，`_update_trailing()` 无法区分 low_rr 和 main 持仓。Judge 在开仓时知道 slot（log 中可见 `slot=low_rr_extra`），但该信息未持久化到 position dict。

## Goals / Non-Goals

**Goals:**
- Low RR 槽持仓在 +0.5R 即启动 trailing（0.3R 距离），锁住方向正确的利润
- Position dict 携带 slot 标记，供退出逻辑差异化处理
- 参数可配置，便于后续微调
- 不改变主仓位退出行为

**Non-Goals:**
- 不修改入场逻辑 / 门槛 / slot 分配
- 不修改主仓位（main slot）的 BE/锁利/trailing 逻辑
- 不引入新的 TP 分档（仍保留 TP1/TP2 作为全平条件）
- 不做交易所侧 algo 单同步（本地 trailing 足够，low_rr 仓位小）

## Decisions

**D1: 在 `_update_trailing()` 内部加 slot 分支，而非抽离为独立函数**
- 理由：改动最小，trailing 逻辑集中在单一函数内，便于审计
- 备选：新建 `_update_trailing_low_rr()` —— 增加维护成本，两处同步 SL ratchet 逻辑

**D2: slot 标记写入 position dict 的 `slot` 字段**
- 理由：position dict 是持仓状态的唯一来源，executor 和 agent 层都能读取
- 写入时机：Judge `_open_position()` 路径，与 `entry_price`/`stop_loss` 同时写入
- 备选：从 log 反查 —— 不可靠，重启后丢失

**D3: Low RR early trailing 完全替代 BE/锁利逻辑（对 low_rr 槽）**
- 理由：+0.5R 启动 0.3R trailing 本身覆盖了保本功能（首次 SL 移到 +0.2R ≈ 保本+费用），无需叠加 BE/锁利
- 简化逻辑，避免 BE 和 trailing 相互干扰

**D4: 参数通过 executor config dict 传入，默认 hardcode**
- `low_rr_trail_start_r`: 0.5（启动阈值）
- `low_rr_trail_dist_r`: 0.3（跟踪距离）
- 理由：与现有 config 模式一致（如 `max_drawdown_pct`），无需新增 env var

## Risks / Trade-offs

- [抖动风险] 0.3R trailing 在高波动币种可能被正常回调抖出 → 回测已验证全量正期望，可接受；后续可按 atr_pct 动态调整
- [TP1 冲突] 如果价格先触 TP1 再回撤，partial_tp_1 和 trailing 会同时激活 → 现有 exit_lock 机制（FR-06）保证串行，无并发风险
- [回滚] 如效果不佳，删除 slot 分支 + 去掉 slot 标记即可，无数据迁移
