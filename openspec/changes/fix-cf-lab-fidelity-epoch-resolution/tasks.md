# Tasks

> 详细任务在 comet-build 阶段细化。本清单为 open 阶段初始边界。

## 纪元解析分层（utils/decision_replay.py）
- [ ] 新增纪元兜底 helper（缺键按录制纪元默认补：ladder 缺→False、ev_winrate_gate_enabled 缺→True、ev_neutral_p_win 缺→0.55）
- [ ] `replay_decision` 有效 config 改三层：production_base < 纪元兜底 < config_snapshot < 扰动 override
- [ ] `utils/sequential_perturbation.py` `run_arm` 传 config 对齐（不再让单一 arm config 压过 per-record snapshot）

## 测试改纪元解析 + accept/reject 主指标
- [ ] tests/test_decision_replay.py：baseline 不再传全局 ladder pin，改纪元解析；gate 保真 ≥0.85
- [ ] tests/test_sequential_perturbation.py：同上
- [ ] 新增 accept/reject 二元保真断言 ≥0.95（实测 0.985）
- [ ] gate 严格保真降为诊断次指标（保留断言但放宽/标注语义）
- [ ] 回归 perturbation 测试确认扰动 override 仍能翻转目标旋钮

## 残余深挖（调查任务）
- [ ] 逐记录对比 range_position→ev_gate 发散记录的录制 vs 回放 ev_gate EV 内部输入/输出
- [ ] 钉死 ev_gate pass→fail 真因（已排除 capture 缺口 / ladder / ev_winrate 纪元）
- [ ] 据结论：本 change 内修复 或 记 follow-up（写入验证报告）

## 验证
- [ ] `python3 -m pytest tests/test_decision_replay.py tests/test_sequential_perturbation.py -q` 全绿
- [ ] 全量回归无退化
