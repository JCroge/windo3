# Tasks — perturbation-knob-sweep (L4)

> 反事实策略实验室 #4 收官层。observability-only write-only，绝不自动改线上 config。复用 L3b build_delta_report + L1 诚实 gate。

## 1. 旋钮扫描引擎（knob-sweep-engine）

- [ ] 1.1 新建 `utils/knob_sweep.py`：`sweep_knob(records, knob, values, price_loader, *, baseline_config={})` 逐值跑 L3b `build_delta_report`，收集 `{value, delta, baseline_fidelity, untrustworthy, divergence_ratio, sequence_len}`
- [ ] 1.2 单测（合成短序列 fixture）：逐值跑 + 聚合、显式值列表、untrustworthy 值带标记

## 2. 方向推荐器（direction-recommender）

- [ ] 2.1 `recommend_direction(sweep_result, *, min_sample, actionable_min_pnl)`：门控（剔 untrustworthy/薄样本）+ 按 delta 净 PnL 排名 + actionable 判定 → recommend / no_actionable_direction
- [ ] 2.2 confidence 三因子派生（baseline_fidelity × divergence 惩罚 × 样本档）+ 报出三原始因子 + fidelity_note
- [ ] 2.3 单测：actionable 给推荐、无 trustworthy 拒答、改善不显著拒答、三因子透明、薄样本剔除

## 3. 红线守卫 + 文档

- [ ] 3.1 扩展 `tests/test_cf_red_line_guard.py`：决策/风控路径不读 `knob_sweep` 产物
- [ ] 3.2 docs：CLAUDE.md 红线补 L4 声明（绝不自动改线上 config）；docs/to-do-list.md 路线图（#4 完成 = 实验室 L1-L4 全收官）；memory roadmap 标 L4 完成

## 4. 验证

- [ ] 4.1 全量 `python3 -m pytest -q` 通过，基线不回归（当前 1217，只增不减）
- [ ] 4.2 `python3 -m compileall -q .` 通过
