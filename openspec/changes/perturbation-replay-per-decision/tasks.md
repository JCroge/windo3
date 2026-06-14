# Tasks — perturbation-replay-per-decision (L3a)

> 反事实策略实验室 #3 第一步。observability-only write-only，零交易决策影响。复用 L2 harness + L1 诚实 gate。

## 1. 扰动引擎（knob-perturbation-engine）

- [x] 1.1 新建 `utils/perturbation_replay.py`：`replay_with_perturbation(record, baseline_config, perturbed_config)` 调 L2 `replay_decision` 两次 + `compare_decision` diff
- [x] 1.2 `flip_kind` 派生（accept_to_reject / reject_to_accept / gate_label_change / none）+ 返回 `{baseline_action, perturbed_action, flipped, flip_kind, diffs}`
- [x] 1.3 单测（合成 fixture）：accept→reject 翻转（如收紧 rr_floor）、reject→accept（放宽）、gate 标签变化、无变化、缺快照返回不可回放

## 2. 翻转报表（perturbation-flip-report）

- [x] 2.1 `build_perturbation_report(records, baseline_config, perturbed_config, *, min_sample, lowconf_sample)`：逐 record 跑引擎，按 reject_reason×regime×side 分桶 + flip_kind 分布
- [x] 2.2 诚实 gate（复用 `cf_honesty_gate`）薄样本拒答；metadata 带 `perturbed_knobs` + `fidelity_note`；缺快照计 skipped
- [x] 2.3 单测：分桶翻转统计、薄样本拒答、缺快照跳过、metadata 标注

## 3. 红线守卫 + 文档

- [x] 3.1 扩展 `tests/test_cf_red_line_guard.py`：决策/风控路径不读 `perturbation_replay` 产物
- [x] 3.2 docs：CLAUDE.md 红线补 L3a 声明；docs/to-do-list.md 路线图（#3 L3a 完成，L3b/L4 待做）；memory roadmap 更新

## 4. 验证

- [x] 4.1 全量 `python3 -m pytest -q` 通过，基线不回归（当前 1201，只增不减）
- [x] 4.2 `python3 -m compileall -q .` 通过
