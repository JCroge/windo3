## Why

CF 反事实实验室的两个保真度测试跌破 0.85 阈值（`test_sequential_baseline_fidelity_restored` fidelity=0.732、`test_production_baseline_restores_fidelity` 同失败，base-ref 1bbbc24 即失败，与 rotation change 无关）。若 lab 再度 untrustworthy，据其做的方向推荐不可信。

explore 阶段（全只读实测）定位三层：

1. **主因（纪元 pin bug）**：`utils/decision_replay.py:96` `effective = {**base, **(config or {})}` 把测试传的 `{"ladder_rr_enabled": False}` 作扰动 override **无条件压过** `record.config_snapshot`。磁带横跨两纪元（1655 旧 v2 无 snapshot + 1189 新 v3 含 snapshot 且 `ladder_rr_enabled=True`，录于 06-17 lever2 默认开之后）。全局 pin ladder=False 对新纪元记录系统性发散。实测：global_false **0.729** / naked **0.525** / 逐记录纪元解析 **0.890**（过阈值，接近测试注释期望的 ~0.91）。

2. **可信度被严格指标低估**：accept/reject 二元保真 v3=**0.991** / full=**0.985**——lab 对方向（开/不开仓）决策其实可信。gate 严格保真（哪个门拦）惩罚了"同为 reject、仅门归因不同"的情况，低估了真实可信度。

3. **残余（range_position→ev_gate，占 v3 不一致 84%/203 次，均 reject→reject）**：已**证伪** capture 缺口（字段 `position_in_24h_range=0.1755` 在 `tech_analysis.entry_context/short_context` 都录上）与 ev_winrate 纪元错配（补该键纪元值后 v3 保真纹丝不动）。机制收窄到 **ev_gate EV 计算在回放 pass→fail**，真因待逐记录追 EV 内部。

附带发现：config_snapshot 纪元不完整——`ev_winrate_gate_enabled`/`ev_neutral_p_win` 仅在 298/1205 条 v3 记录里（06-18「EV胜率门解耦」才加进 DEFAULTS）。泛化问题：**任何"录制后才进 snapshot 的键"，在缺键记录回放时会用当前 production 默认而非录制纪元默认**——默认翻转即漂移。

## What Changes

- **修纪元解析分层**：`replay_decision` 的有效 config 合并从"override 无条件压过 snapshot"改为正确三层——`production_base_config()` < **纪元兜底**（缺键按录制纪元默认补齐：`ladder_rr_enabled` 缺→False、`ev_winrate_gate_enabled` 缺→True 等）< `record.config_snapshot` < **真扰动 override**（CF 实验扰动机制保持，仍在最顶层）。`run_arm` 同步对齐传 config 方式。
- **两个失败测试改用纪元解析**：baseline 回放不再传全局 `{"ladder_rr_enabled": False}` pin，改由 harness 逐记录纪元解析。
- **新增 accept/reject 二元保真为主可信度指标**（SHALL ≥0.95，实测 0.985），gate 严格保真降为诊断性次指标（保留但不作硬可信门，或放宽阈值并标注其语义）。
- **range_position→ev_gate 残余深挖（调查任务）**：逐记录对比录制 vs 回放的 ev_gate EV 内部输入/输出，钉死 pass→fail 真因；据结果决定本 change 内修复或记 follow-up（起点证据：非 capture 缺口、非 ladder/ev_winrate 纪元）。

## Capabilities

### New Capabilities

<!-- 无新 capability -->

### Modified Capabilities

- `deterministic-replay-harness`: 「回放有效 config 与 live 生产一致」需求修订——缺键 fallback 从"当前 production 基线"改为"录制纪元默认"（抗默认漂移），明确纪元兜底与扰动 override 的分层顺序；新增 accept/reject 二元保真作为主可信度判据。

## Impact

- **代码**：`utils/decision_replay.py`（`replay_decision` 有效 config 三层合并 + 纪元兜底 helper）、`utils/sequential_perturbation.py`（`run_arm` 传 config 对齐）。
- **测试**：`tests/test_decision_replay.py`、`tests/test_sequential_perturbation.py`（baseline 改纪元解析 + 新增 accept/reject 断言）。
- **observability-only**：CF lab 全程离线、write-only，不进生产决策链路；无 live 行为变更，不需重启交易进程。
- **诊断产物**：range_position/ev_gate 深挖结论记入验证报告或 follow-up。
- **下游**：恢复 lab 可信后，`cf_direction_recommendation.py` 等方向推荐工具结论方可信赖。
