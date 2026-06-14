# Tasks — sequential-portfolio-perturbation (L3b)

> 反事实策略实验室 #3 第二步（收官层）。observability-only write-only，完全隔离。
> 深度技术决策（状态机粒度、daily-stop 阈值、divergence 定义）在 comet-design 的 Superpowers Design Doc 收口。

## 1. 反事实组合状态机（counterfactual-portfolio-sim）

- [ ] 1.1 新建 `utils/cf_portfolio.py`：`CounterfactualPortfolio` 维护 CF 持仓 + slot + capital + EV 计数 + 独立 cooldown + daily-stop 累加器（字段对齐 L2 快照白名单）
- [ ] 1.2 `to_snapshot()`：以 L2 `restore_state` 接受的快照格式导出当前 CF 状态
- [ ] 1.3 `open_cf_position(decision)` / `resolve_due(now)`：开仓占 slot；到期用 L1 `resolve_counterfactual` 退出 + 净 PnL
- [ ] 1.4 反馈：退出 PnL 喂回 capital / EV 计数 / 独立 CF `ArchetypeCooldown.record_result` / daily-stop 累加器（绝不读真实状态）
- [ ] 1.5 CF daily-stop：当日累计 PnL 跌破阈值（Reviewer 阈值常数）→ 停当日剩余开仓
- [ ] 1.6 单测：状态隔离、to_snapshot 格式、开/退/反馈、daily-stop 触发、cooldown 独立

## 2. 序列扰动 driver（sequential-perturbation-driver）

- [ ] 2.1 新建 `utils/sequential_perturbation.py`：时间序读磁带，每步 `cf.to_snapshot()` 注入 → L2 `replay_decision`（CF 状态）→ 真实决策 → `cf.apply_decision`
- [ ] 2.2 退出推进：每步先 `cf.resolve_due(now)` 解析到期 CF 仓
- [ ] 2.3 完全隔离：CF 决策只内部消费，绝不 publish 真实 bus
- [ ] 2.4 单测（合成短序列 fixture）：时间序处理、开仓占 slot、到期退出释放 slot、隔离守卫

## 3. delta 报表（perturbation-delta-report）

- [ ] 3.1 `build_delta_report(records, baseline_config, perturbed_config)`：两臂同序列同估算 → PnL/胜率/回撤 baseline/perturbed/delta
- [ ] 3.2 误差观测：序列长度、CF vs 真实开仓数、divergence_ratio、估算 PnL 占比 + L1 诚实 gate + fidelity_note metadata
- [ ] 3.3 单测：两臂 delta、divergence 计数、高 divergence low_confidence、metadata 标注

## 4. 红线守卫 + 文档

- [ ] 4.1 扩展 `tests/test_cf_red_line_guard.py`：决策/风控路径不读 cf_portfolio / sequential_perturbation 产物
- [ ] 4.2 docs：CLAUDE.md 红线补 L3b 声明；docs/to-do-list.md 路线图（#3 完成，L4 待做）；memory roadmap 更新

## 5. 验证

- [ ] 5.1 全量 `python3 -m pytest -q` 通过，基线不回归（当前 1208，只增不减）
- [ ] 5.2 `python3 -m compileall -q .` 通过
