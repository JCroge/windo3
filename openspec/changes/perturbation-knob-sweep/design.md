## Context

反事实策略实验室 L4（收官）。L3b `build_delta_report(records, baseline_config, perturbed_config, price_loader, *, fidelity_threshold, ...)` 返回 `{baseline, perturbed, delta:{net_pnl,win_rate,max_drawdown}, metadata:{baseline_fidelity, untrustworthy, divergence_ratio, sequence_len, fidelity_note, ...}}`（baseline_fidelity < 阈值时 delta=None+untrustworthy）。L4 是其编排：grid 扫描 + 排名 + 诚实推荐。

红线（CLAUDE.md）：observability-only write-only；绝不自动改线上 config。

## Goals / Non-Goals

**Goals:**
- 单旋钮 grid 扫描：逐值跑 L3b，聚合 delta + 信任/样本元数据。
- 方向推荐：门控 + 排名 + actionable → 推荐或拒答（证据不足不杜撰）。
- 推荐带样本量/置信度/baseline 保真度/保真天花板。
- observability-only write-only，绝不自动应用。

**Non-Goals:**
- 多旋钮联合扫描（组合爆炸）。
- LLM 旋钮。
- 自动改线上 config（只出建议，人审）。

## Decisions（4 叉子收口）

### D1（叉子①）— 单旋钮 1D 扫描
`sweep_knob(records, knob, values, price_loader, baseline_config={})`：对 `values` 中每个 v，跑 `build_delta_report(records, {}, {knob: v}, price_loader)`，收集 `{value, delta, baseline_fidelity, untrustworthy, divergence_ratio, sequence_len}`。多旋钮组合爆炸留后续。

### D2（叉子④）— grid 显式值列表
`values` 是显式值列表（`[1.3, 1.4, 1.5, 1.6, 1.7]`），不是 range+step——显式更可控、可非均匀、避免浮点步长坑。

### D3（叉子②）— 排名/推荐判据
`recommend_direction(sweep_result, *, min_sample, actionable_min_pnl)`：
1. **门控**：剔除 `untrustworthy=True`（L3b baseline_fidelity 不足）与 `sequence_len < min_sample`（L1 诚实）。
2. **排名**：剩余 trustworthy 值按 `delta.net_pnl` 降序。
3. **actionable 判定**：最优值的 `delta.net_pnl > actionable_min_pnl`（显著正）且与 baseline（v=当前生产值或 0 扰动）相比净改善 → 输出方向推荐。否则 `verdict="no_actionable_direction"`（绝不杜撰）。
4. 输出：`{verdict, recommended_value, delta_net_pnl, confidence, sample, baseline_fidelity, fidelity_note}`。

### D4（叉子③）— 置信度派生
`confidence` 从三因子派生（observability 标签非精确概率）：`baseline_fidelity`（越高越可信）× `(1 - divergence_ratio 过高惩罚)` × 样本量档（L1 三档 INSUFFICIENT/low/actionable）。低任一 → confidence 低/拒答。报表同时报出三原始因子供人核对，不把它们藏进单一数字。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| 扫描出"最优"但其实噪声 | 门控（untrustworthy/薄样本）+ actionable CI/阈值 + 拒答 no_actionable_direction |
| 多值扫描放大过拟合（多重比较） | 单旋钮 1D 起步；报出全部值的 delta 供人看趋势非只挑最优；保真天花板随报 |
| 用户误把推荐当自动应用 | 红线：只出建议，绝不改线上 config；文档显式人审 |
| 继承 L3b 保真天花板 | fidelity_note 随每条推荐报出 |
| 红线误用 | 守卫测试扩展 |

## Migration Plan
纯新增离线编排层，无生产改动、无 schema 迁移。回滚=删模块。

## Open Questions（build 收口）
- `actionable_min_pnl` 默认阈值与 confidence 派生函数细节。
- 推荐里"方向"的措辞（value 升/降 + 量化）。
- baseline 取 v=生产默认（perturbed_config={}）作 0 扰动锚点。
