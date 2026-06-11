# Tasks: add-position-tp-sink-halt-recovery

## P1-01：加仓 TP 经 `_set_position_tp` 收口
- [x] `executor.py` `add_to_position` TP 重算段改为按每个 level 距 old_entry 比例平移整个 `take_profit_levels`，经 `_set_position_tp(position, new_levels[0], new_levels)` 收口（commit c341999）
- [x] 保留 `if old_tp and old_entry > 0` 守卫；无 levels 时回退 `[old_tp]` 单级仍经 sink
- [x] 核对 SL 重算段与新 TP 段一致（同 distance-ratio philosophy）

## P2-02：halt 恢复语义诚实
- [x] `clear_symbol_halt` 签名/返回类型不变（按设计保 int + 既有 spec）；诚实事实改由 resume_symbol handler 透传
- [x] `agents/trading/executor.py` resume_symbol handler 防御性读 `_halt_state`，`symbol_halt_cleared` 附 `global_halt_active`（commit 79795b8）
- [x] `agents/trading/telegram_notifier.py` `symbol_halt_cleared` 渲染按 `global_halt_active` 补"全局仍 halt，请用 /resume"

## 测试
- [x] 用例：加仓后 `take_profit==take_profit_levels[0]` 不变量保持（test_invariant_holds_after_add）
- [x] 用例：加仓后再跑 `_update_trailing` 不触发 `tp_invariant_breach` halt
- [x] 用例：tp_filled==1 加仓，levels 平移 + tp_filled 不变、不 halt（test_add_after_partial_tp_fill）
- [x] 用例：多级 levels 加仓后各级距离比例保持（test_multi_level_ratios_preserved）
- [x] 用例：`global_halt_active` 真/假两态 + clear_symbol_halt 返回 int 不破（TestResumeSymbolGlobalHaltHint + 既有 32 全绿）

## 同构与回归（CLAUDE.md 红线）
- [x] `event_backtest.py` 经核对**无加仓路径**（grep `add_to_position`/`加仓` 为空）→ 加仓 TP 重算 live-only，无同构对象需同步
- [x] 全量 `python3 -m pytest -q` = `1071 passed / 4 deselected / 1 warning`（1066 基线 + 5 新增）
- [x] `compileall executor.py agents utils` 通过

## 归档阶段事项（非 build 勾选项，散文记录）

> 以下在 comet-archive 阶段处理，不计入 build 完成勾选：
>
> - delta spec 同步至 master：comet-archive 自动执行（本 change 只动 `entry-drift-policy` + `tg-symbol-halt-control`）。
> - CLAUDE.md "当前事实" + `docs/to-do-list.md` 关闭 P1-01/P2-02（引用第五次审计报告）。
>   ⚠️ **并发避让**：另一窗口正改 P1-02/P1-03（judge.py）。这两份共享文档延到 archive，
>   只追加 P1-01/P2-02 独立行，不碰 P1-02/P1-03 行；记忆文件（非分支隔离）暂不动，待两线落地后统一。
