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
