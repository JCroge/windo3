---
comet_change: perturbation-knob-sweep
role: technical-design
canonical_spec: openspec
---

# Knob Sweep + Direction Recommend (L4) — 技术设计

> 需求事实源是 OpenSpec：`openspec/changes/perturbation-knob-sweep/{proposal,design,specs/*}.md`。本文档只讲 HOW。

## 1. 范围

反事实策略实验室收官层。纯编排：在 L3b `build_delta_report` 之上单旋钮 grid 扫描 + 诚实门控 + 方向推荐。observability-only write-only，绝不自动改线上 config。

## 2. 模块边界

```
utils/knob_sweep.py
  ├─ sweep_knob(records, knob, values, price_loader, *, baseline_config={}, ...) -> list[dict]
  │     逐值跑 build_delta_report，收集 {value, delta, baseline_fidelity, untrustworthy, divergence_ratio, sequence_len}
  └─ recommend_direction(sweep_result, *, min_sample, actionable_min_pnl) -> dict
        门控 + 排名 + 多重比较守卫（连贯趋势）+ confidence 三因子 → recommend / no_actionable_direction
```
复用：L3b `build_delta_report`（逐值）、L1 `cf_honesty_gate`（样本/区间）。新模块只编排，零决策逻辑。

## 3. 关键技术决策

### D1（叉子①②）— 单旋钮 1D 扫描，显式值列表
`sweep_knob(records, knob, values, price_loader, baseline_config={})`：对 `values`（显式列表，可非均匀）每个 v 跑 `build_delta_report(records, baseline_config, {knob: v}, price_loader)`，收集每值 delta + 信任/样本元数据。多旋钮组合爆炸留后续。

### D2（叉子③）— 门控 + 排名 + actionable
`recommend_direction`：
1. 门控：剔 `untrustworthy=True`（L3b baseline_fidelity 不足）与 `sequence_len < min_sample`（L1 诚实）。
2. 排名：剩余按 `delta.net_pnl` 降序。
3. actionable：最优值 `delta.net_pnl > actionable_min_pnl`（显著正）→ 候选推荐；否则 `no_actionable_direction`。

### D3（多重比较守卫，L4 诚实核心）— 连贯趋势才推荐，孤峰拒答
L4 一次扫 N 值挑最优 = 多重比较选择性偏差 → 必须守卫：
- **报出全部值的 delta 曲线**（`all_values` 字段，看趋势非只看赢家）。
- **连贯趋势判定**：最优值不是孤立尖刺——其相邻值的 delta 应同向（单调或局部连贯），否则疑似噪声标 `isolated_spike` 拒推荐。
- **样本/置信门随值数收紧**：`effective_min_pnl = actionable_min_pnl * (1 + k * len(values))`（测的值越多门槛越高）。
- 只在「actionable + 连贯趋势 + 足量证据」三者同时满足才 `verdict=recommend`。

### D4（叉子④）— confidence 三因子透明
confidence 从 `baseline_fidelity`（越高越可信）× divergence 惩罚（divergence 过高降权）× 样本档（L1 三档）派生；**同时报出三原始因子**，不藏单一数字。

## 4. 红线守卫
observability-only write-only；`knob_sweep` 严禁被 gate/veto/halt/rank/daily-stop 读取；推荐**绝不自动应用到线上 config**（只出建议，人审）。扩展 `tests/test_cf_red_line_guard.py`。

## 5. 数据流

```
records（决策磁带）+ klines（价格）+ knob + values[]
  └─ sweep_knob: for v in values → build_delta_report({}, {knob: v}) → 收集每值 delta+元数据
  └─ recommend_direction:
       门控（剔 untrustworthy/薄样本）→ 排名 → 多重比较守卫（连贯趋势/孤峰）→ confidence 三因子
       → {verdict, recommended_value, delta_net_pnl, confidence, sample, baseline_fidelity,
          all_values, fidelity_note}  # all_values=全貌；证据不足→no_actionable_direction
```

## 6. 测试策略
- **sweep_knob**：逐值跑 + 聚合（合成短序列 fixture）、显式值列表、untrustworthy 值带标记。
- **recommend_direction**：actionable+连贯趋势给推荐 / 孤立尖刺拒答 / 无 trustworthy 拒答 / 改善不显著拒答 / 薄样本剔除 / 三因子透明 / all_values 全貌随报。
- **红线守卫** + **零回归**：全量 pytest ≥ 1217。

## 7. 风险 / 取舍

| 风险 | 缓解 |
|---|---|
| 扫一排挑最高=过拟合噪声 | D3 多重比较守卫：连贯趋势 + 孤峰拒答 + 报全貌 + 门随值数收紧 |
| 用户误把推荐当自动应用 | 红线：只建议绝不改线上 config，文档显式人审 |
| 继承 L3b 保真天花板 | fidelity_note 随每条推荐报出 |
| 红线误用 | 守卫测试扩展 |

## Migration / Open
纯新增离线编排层，无生产改动、无 schema 迁移。回滚=删模块。
- `actionable_min_pnl` 默认 + 连贯趋势判定细节（相邻同向 vs 单调）+ confidence 派生权重在 build 收口。
- baseline 取 `perturbed_config={}`（生产默认）作 0 扰动锚点。
