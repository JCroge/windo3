# Tasks — fix-cf-lab-symbol-state-injection

> 骨架，comet-design 定方案后细化。

## 设计（comet-design）
- [x] brainstorm 选定方案（**采用 A-minimal**：`_inject_cf_state` 整体还原录制 _symbol_state；A-full overlay 因 last_open_time 两臂对称偏差在 delta 抵消、YAGNI 而不采）
- [x] 确认字段分类边界 + perturbed 臂级联不削弱（EV/cooldown 战绩累计未动，仍 CF 自累计）
- [x] 产出 Design Doc + delta spec（sequential-perturbation-driver，validate 通过）

## 实现
- [x] `_inject_cf_state` 的 `_symbol_state` 整体还原录制快照（一行，镜像 _regime_manager 透传）
- [N/A] CF overlay position-outcome 字段 —— A-minimal 不做（A-full 留作未来可选，last_open_time 偏差两臂相消）

## 测试
- [x] sequential baseline fidelity **0.944**（≥0.85，对照修前 0.798；甚至 > 直接 L2 0.914）
- [x] EV/cooldown 累计未受影响（改动只碰 _symbol_state 行 + 全量回归验证）
- [x] 红线守卫 `tests/test_cf_red_line_guard.py` 维持（4 passed）
- [x] 全量 pytest 回归：1255 passed（基线 1252 +3，不回退）

## 验收
- [x] 重跑 cf_direction_recommendation.py：**baseline_fidelity 0.798→0.9441，`untrustworthy=False` 跨过可信线**（实验室端到端首次可信）。L4 扫描产出真实 delta（非 None），perturbation 咬合（div 随地板降 0→0.81 增长），baseline_cf_open=2。**结论**:放宽 rr_floor_default/min_confidence 的 PnL delta≈+0.00 → **可信的"非高价值杠杆"结论**（reject 被多 gate 过度决定，放宽地板只把决策级联到其它 gate 而非盈利开仓）——独立佐证 choppy 地板 1.50 维持的合理性。区别于此前死锁空转/虚假 1.0/untrustworthy 拒答。
