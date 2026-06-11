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
