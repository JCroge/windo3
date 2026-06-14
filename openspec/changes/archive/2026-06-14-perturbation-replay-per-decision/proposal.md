## Why

反事实策略实验室路线图 #3 的第一步（L3a）。L2 证明了回放能用真实 Judge 代码复现历史决策（golden master）。下一步要回答用户最初的问题——"放宽 choppy R:R 地板 1.50→1.30 这类闸门调参，到底会怎样"。

L3a 用最低风险的方式先给出部分答案：对每条录下的决策点，用其**录下的状态快照** + **扰动后的旋钮 config** 重跑真实 `_make_decision`，比对"原决策 vs 扰动决策"，量化哪些 gate 在新旋钮下会翻、翻向哪。这是 L3b（序列组合态重演，捕获级联效应）的低风险前置——L3a 把"逐决策独立扰动"做扎实，L3b 再加序列状态机。

## What Changes

- **新增逐决策扰动引擎**：复用 L2 `utils/decision_replay.py::replay_decision`，新增对同一 record 跑 baseline config 与 perturbed config 两次、用 `compare_decision` 字段分层 diff 的能力。observability-only write-only，不改 Judge 决策逻辑。
- **新增扰动翻转报表**：按 reject_reason×regime×gate 分桶统计"翻转率 + 翻转方向"（accept↔reject、各 gate 标签变化），配 L1 诚实 gate（样本量 + 置信区间，薄样本拒答）。
- 不做序列级联（每个决策点用其录下的状态快照独立评估）——明确标注为近似。

## Capabilities

### New Capabilities
- `knob-perturbation-engine`: 逐决策扰动引擎——同一 record 跑 baseline vs perturbed config 两次真实 `_make_decision`，分层 diff 出决策翻转。
- `perturbation-flip-report`: 扰动翻转分桶报表——按 reject_reason×regime×gate 统计翻转率/方向 + 诚实 gate。

### Modified Capabilities
<!-- 无：复用 L2 deterministic-replay-harness 与 L1 cf-honesty 的既有能力，本 change 为新增分析层。 -->

## Impact

- **新增代码**：扰动引擎（扩展 `utils/decision_replay.py` 或新 `utils/perturbation_replay.py`：`replay_with_perturbation(record, baseline_config, perturbed_config)` → diff）；翻转报表（扩展 `replay_report.py` 或新模块）。
- **复用既有**：L2 `replay_decision` / `compare_decision`（已成熟，Judge 决策 90% 复用）、L1 `cf_honesty_gate.summarize_bucket`、决策磁带 record（含状态快照 + 内联 LLM）。
- **保真天花板（明确标注）**：L3a 用录下的内联 LLM 输出，所以只对**非 LLM 旋钮**（R:R floor / EV 阈值 / gate 阈值 / slot 上限）确定；改 LLM prompt 类旋钮不在 L3a 范围。**逐决策独立**，不捕获级联（早期翻转改变后续状态）——留 L3b。
- **红线合规**：observability-only write-only，扰动引擎/报表严禁被任何 gate/veto/halt/rank/daily-stop 读取（守卫测试扩展，同 L1/L2）。
- **非目标（留 L3b/后续）**：序列组合态重演（slot/daily-stop/资金曲线模拟 + 反事实 PnL 反馈进 EV/cooldown + 误差累积观测）、trailing/partial-TP/risk-close 退出、L4 旋钮扫描。
