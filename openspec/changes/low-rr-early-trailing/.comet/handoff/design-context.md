# Comet Design Handoff

- Change: low-rr-early-trailing
- Phase: design
- Mode: compact
- Context hash: c1e916c010c4489c741824676d55b93b33a9b867caadf609d7a7439cba5e913f

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/low-rr-early-trailing/proposal.md

- Source: openspec/changes/low-rr-early-trailing/proposal.md
- Lines: 1-24
- SHA256: 879d4fb4a21b3e2a5863b7801e75e3820056fc7471bd62e3842e81f1bc87a4c4

```md
## Why

Low RR 槽（low_rr_extra / long_bullish_low_rr / long_aligned_low_rr）的退出机制与主仓位相同，但其 TP1 距离远（R:R 1.21-1.48），实际极少触达 TP1（全量回测 TP1 命中率仅 17.9%），导致方向正确的单大量利润回吐后止损。CF 实验室 38,648 条信号回测证实当前机制均R -0.071（负期望），加提前 trailing 后均R +0.128（正期望），胜率从 53.9% 提升至 68.6%。

## What Changes

- Low RR 槽持仓在浮盈达 +0.5R 时即启动 trailing stop，trailing 距离 0.3R
- 不再等待 TP1 触发才激活 trailing，TP1 仍保留作为全平触发条件
- Position dict 增加 `slot` 字段标记开仓槽类型，供退出逻辑判断
- 主仓位（size=30, lev=10x）退出逻辑完全不变

## Capabilities

### New Capabilities
- `low-rr-early-trailing`: Low RR 槽独立的提前 trailing 退出机制，+0.5R 启动、0.3R 距离跟踪

### Modified Capabilities

## Impact

- `executor.py`: `_update_trailing()` 增加 low_rr 槽分支逻辑
- `agents/trading/judge.py`: 开仓时在 position dict 写入 `slot` 标记
- 测试：新增 trailing 参数化测试覆盖 low_rr 路径
- 不影响现有交易所交互、风控逻辑、主仓位退出
```

## openspec/changes/low-rr-early-trailing/design.md

- Source: openspec/changes/low-rr-early-trailing/design.md
- Lines: 1-45
- SHA256: 43884721bd03edd23b6e345565cc13fd9b786bb4a608db472c0605455d6ac613

```md
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
```

## openspec/changes/low-rr-early-trailing/tasks.md

- Source: openspec/changes/low-rr-early-trailing/tasks.md
- Lines: 1-22
- SHA256: 9922743e5c8c157ece29f4ec93ea7371b4a278abcc5e119c219aba0064cb311f

```md
## 1. Position Slot 标记

- [ ] 1.1 在 Judge 开仓路径（`_open_position` 或等效）写入 `slot` 字段到 position dict，low_rr 槽写 `"low_rr"`，主槽写 `"main"` 或不写
- [ ] 1.2 确认 executor `open_position()` / `_register_position()` 保留并持久化 `slot` 字段

## 2. Early Trailing 逻辑

- [ ] 2.1 在 executor config 增加 `low_rr_trail_start_r`（默认 0.5）和 `low_rr_trail_dist_r`（默认 0.3）参数
- [ ] 2.2 在 `_update_trailing()` 开头检测 `position.get('slot') == 'low_rr'`，命中时走独立 early trailing 分支（+0.5R 启动，0.3R 距离），跳过 BE/锁利逻辑
- [ ] 2.3 Early trailing 分支实现：更新 highest_price → 计算 trail_sl = highest - R*dist → ratchet（只向有利方向移动）→ 调用 `_move_sl()`

## 3. 测试

- [ ] 3.1 单元测试：low_rr 槽 +0.5R 时 trailing 激活，SL 移动到 highest - 0.3R
- [ ] 3.2 单元测试：low_rr 槽 trailing SL 只向有利方向 ratchet
- [ ] 3.3 单元测试：low_rr 槽 TP1 仍能触发 partial_tp_1
- [ ] 3.4 单元测试：main 槽不受影响，走原 BE/锁利/TP1-trailing 路径
- [ ] 3.5 回归：运行全量 pytest，确认基线不降

## 4. 验证

- [ ] 4.1 用 CF 回测脚本验证修改后代码路径产出与独立回测一致（均R ≈ +0.128）
```

## openspec/changes/low-rr-early-trailing/specs/low-rr-early-trailing/spec.md

- Source: openspec/changes/low-rr-early-trailing/specs/low-rr-early-trailing/spec.md
- Lines: 1-42
- SHA256: ebd33f92ee1d7e827d00d83b42da554191f0e6d27e8ad9152b3aecc81332b64c

```md
## ADDED Requirements

### Requirement: Low RR slot early trailing activation
The system SHALL activate trailing stop for low_rr slot positions when unrealized profit reaches +0.5R, without waiting for TP1 to be hit.

#### Scenario: Trailing activates at +0.5R for low_rr position
- **WHEN** a position opened via low_rr slot (low_rr_extra / long_bullish_low_rr / long_aligned_low_rr) reaches +0.5R unrealized profit
- **THEN** trailing stop activates with distance 0.3R from highest price since entry

#### Scenario: Trailing SL ratchets upward
- **WHEN** trailing is active and price makes new high
- **THEN** trailing SL moves to (new_highest - 0.3R), never moves down

#### Scenario: Trailing SL triggers exit
- **WHEN** price retraces to trailing SL level
- **THEN** position is fully closed at trailing SL price

#### Scenario: TP1 still triggers if reached
- **WHEN** price reaches TP1 before trailing SL is hit
- **THEN** normal partial_tp_1 logic fires (50% reduce), trailing continues on remainder

### Requirement: Position slot marking
The system SHALL record the slot type in position dict at open time so exit logic can differentiate low_rr from main positions.

#### Scenario: Low RR slot position is marked
- **WHEN** a position is opened via low_rr_extra / long_bullish_low_rr / long_aligned_low_rr policy
- **THEN** position dict contains `slot` field with value identifying it as low_rr

#### Scenario: Main slot position is not affected
- **WHEN** a position is opened via main slot (size=30, lev=10x)
- **THEN** position dict `slot` field is absent or set to `main`, and existing trailing logic applies unchanged

### Requirement: Early trailing parameters are configurable
The system SHALL use configurable parameters for early trailing activation threshold and distance.

#### Scenario: Default parameters
- **WHEN** no override is configured
- **THEN** activation threshold is 0.5R and trailing distance is 0.3R

#### Scenario: Parameters adjustable via config
- **WHEN** config specifies different values for `low_rr_trail_start_r` and `low_rr_trail_dist_r`
- **THEN** those values are used instead of defaults
```

