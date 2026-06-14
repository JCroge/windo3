---
comet_change: sequential-portfolio-perturbation
role: technical-design
canonical_spec: openspec
---

# Sequential Portfolio Perturbation (L3b) — 技术设计

> 需求事实源是 OpenSpec：`openspec/changes/sequential-portfolio-perturbation/{proposal,design,specs/*}.md`。本文档只讲 HOW。

## 1. 范围

反事实策略实验室收官层。按时间序重放决策磁带 + 维护扰动后 CF 组合状态 → 整策略 PnL/胜率/回撤 delta。复用 L1（`resolve_counterfactual`/`CostModel`）、L2（`replay_decision`/`restore_state`）、`ArchetypeCooldown`。observability-only write-only，与真实系统完全隔离。

## 2. 模块边界

```
utils/cf_portfolio.py :: CounterfactualPortfolio
  ├─ to_snapshot() → L2 restore_state 接受的快照格式
  ├─ apply_decision(decision, ts) → 开仓占 slot / hold 不开
  ├─ resolve_due(ts) → 到期 CF 仓用 resolve_counterfactual 退出 + 反馈
  └─ 内部：CF 持仓 / slot / equity / EV 计数 / 独立 CF ArchetypeCooldown / daily-stop 累加器
utils/sequential_perturbation.py
  ├─ run_arm(records, config, prices) → 时间序模拟一臂，返回 {pnl, win_rate, max_drawdown, decisions, divergence}
  └─ build_delta_report(records, baseline_config, perturbed_config, prices) → 两臂 + delta + 保真/误差观测
```

## 3. 关键技术决策

### D1 — 状态机只模拟 `_make_decision` 读的状态
CF 字段 = L2 ~14 快照白名单。capital：`equity = initial + Σ CF 估算 net_usdt`，`_available_balance` 由 equity 派生（sizing 要）。每步 `to_snapshot()` 注入 Judge（复用 L2 `restore_state` 接受的格式）。不模拟决策不读的东西。

### D2 — 反馈：record_result 单一入口 + 独立 CF cooldown
CF 仓退出 → `CfResult.net_usdt` → 喂回：CF equity、CF EV 计数（`_recent_wins`/`_total_completed_trades`）、**独立 CF `ArchetypeCooldown` 实例** `.record_result(archetype, pnl)`、CF daily-stop 累加器。**绝不读/写真实 cooldown/daily-stop**。

### D3 — 退出近似：SL/TP/24h + 两臂同估算抵消
CF 持仓退出只用 L1 `resolve_counterfactual`（SL/TP/24h），漏 trailing/partial/risk-close（标注近似）。**缓解**：baseline 臂与 perturbed 臂用同一估算 → 系统性偏差在 **delta** 抵消。L3b 主结论是 delta，绝对值标估算。

### D4 — 误差/置信度观测
报表带：序列长度、CF 开仓数 vs 真实开仓数、`divergence_ratio`（perturbed 决策与 baseline 决策不同的比例）、估算 PnL 占比。delta 经 L1 诚实 gate（样本量 + 区间）；高 divergence / 薄样本 → low_confidence 或拒答。

### D5 — daily-stop：轻量阈值比较 + Reviewer 阈值常数
CF daily-stop 累加器按 UTC 日聚合 CF 已实现 PnL + 连亏计数；用 Reviewer 阈值常数（`daily_pnl_hard_stop`/`consecutive_loss_limit`，从 config 读）做触发比较 → CF 当日停开剩余。**阈值比较是简单数值非策略逻辑**，标注为轻量重写（Reviewer 方法消息耦合不宜复用）。

### D6 — baseline = CF-sim 同估算 + 序列保真自检（信任锚）
- 两臂都跑 CF-sim：baseline config（= 录制生产默认）/ perturbed config，唯一差异是旋钮 → delta 干净。
- **baseline 序列保真自检（关键）**：baseline-sim 每步决策与**录下决策**比对（L3a baseline 自检的序列版），统计 `baseline_fidelity`（一致率）。
  - 一致率高 → baseline-sim 跟得住现实 → delta 可信。
  - 一致率 < 阈值（如 0.8）→ sim 不可信 → 报表标 `untrustworthy` 并拒给 delta 结论。
- 这是 delta 的**信任前置闸**：L3b 信的是 delta，而 delta 可信的前提是 baseline-sim 跟得住录下现实。

## 4. 红线守卫
observability-only write-only；CF 状态机/driver/报表严禁被 gate/veto/halt/rank/daily-stop 读取；反事实决策只在 CF 内部消费，绝不 publish 真实 bus / Reviewer / RiskGuard。扩展 `tests/test_cf_red_line_guard.py`。

## 5. 数据流

```
decision_replay_tape（时间序）+ klines（价格）
  └─ run_arm(config):
       for record in 时间序:
         cf.resolve_due(record.ts)        # 先结到期 CF 仓 → 反馈
         snap = cf.to_snapshot()          # 当前 CF 状态
         decision = replay_decision(record{state=snap}, config)  # 真实 _make_decision
         cf.apply_decision(decision, record.ts)  # 开仓占 slot / hold
       → {pnl, win_rate, max_drawdown, decisions}
  └─ build_delta_report: run_arm(baseline) vs run_arm(perturbed)
       + baseline_fidelity（vs 录下决策）+ divergence_ratio + 诚实 gate + fidelity_note
```

## 6. 测试策略
- **cf_portfolio**：状态隔离 / to_snapshot 格式 / 开仓占 slot / resolve_due 退出+反馈 / 独立 cooldown record_result / daily-stop 触发停开 / equity 累计。
- **sequential_perturbation**：合成短序列时间序处理 / 开仓占 slot / 到期释放 slot / 隔离（不 publish 真实 bus）。
- **delta 报表**：两臂 delta / baseline_fidelity 计算 / 低一致率标 untrustworthy 拒答 / divergence_ratio / 高 divergence low_confidence / metadata 标注。
- **红线守卫** + **零回归**：全量 pytest ≥ 1208。

## 7. 风险 / 取舍

| 风险 | 缓解 |
|---|---|
| CF PnL 估算误差沿序列累积 | 两臂同估算 delta 抵消；divergence/置信度观测 |
| baseline-sim 偏离现实 → 整 sim 不可信 | D6 baseline 序列保真自检，低一致率拒答（信任锚） |
| 退出近似漏 trailing/partial/risk-close | 标注；delta 抵消；精确建模留后续 |
| CF 状态污染真实 | 完全隔离 + 守卫测试 |
| daily-stop 阈值重写发散 | 只重写阈值比较（数值），复用 config 常数 + 标注 |
| 序列性能 | 单旋钮两臂起步；扫描留 L4 |

## 8. Spec Patch（回写 delta spec）
- `perturbation-delta-report`：新增 baseline 序列保真自检 → 低一致率 `untrustworthy` 拒答。

## Open Questions（build 收口）
- CF 持仓 size/leverage 来源：`plan.size_usdt × leverage`，缺则默认。
- baseline_fidelity 阈值默认（0.8）与 divergence 退化函数细节。
- 价格源：klines_1s 优先 / klines 1m 退化（同 L2 driver）。
