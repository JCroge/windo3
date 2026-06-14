## Why

反事实策略实验室路线图 #3 第二步（L3b），整个实验室的收官层。L3a 已能回答"在录下的决策点上，旋钮使决策翻转的比率"，但它是**逐决策独立**的——忽略级联：早期一个翻转改变后续的 slot 占用、资金、EV 计数、cooldown，进而改变后续决策。要回答用户最初的真问题——"放宽 choppy R:R 地板对**整个策略**的真实盈亏到底怎样"——必须按时间顺序重放整条磁带、维护一个扰动后累积的组合状态。

关键洞察（探索确认）：**市场（每个时间点的 tech_analysis）是录死固定的；L3b 扰动的是系统策略和由此累积的内部状态。** 所以按时间序喂【录下的 tech】+【扰动后累积的状态】给真实 `_make_decision`（复用 L2 harness），反事实开的仓用 L1 估算 PnL 喂回组合状态，给真实整策略 PnL/胜率/回撤 **delta 曲线**。

## What Changes

- **新增序列扰动 driver**：按时间顺序读决策磁带，维护扰动后的模拟组合状态，逐 record 用 perturbed config + CF 状态重决策，处理决策（开/拒），推进 CF 持仓生命周期。
- **新增反事实组合状态机**：CF 持仓集合、slot 占用、资金/equity、EV 计数、独立 cooldown、daily-stop 累加器；CF 开仓 → L1 `resolve_counterfactual` 退出 → 估算 PnL 喂回（资金/EV/cooldown.record_result/daily-stop）。
- **新增 baseline-vs-perturbed delta 报表**：两臂（baseline config / perturbed config）跑同一序列、用**同一 CF 估算方法** → PnL/胜率/回撤 delta + 误差/置信度观测。
- 全程 observability-only write-only；反事实状态/消息与真实系统**完全隔离**（绝不进真实 bus/Reviewer/RiskGuard，绝不读真实 cooldown/daily-stop）。

## Capabilities

### New Capabilities
- `counterfactual-portfolio-sim`: 反事实组合状态机——CF 持仓生命周期 + slot/capital/EV/cooldown/daily-stop 模拟 + L1 估算 PnL 反馈，独立于真实状态。
- `sequential-perturbation-driver`: 时间序驱动——读磁带、注入 CF 状态给真实 `_make_decision`、推进生命周期、积累两臂结果。
- `perturbation-delta-report`: baseline-vs-perturbed 整策略 delta（PnL/胜率/回撤）+ 误差累积/置信度观测 + L1 诚实 gate。

### Modified Capabilities
<!-- 无：复用 L1 counterfactual-pnl、L2 deterministic-replay-harness、L3a 既有能力，本 change 为新增序列模拟层。 -->

## Impact

- **新增代码**：CF 组合状态机（如 `utils/cf_portfolio.py`）；序列 driver（如 `utils/sequential_perturbation.py`）；delta 报表（扩展或新模块）。
- **复用既有**：L2 `replay_decision`（注入 CF 状态而非录下快照）、L1 `resolve_counterfactual` + `CostModel`、`LiveLedger`（记账 pattern，mock fill）、`ArchetypeCooldown.record_result`（喂 PnL，**绝不读 is_cooled 真实状态**）。
- **保真天花板（明确标注）**：CF PnL 只 SL/TP/24h 退出，漏 trailing/partial-TP/risk-close（~10-20% 交易差异），误差沿序列累积。**缓解**：两臂用同一估算方法 → 系统性偏差在 **delta** 中部分抵消（L3b 价值在 delta 非绝对值）。
- **红线合规**：observability-only write-only；CF 状态机/driver/报表严禁被任何 gate/veto/halt/rank/daily-stop 读取；反事实消息绝不进真实总线（守卫测试扩展）。
- **非目标（留 L4/后续）**：旋钮扫描 + 排名 + 自动方向推荐（L4）；LLM 旋钮扰动；trailing/partial-TP/risk-close 精确退出建模。
