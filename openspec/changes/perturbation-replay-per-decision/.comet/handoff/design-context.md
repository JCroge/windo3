# Comet Design Handoff

- Change: perturbation-replay-per-decision
- Phase: design
- Mode: compact
- Context hash: 6a67521d9becc771955601b77ab0e5776ff53f958686e4f8f77a0d711a032ef7

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/perturbation-replay-per-decision/proposal.md

- Source: openspec/changes/perturbation-replay-per-decision/proposal.md
- Lines: 1-28
- SHA256: c84e28aec5fb4add879ea0c2130ed638e9d4ff22f0cd447e36dac81db731db08

```md
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
```

## openspec/changes/perturbation-replay-per-decision/design.md

- Source: openspec/changes/perturbation-replay-per-decision/design.md
- Lines: 1-52
- SHA256: 0d754657890963f1b33405f65e0a13204b7a745352661e4e87caac690293032f

```md
## Context

反事实策略实验室 L3a。L2 已交付 `utils/decision_replay.py`：`replay_decision(record, config)` 用 `MultiJudge.__new__` + 还原状态快照 + mock 3 外部 await + patch time → 跑真实 `_make_decision` 截获决策；`compare_decision(a, b)` 三层比对。`_install_config_flags(judge, config)` 把决策旋钮（R:R floor / EV / gate 阈值 / slot）从 config 注入。

L3a 的洞察：`replay_decision` 已经接受 `config` 参数并经 `_install_config_flags` 注入旋钮——所以"扰动一个旋钮"= **用不同 config 跑同一条 record 两次**，diff 两次决策。引擎几乎零新逻辑，价值在 baseline-vs-perturbed 编排 + 翻转聚合。

红线（CLAUDE.md）：observability-only write-only，严禁交易决策读回放/扰动产物。

## Goals / Non-Goals

**Goals:**
- 逐决策扰动：同一 record 跑 baseline vs perturbed config，分层 diff 出翻转。
- 翻转报表：按 reject_reason×regime×gate 分桶统计翻转率/方向 + 诚实 gate。
- 全程 observability-only write-only，零交易行为改动。

**Non-Goals:**
- 序列组合态重演 / 反事实 PnL 反馈 / 误差累积（L3b）。
- trailing/partial-TP/risk-close 退出建模。
- LLM 旋钮扰动（L3a 用录下内联 LLM，只确定非 LLM 旋钮）。
- L4 旋钮扫描。

## Decisions

### D1 — 扰动引擎复用 replay_decision，不重写
- `replay_with_perturbation(record, baseline_config, perturbed_config) -> dict`：调 `replay_decision(record, baseline_config)` 与 `replay_decision(record, perturbed_config)`，对两个 captured payload 调 `compare_decision`，返回 `{baseline_action, perturbed_action, flipped, diffs, flip_kind}`。
- `flip_kind`：派生分类——`accept_to_reject` / `reject_to_accept` / `gate_label_change`（action 不变但某 gate 标签变）/ `none`。
- baseline_config 默认 `{}`（= 录制时的生产默认）；perturbed_config 是要测的旋钮（如 `{"rr_floor_default": 1.30}`）。
- **理由**：引擎是纯编排，决策逻辑全在真实 `_make_decision`，零发散。

### D2 — 翻转报表 + 诚实 gate
- `build_perturbation_report(records, baseline_config, perturbed_config, *, min_sample, lowconf_sample) -> dict`：对每条 replayable record 跑引擎，按 `reject_reason|regime|side` 分桶，桶内统计翻转计数/率 + `flip_kind` 分布。
- 翻转方向是离散事件，胜率类用 Wilson 区间（复用 `cf_honesty_gate`）；薄样本桶标 `INSUFFICIENT_SAMPLE`。
- 仅 replayable record（有状态快照）纳入；缺快照计 skipped。

### D3 — 保真与范围标注（写进输出）
- 报表 metadata 带 `fidelity_note`：逐决策独立、不含级联（L3b）；只对非 LLM 旋钮确定；LLM 输出取录制内联。
- 报表带 `perturbed_knobs`（perturbed_config 的 diff）+ 样本量，使结论可判显著性。

## Risks / Trade-offs

- **[逐决策独立忽略级联]** → 明确标注近似；L3b 补序列重演。报表只声称"在录下的决策点上，旋钮使决策翻转的比率"，不声称整策略 PnL。
- **[扰动 config 注入不全]** → 复用 `_install_config_flags` 白名单；perturbed_config 的键必须是该白名单认得的旋钮，否则无效（报表标 unknown_knob）。
- **[红线误用]** → 守卫测试扩展（决策路径不读扰动引擎/报表）。
- **[样本不足过拟合]** → 复用 L1 诚实 gate，薄样本拒答。

## Migration Plan
- 纯新增离线分析层，无生产链路改动、无 schema 迁移。
- 回滚：删模块即可，不影响 L1/L2/交易。

## Open Questions
- 引擎放扩展 `decision_replay.py` 还是新 `utils/perturbation_replay.py`——build 定（倾向新模块，保持 harness 单一职责）。
- `flip_kind` 的 gate 标签全集（哪些 attribution 字段算"gate 翻转"）——build 对照 compare_decision 的 `_DISCRETE_ATTR` 定。
```

## openspec/changes/perturbation-replay-per-decision/tasks.md

- Source: openspec/changes/perturbation-replay-per-decision/tasks.md
- Lines: 1-25
- SHA256: bc45c89233a024f50e2f6e8141fec66e83e50c763916956d5525d83725ab5962

```md
# Tasks — perturbation-replay-per-decision (L3a)

> 反事实策略实验室 #3 第一步。observability-only write-only，零交易决策影响。复用 L2 harness + L1 诚实 gate。

## 1. 扰动引擎（knob-perturbation-engine）

- [ ] 1.1 新建 `utils/perturbation_replay.py`：`replay_with_perturbation(record, baseline_config, perturbed_config)` 调 L2 `replay_decision` 两次 + `compare_decision` diff
- [ ] 1.2 `flip_kind` 派生（accept_to_reject / reject_to_accept / gate_label_change / none）+ 返回 `{baseline_action, perturbed_action, flipped, flip_kind, diffs}`
- [ ] 1.3 单测（合成 fixture）：accept→reject 翻转（如收紧 rr_floor）、reject→accept（放宽）、gate 标签变化、无变化、缺快照返回不可回放

## 2. 翻转报表（perturbation-flip-report）

- [ ] 2.1 `build_perturbation_report(records, baseline_config, perturbed_config, *, min_sample, lowconf_sample)`：逐 record 跑引擎，按 reject_reason×regime×side 分桶 + flip_kind 分布
- [ ] 2.2 诚实 gate（复用 `cf_honesty_gate`）薄样本拒答；metadata 带 `perturbed_knobs` + `fidelity_note`；缺快照计 skipped
- [ ] 2.3 单测：分桶翻转统计、薄样本拒答、缺快照跳过、metadata 标注

## 3. 红线守卫 + 文档

- [ ] 3.1 扩展 `tests/test_cf_red_line_guard.py`：决策/风控路径不读 `perturbation_replay` 产物
- [ ] 3.2 docs：CLAUDE.md 红线补 L3a 声明；docs/to-do-list.md 路线图（#3 L3a 完成，L3b/L4 待做）；memory roadmap 更新

## 4. 验证

- [ ] 4.1 全量 `python3 -m pytest -q` 通过，基线不回归（当前 1201，只增不减）
- [ ] 4.2 `python3 -m compileall -q .` 通过
```

## openspec/changes/perturbation-replay-per-decision/specs/knob-perturbation-engine/spec.md

- Source: openspec/changes/perturbation-replay-per-decision/specs/knob-perturbation-engine/spec.md
- Lines: 1-45
- SHA256: a183ffc33d64edc4c9861b94615886ac0caa1915d6f71c8989d4c4485d14fa0f

```md
## ADDED Requirements

### Requirement: 逐决策扰动跑两次真实决策
系统 SHALL 对同一 decision_replay_record 用 baseline config 与 perturbed config 各跑一次真实 `_make_decision`（经 L2 `replay_decision`），并分层 diff 两次决策，复用真实 Judge 逻辑不重写。

#### Scenario: baseline 与 perturbed 各一次
- **WHEN** 对一条 replayable record 跑扰动引擎
- **THEN** 系统 SHALL 调用 `replay_decision(record, baseline_config)` 与 `replay_decision(record, perturbed_config)`，得到两个 captured 决策

#### Scenario: 复用真实决策逻辑
- **WHEN** 扰动引擎执行
- **THEN** 其 SHALL 经 `replay_decision`→真实 `_make_decision` 产生决策，SHALL NOT 另写第二份评分/gate

### Requirement: baseline 复现自检闸
系统 SHALL 对每条 record 先验证 baseline replay 复现录下的决策；不复现的 record 标 `baseline_mismatch` 并排除出翻转统计。

#### Scenario: baseline 复现失败排除
- **WHEN** baseline replay 的决策与 record 录下的 `trade_decision_output` 比对不 match
- **THEN** 系统 SHALL 标 `status=baseline_mismatch` 并 SHALL NOT 把该 record 计入翻转率（连原决策都没复现，perturbed diff 不可信）

#### Scenario: baseline 复现成功才比翻转
- **WHEN** baseline replay 复现了录下的决策
- **THEN** 系统 SHALL 才比对 baseline vs perturbed 求翻转

### Requirement: 翻转分类
系统 SHALL 用 `compare_decision` 分层比对两次决策，并派生 `flip_kind ∈ {accept_to_reject, reject_to_accept, gate_label_change, none, baseline_mismatch}`。

#### Scenario: accept↔reject 翻转
- **WHEN** baseline action 为开仓而 perturbed 为 hold（或反之）
- **THEN** `flipped=True` 且 `flip_kind` 为 `accept_to_reject` / `reject_to_accept`

#### Scenario: gate 标签变化但 action 不变
- **WHEN** 两次 action 相同但某 gate 标签（如 rr_policy / rr_floor_used）不同
- **THEN** `flip_kind=gate_label_change`，`diffs` 含该字段

#### Scenario: 无变化
- **WHEN** 两次决策一致
- **THEN** `flipped=False`，`flip_kind=none`

### Requirement: 引擎 observability-only write-only
系统 SHALL 保证扰动引擎为离线工具，严禁被任何 gate/veto/halt/rank/daily-stop 读取或进入生产决策链路。

#### Scenario: 引擎不进生产链路
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其 SHALL NOT import 或调用扰动引擎
```

## openspec/changes/perturbation-replay-per-decision/specs/perturbation-flip-report/spec.md

- Source: openspec/changes/perturbation-replay-per-decision/specs/perturbation-flip-report/spec.md
- Lines: 1-33
- SHA256: 8e74369177e79ec6b217b64eacd72e6f8bee27795a9650e4cf3d56403223454a

```md
## ADDED Requirements

### Requirement: 翻转分桶报表
系统 SHALL 对一批 record 跑扰动引擎，按 reject_reason×regime×side 分桶统计翻转计数/率与 flip_kind 分布。

#### Scenario: 分桶翻转统计
- **WHEN** 对一批 replayable record 跑扰动报表
- **THEN** 输出 SHALL 按 `reject_reason|regime|side` 分桶，每桶含翻转总数、翻转率、各 flip_kind 计数

#### Scenario: 缺快照跳过计数
- **WHEN** record 缺状态快照（不可回放）
- **THEN** 系统 SHALL 跳过该条并计入 skipped，不中断报表

### Requirement: 诚实 gate 守门
系统 SHALL 对每桶翻转结论经 L1 诚实 gate（Wilson 区间 + 三档样本），薄样本桶拒答。

#### Scenario: 薄样本拒答
- **WHEN** 某桶样本量低于阈值
- **THEN** 该桶 SHALL 标 `INSUFFICIENT_SAMPLE`，不给翻转率结论

### Requirement: 范围与保真标注
系统 SHALL 在报表 metadata 标注 L3a 的范围与保真限制。

#### Scenario: metadata 带标注
- **WHEN** 生成扰动报表
- **THEN** metadata SHALL 含 `perturbed_knobs`（perturbed_config diff）、`fidelity_note`（逐决策独立、不含级联、只对非 LLM 旋钮确定）

### Requirement: 报表 observability-only
系统 SHALL 保证报表为离线分析产物，输出严禁被任何 gate/veto/halt/rank/daily-stop 读取做交易决策。

#### Scenario: 报表不进决策
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其 SHALL NOT 读取扰动报表产物
```

