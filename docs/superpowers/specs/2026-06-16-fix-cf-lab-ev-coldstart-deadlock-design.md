---
comet_change: fix-cf-lab-ev-coldstart-deadlock
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-16-fix-cf-lab-ev-coldstart-deadlock
status: final
---

# Design Doc — fix-cf-lab-ev-coldstart-deadlock

反事实策略实验室 L3b 序列组合模拟的 **EV-gate 冷启动死锁** 技术修复方案。上游事实源见 `openspec/changes/fix-cf-lab-ev-coldstart-deadlock/proposal.md`。

## 1. 根因(已坐实到代码行)

| 环节 | 事实 |
|---|---|
| live EV gate `_get_p_win` (judge.py:3485) | `if _recent_win_rate is not None AND _total_completed_trades >= _min_trades_for_ev_gate(=10): return _recent_win_rate,"rolling"` 否则退 bayesian `(wins+2)/(trades+5)` |
| live `_recent_win_rate` 语义 | Reviewer 滚动窗口率 = 最近 `rolling_window_size=20` 笔已结算 win 数 / 窗口长。录制快照中 **0.45** |
| CF-sim 注入 (sequential_perturbation `_inject_cf_state`) | 用 `cf.to_snapshot()` 替换录制快照;`to_snapshot` 把 `_recent_win_rate` 重派生为 `_recent_wins/_total_completed_trades` = 9/52 = **0.173**(语义错配),`_seed_cf_prior` 只 seed 了 wins/total、没 seed rolling rate |
| 后果 | CF EV gate 看到 p_win≈0.17(未 seed 时 bayesian 0.40),远低于 live 0.45 → 负 EV → 拒所有开仓 → 胜率永不累计 → 冷启动死锁 |

**证据**:同一 WLD 记录直接 `replay_decision({rr_floor_default:0.3})`→`open_long`;经 `_inject_cf_state`→`hold (EV=-0.41<0.05 p_win=40%)`。`build_delta_report` perturbed `rr_floor_default=0.3` 仍 `perturbed_cf_open_count=0`。录制快照 591 条 v2 全部 `_recent_win_rate=0.45 / _recent_wins=9 / _total_completed_trades=52`。

**已证伪(勿据此改 capture)**:tape tech 捕获正确、L2 回放忠实(直接 replay 逐字复现 `rr_below_floor:1.37<1.50`)。`entry_long/short=0` 是红鲱鱼(plan 由 ma_aligned+LLM 生成)。

## 2. 修复设计

### 2.1 CF rolling 胜率(镜像 Reviewer 20 窗口)— `cf_portfolio.py`
`CounterfactualPortfolio` 新增 `_cf_win_window`(长 20 deque,存 CF 已结算 win/loss bool):
- **语义对齐 live**:`rolling_win_rate = sum(window)/len(window)`,与 Reviewer `_calculate_rolling_metrics`(最近 20 笔 wins/len)一致。
- **演化**:每笔 CF 仓结算时 `append(pnl>0)`,FIFO 顶老条目;约 20 笔后窗口 100% 是 CF 自己的结果 → 级联真实。
- **不变量**:窗口只吃 CF 自身结算结果,**绝不 per-record 注入 reality 演化计数**(L3b 最终审查修过的陷阱)。
- **`to_snapshot` 修正**:`_recent_win_rate` emit `rolling_win_rate`(窗口率),不再 emit `_recent_wins/_total_completed_trades`;`_recent_wins`/`_total_completed_trades` 保留累计语义供 bayesian fallback。cooldown 读 `_archetype_cooldown` 不读 `_recent_win_rate`,无副作用。

### 2.2 暖启动播种 — `sequential_perturbation.py::_seed_cf_prior`
序列起点用录制的 `_recent_win_rate=0.45` 等价填满窗口(9 个 True + 11 个 False),使 CF 起步即 0.45(磁带窗口前真实滚动胜率),CF 真实结果 FIFO 逐步换掉合成条目。`_seed_cf_prior` 继续 seed `_recent_wins`/`_total_completed_trades`/cooldown(供 fallback 与 cooldown 路径)。两臂共享同一播种 → delta 干净。

### 2.3 baseline_fidelity 改 gate-level 比对 — `perturbation_delta_report` / `_decision_class`
现在 `live=reject` 与 `CF-sim=hold(换 gate 拦)` 都归"非-accept"类即算复现。改为比对**触达的 gate**:取 `reject_reason` 冒号前前缀(`rr_below_floor`/`ev_gate`/`daily_bearish_required`/...),accept 仍单独一类。换 gate 拦 = 不复现 → 计入 `untrustworthy`/`fidelity_note`。使 EV-gate 误拦今后被保真指标暴露而非掩盖。

### 2.4 驱动 v2 过滤(次要)— `cf_direction_recommendation.py::load_records`
只收 `schema_version=='decision_replay_record.v2' AND tech_analysis 非空`,不盲信写入时固化的 stale `replayable` 标志(932 条 v1 旧空记录不再混入)。

## 3. 测试策略
- **端到端坐实死锁已解**:rr_below_floor 记录在 `build_delta_report` 放宽地板后 `perturbed_cf_open_count>0`。
- **gate-level 保真**:构造 CF-sim 用 EV gate 拦、live 是 rr 拦的 fixture → 计为不复现(divergence>0)。
- **不虚高 fidelity**:perturbed 臂多开仓的 fixture,验证合成种子不抬高 baseline_fidelity、delta 由各自 CF 结果驱动。
- **窗口 FIFO**:20 笔 CF 结算后窗口 100% 是 CF 结果(种子已挤出)。
- **红线守卫** `tests/test_cf_red_line_guard.py` 维持通过。
- 全量 pytest 不回退(基线 1238)。

## 4. 保真度坦白(写入避免误读)
即便修好,L3b 退出仍只近似 SL/TP/24h(漏 trailing/partial/risk-close ~10-20%);且只有 ~6% 的 rr_below_floor 单纯靠放宽地板能翻开(其余被 short_score_too_low/daily_bearish_required/15m 时机等合法 gate 过度决定)。故修复后实验室大概率仍对"放宽 R:R 地板"给 `no_actionable_direction`——但那是**可信的"不值得"结论**,而非死锁空转。两者由 `perturbed_cf_open_count>0`(死锁已解)区分。

## 5. 红线 / 不变量
- observability-only write-only:禁止任何 gate/rank/veto/halt/daily-stop 读取 CF 产物(守卫不放松)。
- 不改 live Judge 决策逻辑、不改 choppy R:R 地板 1.50、不新增 LLM 旋钮扰动、无需 event_backtest(不改策略公式)。
- 两臂同估算 → 系统性偏差在 delta 抵消的原则保持。
