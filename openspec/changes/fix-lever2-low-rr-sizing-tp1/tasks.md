# Tasks: fix-lever2-low-rr-sizing-tp1

- [ ] 1. 写失败测试：lever2 开 + 阶梯 rr≥1.5 但 TP1 rr<1.5 + low_rr_policy 的 plan → 断言仍走缩仓（size_usdt 缩、leverage 降）；当前(阶梯耦合)会 fail。
- [ ] 2. 修两处缩仓块（`judge.py:1486` 主路径 + `3038` `_apply_regime_policy`）：缩仓 `if` 条件 + `rr_scale` 用 `plan.get('effective_rr_tp1', rr)`；地板 gate 不变。
- [ ] 3. 根因消除核对 + 全量 pytest 绿（lever2 关零回归）+ event_backtest 非回归。
