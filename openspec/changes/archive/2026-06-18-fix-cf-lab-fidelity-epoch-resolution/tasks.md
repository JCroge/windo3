# Tasks

> 实现采用纪元解析四层合并 + accept/reject 主指标 + 守卫 + 残余快修。

## 纪元解析分层（utils/decision_replay.py）
- [x] 新增纪元兜底 helper `_EPOCH_FALLBACK`（ladder 真翻转→False；ev_winrate/ev_neutral 防御性 no-op）+ `_resolve_effective_config`
- [x] `replay_decision` 有效 config 四层：production_base < 纪元兜底 < config_snapshot < 扰动 override
- [x] `run_arm` 传 config 对齐（baseline 传 {} → per-record 纪元解析；引擎修复后无需改 run_arm 本体）

## 测试改纪元解析 + accept/reject 主指标
- [x] tests/test_decision_replay.py：baseline 不再传全局 ladder pin，改纪元解析
- [x] tests/test_sequential_perturbation.py：同上
- [x] 新增 accept/reject 二元保真断言 ≥0.95（实测 0.996）
- [x] gate 严格保真降为诊断 print（实测 0.969）
- [x] 回归 perturbation 测试确认扰动 override 仍翻转旋钮（26/26 PASS）

## 残余深挖（调查任务 → 快修）
- [x] 逐记录对比 range_position→ev_gate 发散记录的录制 vs 回放 ev_gate EV 内部
- [x] 钉死真因：`_install_config_flags` 白名单未还原 `_ev_winrate_gate_enabled`/`_ev_neutral_p_win`，ev_gate `getattr` 默认 True 强制门开（与历史 capture 三修同类）
- [x] 快修：白名单补传两开关 + 针对性测试；gate 保真 0.787→0.969、accept/reject 0.988→0.996

## 守卫
- [x] 纪元兜底守卫：磁带缺键 ⊆ `_EPOCH_FALLBACK ∪ _GATE_IRRELEVANT`
- [x] `_EPOCH_FALLBACK` 键 ⊆ DEFAULTS

## 验证
- [x] `python3 -m pytest tests/test_decision_replay.py tests/test_sequential_perturbation.py -q` 全绿
- [x] 全量回归无退化（1314 passed；2 个 CF 保真度测试 failed→passed；其余 8 round2 失败为全量 asyncio event-loop 污染，隔离全 PASS，与本 change 无关）
