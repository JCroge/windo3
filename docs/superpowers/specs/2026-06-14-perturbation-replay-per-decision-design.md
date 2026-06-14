---
comet_change: perturbation-replay-per-decision
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-14-perturbation-replay-per-decision
status: final
---

# Per-Decision Perturbation Replay (L3a) — 技术设计

> 需求事实源是 OpenSpec：`openspec/changes/perturbation-replay-per-decision/{proposal,design,specs/*}.md`。本文档只讲 HOW。

## 1. 范围

反事实策略实验室 #3 第一步。对每条录下的 decision_replay_record，用其录下状态快照 + baseline/perturbed 两套旋钮 config 各跑一次真实 `_make_decision`，比对决策翻转，按 gate 分桶量化翻转率/方向。逐决策独立（不含级联，留 L3b），observability-only write-only。

## 2. 模块边界

```
utils/perturbation_replay.py
  ├─ replay_with_perturbation(record, baseline_config, perturbed_config) -> dict
  │     调 L2 replay_decision ×2 + baseline 复现自检 + compare_decision diff + flip_kind
  └─ build_perturbation_report(records, baseline_config, perturbed_config, *, min_sample, lowconf_sample) -> dict
        逐 record 跑引擎，按 reject_reason×regime×side 分桶 + L1 cf_honesty_gate
```
全部复用：L2 `replay_decision`/`compare_decision`、L1 `cf_honesty_gate.summarize_bucket`。新模块只编排，零决策逻辑。

## 3. 关键技术决策

### D1 — 引擎独立模块 `utils/perturbation_replay.py`
保持 L2 `decision_replay.py` harness 单一职责；扰动是其消费者，不混入。

### D2 — 扰动 = 换 config 跑两次
`replay_decision(record, config)` 已经 config 经 `_install_config_flags` 注入旋钮（R:R floor / EV / gate 阈值 / slot）。`replay_with_perturbation`：
- `baseline = replay_decision(record, baseline_config)`（baseline_config 默认 `{}` = 录制生产默认）
- `perturbed = replay_decision(record, perturbed_config)`（如 `{"rr_floor_default": 1.30}`）

### D3 — baseline 复现自检闸（加固）
对每条 record 先 `compare_decision(record["trade_decision_output_normalized"], baseline)`：
- 不 match → 标 `status="baseline_mismatch"`，**排除出翻转统计**（连原决策都没复现，perturbed diff 不可信）。
- match → 才比对 baseline vs perturbed 求翻转。
- 这把 L2 golden-master 变成 L3a 翻转结论的可信前置闸，顺手暴露状态快照不全的脏 record。
- 注：record 的 `trade_decision_output` 是录制原始输出；baseline replay 的 captured payload 需与之同结构比对（reject record 的输出是 hold/reject 形态，accept 是 open 形态——比对前归一）。

### D4 — flip_kind 复用 compare_decision 标签
`flip_kind ∈ {accept_to_reject, reject_to_accept, gate_label_change, none, baseline_mismatch}`。gate_label_change 的字段全集 = `compare_decision._DISCRETE_ATTR`（rr_policy/rr_floor_used/short_gate_decision/short_gate_reason/slot_type/is_probe/...），不另立。

### D5 — 翻转报表 + 诚实 gate + 保真标注
- 按 `reject_reason|regime|side` 分桶，桶内统计翻转数/率 + flip_kind 分布；翻转是离散事件，胜率类经 Wilson（`cf_honesty_gate`），薄样本桶 `INSUFFICIENT_SAMPLE`。
- metadata 带 `perturbed_knobs`（perturbed_config diff）、`baseline_mismatch_count`、`skipped_no_snapshot`、`fidelity_note`（逐决策独立不含级联；只对非 LLM 旋钮确定；LLM 取录制内联）。

## 4. 红线守卫
observability-only write-only：`perturbation_replay` 严禁被 gate/veto/halt/rank/daily-stop 读取。扩展 `tests/test_cf_red_line_guard.py`。

## 5. 测试策略
- **引擎**：accept→reject 翻转（收紧 rr_floor）/ reject→accept（放宽）/ gate_label_change / none / 缺快照不可回放 / baseline_mismatch（造一条 baseline 复现不上的）。
- **报表**：分桶翻转统计 / 薄样本拒答 / 缺快照跳过 / baseline_mismatch 排除 / metadata 标注。
- **红线守卫** + **零回归**：全量 pytest ≥ 1201。

## 6. 风险 / 取舍

| 风险 | 缓解 |
|---|---|
| 逐决策独立忽略级联 | 明确标注近似（fidelity_note）；L3b 补序列重演。报表只声称"录下决策点的翻转率"，不声称整策略 PnL |
| baseline 复现不上污染翻转 | D3 自检闸排除 baseline_mismatch |
| perturbed_config 键非白名单旋钮 | 无效 config 不生效；报表标注 perturbed_knobs 供核对 |
| 红线误用 | 守卫测试扩展 |
| 样本不足过拟合 | L1 诚实 gate 薄样本拒答 |

## 7. Spec Patch（回写 delta spec）
- `knob-perturbation-engine`：新增 baseline 复现自检 → `baseline_mismatch` 排除。

## Migration / Open
纯新增离线层，无生产改动、无 schema 迁移。回滚=删模块。无遗留 open question（Q1/Q2 已定，D3 加固已纳入）。
