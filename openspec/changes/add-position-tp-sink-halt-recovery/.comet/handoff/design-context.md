# Comet Design Handoff

- Change: add-position-tp-sink-halt-recovery
- Phase: design
- Mode: compact
- Context hash: d35e0f2e5b3873a6c4b5cd537fb4fb040932fe5642bceba8b0ee7f79c93ff283

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/add-position-tp-sink-halt-recovery/proposal.md

- Source: openspec/changes/add-position-tp-sink-halt-recovery/proposal.md
- Lines: 1-33
- SHA256: b64658f7e00509a0f222292371c2e72626dbf31b93cea8d40ae3b1917c1c39ea

```md
## Why

第五次系统性审计（`docs/generated_reports/系统性审计报告_20260610_第五次.md`，P1-01，经对抗式复核 CONFIRMED 置信 0.9）发现一条高频可触发的可用性事故链：`ContractExecutor.add_to_position` 加仓后基于新均价重算并直接写 `position['take_profit']`，但**不更新** `position['take_profit_levels']`、也**不经** `_set_position_tp` 单一收口。下一轮 `_update_trailing` 顶部的 TP 不变量守卫检测到 `take_profit != take_profit_levels[0]`，调 `_halt_symbol(reason='tp_invariant_breach')`，后者**无条件**调 `get_halt_state().halt()` 跳全局熔断 → `can_open_new()=False` → **全系统所有 symbol 停止开新仓且不自愈**，需人工 `/force_resume`。加仓是常规持仓管理动作（PositionAnalyst conviction≥70 即触发），非边缘路径，1066 个测试无一覆盖此安全性。

这是既有 capability `entry-drift-policy` 的 "TP Field Single Source of Truth" 不变量在加仓写路径上的**漏网调用点**。

附带（P2-02 轻量）：`clear_symbol_halt`（TG `/resume_symbol` 路径）只清内存 `_halted_symbols`，不清持久化的全局 `halt_state`。任何 `_halt_symbol`（含真实故障）后，运维用 `/resume_symbol` 以为已恢复，实则全局仍 halt——恢复语义陷阱。

## What Changes

- **P1-01（核心修复）**：`add_to_position` 加仓后的 TP 重算改为按距离比例平移**整个** `take_profit_levels`，并经 `_set_position_tp(position, new_levels[0], new_levels)` 单点收口写入，保证 `take_profit == take_profit_levels[0]` 不变量在加仓后保持，消除 `tp_invariant_breach` 误触发与随之而来的全局熔断。需正确处理加仓发生在 partial-TP 已部分成交（`tp_filled>0`）时的 levels 平移与 `tp_filled` 语义一致。
- **P2-02（恢复语义诚实）**：`clear_symbol_halt` / `/resume_symbol` 处理链清完 per-symbol halt 后，若全局 `halt_state` 仍处于 halted，向 TG 回显诚实提示（"per-symbol halt 已清，但全局仍 halt，请用 /resume"）。纯消息/运维语义，不改变任何熔断或恢复的实际控制流。
- **测试**：新增"加仓后再跑 `_update_trailing` 不再 halt"的同构回归用例；加仓后 `take_profit==take_profit_levels[0]` 不变量断言；`clear_symbol_halt` 在全局 halt 仍在时回显提示的断言。
- **同构核对**：按 CLAUDE.md 红线，核对 `event_backtest.py` 是否复刻加仓 TP 路径；若复刻则同步，否则在 tasks 记录"加仓 TP 平移不进回测路径"的理由。

## Capabilities

### New Capabilities
（无）

### Modified Capabilities
- `entry-drift-policy`：强化 "TP Field Single Source of Truth" 需求，**显式覆盖 `add_to_position` 加仓写路径**——加仓重算 TP 必须经 `_set_position_tp` 收口、按比例平移所有 levels、保持不变量，禁止旁路只写 scalar `take_profit`。
- `tg-symbol-halt-control`：新增需求——`clear_symbol_halt` 清完 per-symbol halt 后，若全局 `halt_state` 仍 halted，必须向调用方/ TG 回显全局 halt 仍在的诚实状态，并提示需 `/resume`（带对账）才能解除全局熔断。

## Impact

- **代码**：
  - `executor.py`：`add_to_position`（约 3166-3188 TP 重算段）改走 `_set_position_tp`；`clear_symbol_halt`（942-973）增加全局 halt 状态回显（返回值或日志/结构化结果）。
  - `agents/trading/executor.py`：`_handle_resume_symbol` / `clear_symbol_halt` 调用点（约 119）透传全局 halt 仍在的提示。
  - `agents/trading/telegram_notifier.py`：`/resume_symbol` 回显文案补充全局 halt 提示。
- **测试**：新增/扩展 `test_partial_tp_lifecycle.py` 或新建 `test_add_position_tp_invariant.py`；`test_tg_symbol_halt_control.py` 增回显断言。
- **不影响**：`_halt_symbol` 的全局 fail-closed 姿态（真实故障仍跳全局）；per-symbol halt 持久化（仍内存）；`/resume_symbol` 不绕过 reconciliation 清全局——这些深度 halt 重构明确 out-of-scope，拆为后续 change。
- **风险红线**：修改执行/风控路径，必须保持 `_set_position_tp` 单点收口红线、保护单 fail-closed 不回归；基线当前 `1066 passed`，变更后须全绿。
```

## openspec/changes/add-position-tp-sink-halt-recovery/design.md

- Source: openspec/changes/add-position-tp-sink-halt-recovery/design.md
- Lines: 1-55
- SHA256: 0931c66993d9d3f9e45b318b9da81fd1aa5172f50eb1ba5351d4f4b6fb7a0f14

```md
# 高层设计：加仓 TP 收口 + halt 恢复语义诚实

> 本文件是 open 阶段的高层架构决策。深度技术 RFC（含逐步实现）由 comet-design 阶段产出至 `docs/superpowers/specs/`。范围 = B（已与用户确认）。

## 关键事实（一手代码核对，executor.py）

- `_set_position_tp(position, tp_first, tp_levels)`（2087-2096）：TP 字段唯一收口，断言 `tp_levels` 非空且 `tp_first == tp_levels[0]`，写 `take_profit` 与 `take_profit_levels`。
- TP 不变量守卫（`_update_trailing` 顶部，1923-1938）：`take_profit_levels` 非空 且 `take_profit != take_profit_levels[0]` → `_halt_symbol('tp_invariant_breach')`（严格 `!=`，无容差）。
- **生命周期关键**：`take_profit_levels` 在整个生命周期**不收缩**，始终是完整 `[tp1, tp2, ...]`；`tp_filled`（0/1/2，2991 在 reduce 真实成交后推进）是独立计数，驱动 `_update_trailing` 检查哪一级（1961/1968）。`take_profit` scalar 恒 == `levels[0]`，有 levels 时仅为满足不变量（实际 TP 逻辑走 levels + tp_filled，1908-1912 的 scalar 路径仅 legacy 无 levels 时生效）。
- bug 点 `add_to_position`（3166-3188）：重算只写 `position['take_profit'] = new_entry*(1±tp_dist)`，**不碰 levels** → `take_profit != levels[0]` → 下一轮守卫 halt。
- per-symbol halt 已有牙齿：`is_symbol_halted` gate 开仓(2113)/加仓(3061)/setTP；`_halted_symbols` 纯内存（无持久化）。全局 `halt_state` 持久化。`clear_symbol_halt`(942-973) 只清内存。

## 决策 1（P1-01）：加仓 TP 按 level 平移 + 经 `_set_position_tp` 收口

把 `add_to_position` 的 TP 重算段（3178-3183）替换为：

```
old_levels = position.get('take_profit_levels') or [old_tp]   # old_tp == old_levels[0]，由不变量保证
new_levels = []
for lvl in old_levels:
    dist = abs(lvl - old_entry) / old_entry      # 每个 level 各自距 OLD 均价的比例
    new_levels.append(new_entry * (1 + dist) if side == 'long'
                      else new_entry * (1 - dist))
self._set_position_tp(position, new_levels[0], new_levels)     # 单点收口，不变量保住
```

**为何按"每个 level 各自距离"而非单一 tp_dist**：保留多级 TP 结构（tp1@2% / tp2@4% 不被压平）。沿用 SL 重算（3170-3177）的"保持距离比例"同philosophy。

**tp_filled-safe**：`take_profit_levels` 不收缩、`tp_filled` 不动；已填级（如 tp_filled==1 时的 level 0）平移无害（`_update_trailing` 按 tp_filled 跳过它），未填级（level 1）平移到反映新均价是**正确**的（剩余 TP 目标应随新均价移动）。

**边界**：
- `old_tp` 为 None / 无 levels（理论上不该发生，因 FR-05 只在 `protected` 放行且开仓必经 `_set_position_tp`）→ 保持现有"`if old_tp and old_entry>0`"守卫，无 levels 时回退 `[old_tp]` 单级，仍经 sink。
- FR-05（3070-3074）已确保加仓只在 `protection_state=='protected'` 发生，levels 此时形态良好。

## 决策 2（P2-02 轻量）：恢复语义诚实，零控制流改动

`clear_symbol_halt` 清完 per-symbol halt 后，读全局 `get_halt_state()` 是否仍 halted；若是，把"全局仍 halt"事实透传给调用链（返回结构或结构化字段），由 `agents/trading/executor.py` 的 resume_symbol 处理 + `telegram_notifier.py` 的 `/resume_symbol` 文案回显"per-symbol 已清，但全局仍 halt，请用 /resume（带对账）解除"。

**明确不做**（out-of-scope，保持安全姿态）：
- `_halt_symbol` 仍无条件跳全局 fail-closed（真实故障该全局停）——不改。
- per-symbol halt 不加持久化——不改（拆后续 change）。
- `/resume_symbol` 不绕过 reconciliation 清全局——不改。

## 测试与同构

- 新增"加仓（含 tp_filled==1）后再跑 `_update_trailing` 不 halt 且 `take_profit==take_profit_levels[0]`"同构用例。
- 多级 levels 加仓后各级比例保持的断言。
- `clear_symbol_halt` 在全局 halt 仍在时回显提示的断言。
- CLAUDE.md 红线：核对 `event_backtest.py` 是否复刻加仓 TP 路径；若复刻则同步平移逻辑，否则 tasks 记录"加仓 TP 不进回测决策路径"的理由。
- 基线 `1066 passed` 变更后须全绿。

## 风险

- 改动集中在单函数 `add_to_position` 的 TP 段 + `clear_symbol_halt` 回显，blast radius 小。
- 主要风险是 tp_filled>0 时平移语义——已论证 safe，但必须有覆盖 tp_filled==1 加仓的测试坐实。
```

## openspec/changes/add-position-tp-sink-halt-recovery/tasks.md

- Source: openspec/changes/add-position-tp-sink-halt-recovery/tasks.md
- Lines: 1-27
- SHA256: ca35af9fb29c933942a250ffa8ee9b6e2c8c1fed55e77bff4621396cf105109d

```md
# Tasks: add-position-tp-sink-halt-recovery

## P1-01：加仓 TP 经 `_set_position_tp` 收口
- [ ] `executor.py` `add_to_position` TP 重算段（3178-3183）改为按每个 level 距 old_entry 比例平移整个 `take_profit_levels`，经 `_set_position_tp(position, new_levels[0], new_levels)` 收口
- [ ] 保留 `if old_tp and old_entry > 0` 守卫；无 levels 时回退 `[old_tp]` 单级仍经 sink
- [ ] 核对 SL 重算段（3170-3177）与新 TP 段一致性（同 distance-ratio philosophy）

## P2-02：halt 恢复语义诚实
- [ ] `executor.py` `clear_symbol_halt` 清完 per-symbol 后读全局 `halt_state`，结构化返回/透传"全局仍 halt"事实
- [ ] `agents/trading/executor.py` resume_symbol 处理链透传该提示
- [ ] `agents/trading/telegram_notifier.py` `/resume_symbol` 回显补"全局仍 halt，请用 /resume"

## 测试
- [ ] 新增/扩展用例：加仓后 `take_profit==take_profit_levels[0]` 不变量保持
- [ ] 用例：加仓后再跑 `_update_trailing` 不触发 `tp_invariant_breach` halt
- [ ] 用例：tp_filled==1（partial TP 已部分成交）时加仓，levels 平移 + tp_filled 语义一致、不 halt
- [ ] 用例：多级 levels 加仓后各级距离比例保持
- [ ] 用例：`clear_symbol_halt` 在全局 halt 仍在时回显提示

## 同构与回归（CLAUDE.md 红线）
- [ ] 核对 `event_backtest.py` 是否复刻加仓 TP 路径；复刻则同步平移逻辑，否则记录"不进回测决策路径"理由
- [ ] 全量 `python3 -m pytest -q` 须 `1066+ passed`（新增用例后基线上调）
- [ ] `compileall agents utils executor.py` 通过

## 收尾
- [ ] 更新 CLAUDE.md "当前事实" + `docs/to-do-list.md` 关闭 P1-01/P2-02（引用第五次审计报告）
- [ ] delta spec 同步至 master（归档阶段）
```

## openspec/changes/add-position-tp-sink-halt-recovery/specs/entry-drift-policy/spec.md

- Source: openspec/changes/add-position-tp-sink-halt-recovery/specs/entry-drift-policy/spec.md
- Lines: 1-36
- SHA256: ab04196d929dcbeebe22541badf2438363ceb778ba761f995b302f9350c145c3

```md
## MODIFIED Requirements

### Requirement: TP Field Single Source of Truth
All writes to `position.take_profit` and `position.take_profit_levels` SHALL
go through the single setter `_set_position_tp(position, tp_first, tp_levels)`
that enforces `position.take_profit == position.take_profit_levels[0]`. This
applies to EVERY post-open write path that mutates TP, INCLUDING
`add_to_position` (加仓), which recomputes TP against the new weighted-average
entry. Writing scalar `take_profit` without the matching `take_profit_levels`
update through the setter is prohibited. Direct mutation that violates this
invariant SHALL halt the symbol and emit a `tp_invariant_breach` risk alert
when partial_tp_1/partial_tp_2 is about to fire.

When `add_to_position` recomputes TP after a successful add, it SHALL shift
every element of `take_profit_levels` by that element's own
distance-from-old-entry ratio applied to the new entry (mirroring the SL
distance-ratio recompute), then write both fields via `_set_position_tp`. The
shift SHALL preserve multi-level structure and SHALL NOT alter `tp_filled`. An
add that occurs after a partial TP fill (`tp_filled > 0`) SHALL NOT breach the
invariant.

#### Scenario: 加仓后 TP 不变量保持，不触发误熔断
- **WHEN** 一笔已开多仓 `take_profit_levels=[L0, L1]`、`take_profit==L0`、`protection_state=='protected'`
- **AND** `add_to_position` 成功加仓推高加权均价
- **THEN** `position.take_profit == position.take_profit_levels[0]`
- **AND** 下一轮 `_update_trailing` MUST NOT 触发 `tp_invariant_breach` halt

#### Scenario: 多级 TP 加仓后各级距离比例保持
- **WHEN** 加仓前 `take_profit_levels` 各级距 old_entry 的比例为 `[d0, d1]`
- **THEN** 加仓后各级距 new_entry 的比例仍为 `[d0, d1]`（按持仓方向取 ± 号），多级结构不被压平

#### Scenario: partial-TP 已部分成交后加仓
- **WHEN** `tp_filled == 1` 且 `add_to_position` 成功
- **THEN** `tp_filled` MUST 仍为 1
- **AND** `take_profit == take_profit_levels[0]` 不变量保持
- **AND** MUST NOT 触发 `tp_invariant_breach` halt
```

## openspec/changes/add-position-tp-sink-halt-recovery/specs/tg-symbol-halt-control/spec.md

- Source: openspec/changes/add-position-tp-sink-halt-recovery/specs/tg-symbol-halt-control/spec.md
- Lines: 1-26
- SHA256: 34c00972607112b2e80dd0a01760fc2fc70da4d96d8627d6b4e2948d381cd0d5

```md
## ADDED Requirements

### Requirement: resume_symbol 全局 halt 仍在时必须诚实回显
系统 SHALL 在 `/resume_symbol <SYMBOL>` 清掉 per-symbol halt 后，若全局
`HaltState.halted` 仍为 true，使 TG 回显 MUST 明确告知"per-symbol halt 已清，但全局仍 halt，需 `/resume`
（带对账）才能恢复开新仓"，避免运维误以为已恢复交易（恢复语义陷阱）。该回显 MUST
由 TG / MultiExecutor agent 层基于其持有的 `halt_state` 判断生成。MUST NOT 改变
`clear_symbol_halt` 的返回类型（保持返回 int 项数，兼容既有调用方与测试）。MUST NOT
清除、绕过或修改全局 `HaltState`（深度 halt 语义重构属独立后续 change，本需求只补
回显诚实性）。

#### Scenario: 清 per-symbol 但全局仍 halt
- **WHEN** `_halted_symbols={"XLM-USDT-SWAP":{...}}` 且全局 `HaltState.halted==true`
- **AND** TG 收到 `/resume_symbol XLM`
- **THEN** per-symbol halt 被清（`clear_symbol_halt` 返回 1）
- **AND** 全局 `HaltState.halted` MUST 仍为 true
- **AND** TG 回显 MUST 含"全局仍 halt"提示与 `/resume` 指引

#### Scenario: 清 per-symbol 且全局未 halt 不附加提示
- **WHEN** `_halted_symbols={"XLM-USDT-SWAP":{...}}` 且全局 `HaltState.halted==false`
- **AND** TG 收到 `/resume_symbol XLM`
- **THEN** TG 回显正常确认解除该 symbol，MUST NOT 附加全局 halt 提示

#### Scenario: clear_symbol_halt 返回类型不变
- **WHEN** 任意 `/resume_symbol` 或 `_handle_resume` 调用 `clear_symbol_halt`
- **THEN** `clear_symbol_halt` 返回值 MUST 仍为 int（被清项数），既有调用方与测试不破
```

