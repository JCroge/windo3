# Comet Design Handoff

- Change: fix-reviewer-symbol-format-and-marginal-settle
- Phase: design
- Mode: compact
- Context hash: d0bbdf5cc455b177a64ed082ffa3b89370484ac71520d776af7a7590b08ecb0d

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/fix-reviewer-symbol-format-and-marginal-settle/proposal.md

- Source: openspec/changes/fix-reviewer-symbol-format-and-marginal-settle/proposal.md
- Lines: 1-30
- SHA256: ef3030c903420aebf1c2917532a377442927f37b11f03aa12a0b1efe3d861f3c

```md
## Why

`scripts/track_marginal60.py` 8 个边缘单"未结算"，诊断（2026-06-20）出三层根因，主因是 **reviewer 的 symbol 格式不一致违反内部约定**：

- `agents/trading/reviewer.py:112/151/216` 取 `symbol = msg.get('symbol') or payload.get('symbol')`，**不经 `utils/symbol.py::to_internal()` 归一**——而该 helper 文档明确"所有 agent state dict 的 key 都应该用这个函数处理"，CLAUDE.md 红线也规定"跨 Agent symbol 用内部格式 `BASE-USDT`"。上游某 close 路径 leak `BASE-USDT-SWAP`，被原样落入 `trade_record['symbol']` 与 `[复盘] 记录交易` 日志。
- 后果：`记录交易` 日志格式混乱（`ETH-USDT-SWAP`/`UNI-USDT-SWAP`/`XRP-USDT-SWAP` 与 `XLM-USDT`/`XRP-USDT` 并存），`track_marginal60.py` 按精确字符串配对 fills（judge `开仓成功` 全 `BASE-USDT`）↔ PnL 失败 → ETH +0.86 / UNI −1.97 / XRP −0.58 **实际有 PnL 却被格式挡住**未结算；也是 XLM −7.76(跟踪器) vs −10.09(lifecycle) 对不上的根源。
- 次因：reviewer 漏记部分 close 的"记录交易"（external_close pending 未 finalize）；跟踪器选错数据源（grep 有损日志而非权威 `live_position_lifecycle.json`）。

## What Changes

- **① reviewer 入口 symbol 归一（根治 live 数据 bug，单点收口）**：`agents/trading/reviewer.py` 的 3 处 `symbol = msg.get(...)` 套 `to_internal(symbol)`，使 `trade_record['symbol']` 与 `记录交易` 日志恒为内部 `BASE-USDT`。对上游任何格式鲁棒，契合既有约定。
- **② `track_marginal60.py` 结算源改读权威 lifecycle**：从 grep `agent_reviewer_*.log` 改为读 `data/live_position_lifecycle.json`（`total_realized_pnl` 权威 + 统一键 + reconcile 状态）；fill 与 lifecycle 都经 `to_internal` 归一后按 symbol + `opened_at≈fill_ts` join。多 settle external_close 漏记的 close，并用 reconcile 后权威 PnL。
- **非目标**：不回填历史 `trade_history.json`（红线不改 data/ 用户数据，① 仅前向）；不逐个修上游 leak 的 publisher（reviewer 入口收口已对上游鲁棒）。

## Capabilities

### New Capabilities

- `reviewer-canonical-symbol`: reviewer trade record 与日志的 symbol 必须经 `to_internal` 归一为内部 `BASE-USDT`；边缘单 PnL 跟踪从权威 lifecycle 结算。

### Modified Capabilities

（无）

## Impact

- `agents/trading/reviewer.py`（3 处 symbol 取值套 `to_internal`，live 路径需回归）。
- `scripts/track_marginal60.py`（结算源改读 lifecycle.json，observability）。
- 测试：reviewer symbol 归一（混合格式入 → trade_record/日志恒 BASE-USDT）、tracker 从 lifecycle 正确 settle（含原未结算的 ETH/UNI/XRP）。
- 不动 data/ 历史数据；不改 close path / executor / realized_pnl_resolver。
```

## openspec/changes/fix-reviewer-symbol-format-and-marginal-settle/design.md

- Source: openspec/changes/fix-reviewer-symbol-format-and-marginal-settle/design.md
- Lines: 1-41
- SHA256: f3fbecb00d596590a89126a172645ad6c7def4cac5efec36d97f1fdc9e2537be

```md
## 高层架构决策（深度技术设计见 comet-design 的 Superpowers Design Doc）

### 根因

```
上游某 close 路径 leak BASE-USDT-SWAP (违反 "跨 Agent 用 BASE-USDT" 约定)
  → reviewer.py:112/151/216 `symbol = msg.get('symbol') or payload.get('symbol')` 不归一
    → trade_record['symbol'] + "记录交易" 日志格式混乱
      → track_marginal60 grep 精确字符串配对失败 → 8 未结算(ETH/UNI/XRP 实际有 PnL)
```

### 方案：消费侧收口归一 + 跟踪器读权威源

**① reviewer 入口 `to_internal` 收口**：3 处 `symbol = ...` 之后立即 `symbol = to_internal(symbol)`。消费侧防御——对上游任何格式鲁棒，无需逐个排查/修每个 leak 的 publisher。`to_internal` 已是 canonical helper（`SOL-USDT-SWAP`/`SOL/USDT:USDT`/`SOL-USDT` 全 → `SOL-USDT`，幂等）。

**② track_marginal60 读 lifecycle**：
- fill 仍从 judge `开仓成功` 取（symbol+ts），但归一。
- 结算源从 `agent_reviewer_*.log` 的 `记录交易` grep 改为 `data/live_position_lifecycle.json`：遍历 lifecycle 记录（每条有 `symbol`/`opened_at`/`closed_at`/`status`/`total_realized_pnl`/`reconcile_status`），归一 symbol，按 symbol + `opened_at≈fill_ts`（容差窗，如 ±300s）join fill。
- `total_realized_pnl` 是权威 reconcile 后值（解决 −7.76 vs −10.09）；external_close 漏记日志的也能 settle。

### 关键决策

1. **消费侧收口 vs 上游逐个修**：选消费侧（reviewer 入口 + tracker 读时双重归一）。理由：`to_internal` 幂等、契约文档支持"所有 key 都该过它"、对未知/未来 leak 鲁棒；逐个修上游 publisher 是 rabbit hole 且无法保证抓全。仅记录"观察到上游 -SWAP leak"供后续可选根治。
2. **不回填历史 trade_history.json**：红线"不改 data/ 用户数据"；① 前向归一已足够，历史分析读时归一即可。
3. **tracker join 容差**：fill_ts（judge 开仓成功）与 lifecycle.opened_at 可能差几秒（fill 日志 vs lifecycle 落库时点），用时间邻近窗 + 同 symbol + 同 side 匹配最近一条；多 fill 同 symbol 按时序配对。
4. **安全/回归**：reviewer 是 live 路径——`trade_record['symbol']` 被 segmented metrics / 分桶消费，归一为统一格式只会提升一致性；`_apply_pnl_resolution` 按 request_id/position_id upsert 不依赖 symbol，安全。需跑 reviewer 既有测试回归。

### 边界条件

| 情形 | 处理 |
|---|---|
| payload symbol 缺失/None | `to_internal(None)` 须 fail-safe（返回原值或空，不抛）；reviewer 既有 `or` 兜底保留 |
| lifecycle 无对应 opened_at 窗内记录 | 该 fill 标"未结算"（真未平或无 lifecycle） |
| lifecycle total_realized_pnl 为 None/pending | 标"未结算"，不伪造 |
| 同 symbol 多 fill | 按时序配对最近的 lifecycle 记录，避免重复消费 |

### 非目标

- 不改 close path / executor / realized_pnl_resolver（不动 PnL 来源，只改 reviewer 落记格式 + tracker 读源）。
- 不回填历史数据。
- 不逐个根治上游 leak publisher（消费侧收口已覆盖）。
```

## openspec/changes/fix-reviewer-symbol-format-and-marginal-settle/tasks.md

- Source: openspec/changes/fix-reviewer-symbol-format-and-marginal-settle/tasks.md
- Lines: 1-26
- SHA256: 9cb1a434be620e74ca65461150ca39982dce50ba00d840f15b3a5e2766ec2f86

```md
## 1. reviewer 入口 symbol 归一（根治）

- [ ] 1.1 `agents/trading/reviewer.py` import `from utils.symbol import to_internal`
- [ ] 1.2 3 处 symbol 取值点（~112/151/216 `symbol = msg.get('symbol') or payload.get('symbol')`）之后套 `symbol = to_internal(symbol)`（None fail-safe）
- [ ] 1.3 确认 `trade_record['symbol']` 与 `[复盘] 记录交易` 日志均用归一后 symbol
- [ ] 1.4 确认 `_apply_pnl_resolution` upsert 按 request_id/position_id（不依赖 symbol 格式）不回归

## 2. track_marginal60 结算源改读 lifecycle

- [ ] 2.1 `scripts/track_marginal60.py` 新增读 `data/live_position_lifecycle.json`（fail-safe 文件缺失）
- [ ] 2.2 fill（judge 开仓成功）symbol 经 `to_internal` 归一；lifecycle 记录 symbol 亦归一
- [ ] 2.3 settle：按 symbol + side + `opened_at≈fill_ts`（容差窗 ±300s）join，取 `total_realized_pnl`；`status` 未平/`total_realized_pnl` 缺失 → "未结算"
- [ ] 2.4 移除/替换原 grep `agent_reviewer_*.log` 的 `记录交易` 结算逻辑（fill/tier 仍从 judge 日志取）
- [ ] 2.5 真跑 `python3 scripts/track_marginal60.py` 确认原未结算的 ETH/UNI/XRP 现已结算、XLM 用权威 −10.09

## 3. 测试

- [ ] 3.1 单测：reviewer symbol 归一——构造 payload symbol=`XRP-USDT-SWAP` → trade_record['symbol']==`XRP-USDT`；`XRP-USDT` 幂等；None fail-safe
- [ ] 3.2 单测：tracker 从 lifecycle settle——构造 fill（`ETH-USDT`）+ lifecycle（`ETH-USDT-SWAP`,total_realized_pnl）→ join 成功结算；pending/缺失 → 未结算
- [ ] 3.3 reviewer 既有测试不回归（segmented metrics / trade_history / pnl_resolution upsert）
- [ ] 3.4 main() 登记新用例，全量回归零退化

## 4. 文档

- [ ] 4.1 更新 CLAUDE.md（reviewer symbol 归一约定 / track_marginal60 读 lifecycle）
- [ ] 4.2 comet-design 产出 Superpowers Design Doc
```

## openspec/changes/fix-reviewer-symbol-format-and-marginal-settle/specs/reviewer-canonical-symbol/spec.md

- Source: openspec/changes/fix-reviewer-symbol-format-and-marginal-settle/specs/reviewer-canonical-symbol/spec.md
- Lines: 1-39
- SHA256: 22f6180ef857979065b9366f327a535c6205877a7c0e8d13e9890dfc9396d35d

```md
## ADDED Requirements

### Requirement: Reviewer trade record symbol 归一为内部格式

ReviewerAgent 写入 `trade_record['symbol']` 与 `[复盘] 记录交易` 日志的 symbol SHALL 先经 `utils/symbol.py::to_internal()` 归一为内部 `BASE-USDT` 格式，不得把上游 payload 携带的原始格式（可能为 `BASE-USDT-SWAP` 或 ccxt `BASE/USDT:USDT`）原样落入。归一 MUST 在 reviewer 取 `symbol = msg.get('symbol') or payload.get('symbol')` 的各处入口统一施加（单点收口），契合 CLAUDE.md "跨 Agent symbol 用内部格式 BASE-USDT" 约定。

#### Scenario: 上游 -SWAP 格式被归一

- **WHEN** execution_result / pnl_resolved payload 携带 `XRP-USDT-SWAP`
- **THEN** reviewer 写入的 `trade_record['symbol']` 与 `记录交易` 日志均为 `XRP-USDT`

#### Scenario: 已是内部格式不变

- **WHEN** payload 携带 `XRP-USDT`
- **THEN** 归一后仍为 `XRP-USDT`（幂等）

#### Scenario: pnl_resolution upsert 不受影响

- **WHEN** `_apply_pnl_resolution` 按 entry_request_id/position_id upsert 已有 close 记录
- **THEN** 匹配键不依赖 symbol 格式，归一不破坏 upsert 关联

### Requirement: 边缘单 PnL 跟踪从权威 lifecycle 结算

`scripts/track_marginal60.py` 结算已实现 PnL 的数据源 SHALL 为权威 `data/live_position_lifecycle.json`（`total_realized_pnl` + reconcile 状态），而非 grep `agent_reviewer_*.log`。fill（judge `开仓成功`）与 lifecycle 记录 SHALL 都经 `to_internal` 归一 symbol 后按 symbol + `opened_at≈fill_ts` 时间邻近 join。observability-only write-only，不改 config、不下单。

#### Scenario: 格式不一致的已实现 PnL 正确结算

- **WHEN** 一笔边缘单的 lifecycle 记录 symbol 为 `ETH-USDT-SWAP`、fill 日志为 `ETH-USDT`
- **THEN** 经 `to_internal` 归一后两者 join 成功，正确结算其 `total_realized_pnl`（不再"未结算"）

#### Scenario: external_close 已 reconcile 的 PnL 被纳入

- **WHEN** 某 close 走 external_close、reviewer 未记"记录交易"日志，但 lifecycle 有 `total_realized_pnl` 且 `reconcile_status=matched`
- **THEN** 跟踪器从 lifecycle 结算该笔，不再因日志漏行而"未结算"

#### Scenario: 仍 pending 的不强行结算

- **WHEN** lifecycle 记录 `status` 未平仓或 `total_realized_pnl` 缺失/pending
- **THEN** 标"持仓中/未结算"，不伪造 PnL
```

