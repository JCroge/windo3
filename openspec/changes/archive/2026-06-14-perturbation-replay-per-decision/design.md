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
