# Comet Design Handoff

- Change: sequential-portfolio-perturbation
- Phase: design
- Mode: compact
- Context hash: 6dc2a5b5e08e6d13fcd204db2de1d2993e7110eefc8671d04507c64eacbf6750

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/sequential-portfolio-perturbation/proposal.md

- Source: openspec/changes/sequential-portfolio-perturbation/proposal.md
- Lines: 1-30
- SHA256: d51deca7306ce1b2f97259ffa915ad639d9884c2718be3f6017dedf8a7c45d02

```md
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
```

## openspec/changes/sequential-portfolio-perturbation/design.md

- Source: openspec/changes/sequential-portfolio-perturbation/design.md
- Lines: 1-66
- SHA256: 2d9b35098804f32a24693dd9dfa7e36d4fe2373e8ac1844ccc3523cd746bbb37

```md
## Context

反事实策略实验室 L3b（收官层）。L3a 逐决策独立扰动忽略级联；L3b 按时间序重放整条磁带 + 维护扰动后组合状态，给整策略 delta。复用地图（组合状态机 Explore 实测）：Judge `_make_decision` 90% / slot gate 95%（Judge 内部状态注入）/ `resolve_counterfactual` 99% / `CostModel` 95% / `LiveLedger` 70%（需 mock fill）/ `ArchetypeCooldown.record_result` 可复用但**绝不读 `is_cooled()` 真实状态**。

红线（CLAUDE.md）：observability-only write-only；反事实状态/消息与真实系统完全隔离。

## Goals / Non-Goals

**Goals:**
- 序列扰动 driver：时间序读磁带，注入 CF 状态给真实 `_make_decision`，推进 CF 持仓生命周期。
- CF 组合状态机：slot/capital/EV/cooldown/daily-stop + CF 持仓生命周期 + L1 估算 PnL 反馈，独立于真实状态。
- baseline-vs-perturbed delta 报表 + 误差/置信度观测。
- 全程 observability-only write-only，完全隔离。

**Non-Goals:**
- L4 旋钮扫描 + 排名 + 自动方向推荐。
- LLM 旋钮扰动。
- trailing/partial-TP/risk-close 精确退出建模（标注近似）。

## Decisions（含 5 个待 brainstorming 收口的真叉子）

### D1（叉子①）— 组合状态机保真度：只模拟 `_make_decision` 读取的状态
- **方案**：CF 状态机维护的字段 = L2 快照的 ~14 个（slot/`_open_positions`/EV 计数/cooldown/probe/`_symbol_state`/balance/regime）。每步把 CF 状态注入 Judge（复用 L2 `restore_state` 的注入路径），跑真实决策，再按决策结果更新 CF 状态。**不模拟** `_make_decision` 不读的东西。
- **slot**：95% 复用——CF 状态机维护 `_open_positions`/`_position_slots`，真实 slot gate 逻辑在 `_make_decision` 内自然生效。
- **待 brainstorming**：daily-stop / capital 模拟到什么粒度。

### D2（叉子②）— 反事实 PnL 反馈接口：record_result 单一入口 + 独立 CF cooldown
- CF 开仓 → `resolve_counterfactual`（SL/TP/24h）→ `CfResult.net_usdt` → 喂回：CF 资金累加、CF EV 计数（`_recent_wins`/`_total_completed_trades`）、`ArchetypeCooldown.record_result(archetype, pnl)`（**独立 CF cooldown 实例**，绝不碰真实）、CF daily-stop 累加器。
- **红线**：`is_cooled()` 可读但只读 CF 实例；真实 cooldown/daily-stop 状态绝不读。

### D3（叉子③）— 退出路径近似：L1 SL/TP/24h + 标注
- CF 持仓退出只用 L1 `resolve_counterfactual`（SL/TP/24h），漏 trailing/partial-TP/risk-close。**明确标注近似**；不在 L3b 建 trailing/partial（留后续）。
- **缓解（关键设计）**：baseline 臂与 perturbed 臂用**同一退出/估算方法** → 系统性偏差在 **delta** 中部分抵消。L3b 价值在 delta（旋钮带来的变化），非绝对 PnL。

### D4（叉子④）— 误差/置信度观测
- 报表带：序列长度、CF（反事实，现实没开的）开仓数 vs 真实开仓数、**divergence ratio**（与 baseline 决策不同的比例）、估算 PnL 占比。divergence 越大 → 结果越依赖估算 → 置信度越低。
- delta 结论经 L1 诚实 gate（样本量 + 区间）；高 divergence / 薄样本 → 拒答或标 low_confidence。

### D5（叉子⑤）— daily-stop 模拟
- CF daily-stop 累加器按 UTC 日聚合 CF 已实现 PnL；用 Reviewer 的**阈值常数**（`daily_pnl_hard_stop` / `consecutive_loss_limit`）做触发判定（小的阈值比较，非策略逻辑重写）→ 触发后 CF 停开当日剩余。
- **待 brainstorming**：阈值比较是轻量重写还是复用 Reviewer 方法（Reviewer 消息耦合，倾向轻量重写阈值比较 + 标注）。

### D6 — baseline 臂 = 同序列同估算
- baseline 臂跑 baseline config（= 录制生产默认，L2 golden 验证过能复现录下决策），CF PnL 同样估算。perturbed 臂跑 perturbed config。两臂唯一差异是旋钮 → delta 干净。
- baseline 臂的决策应与录下决策一致（每步可做 L3a 式 baseline 自检，divergence 计数暴露状态漂移）。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| CF PnL 估算误差沿序列累积 | 两臂同估算 → delta 抵消系统性偏差；divergence/置信度观测报出 |
| 退出近似漏 trailing/partial/risk-close | 明确标注；delta 抵消；精确建模留后续 |
| CF 状态污染真实系统 | 完全隔离：独立 CF 状态实例、CF 消息绝不进真实 bus、绝不读真实 cooldown/daily-stop（守卫测试） |
| daily-stop 阈值比较重写发散 | 只重写阈值比较（简单数值），复用 Reviewer 阈值常数 + 标注；非策略逻辑 |
| 序列重放性能（整条磁带 ×2 臂 ×N 旋钮） | 单旋钮两臂起步；性能优化留后续 |
| Judge 状态注入不全（L2 已解决主路径） | 复用 L2 `restore_state` + `_install_config_flags`；CF 状态机字段对齐 L2 快照白名单 |

## Migration Plan
- 纯新增离线模拟层，无生产链路改动、无 schema 迁移。回滚=删模块。

## Open Questions（design 阶段 brainstorming 收口）
- D1：daily-stop/capital 模拟粒度。
- D5：daily-stop 阈值比较轻量重写 vs 复用 Reviewer 方法。
- CF 持仓的 size/leverage 来源（plan.size_usdt × leverage，缺则默认）。
- divergence ratio 的精确定义与置信度退化函数。
- 一次跑几个旋钮（单旋钮两臂起步，扫描留 L4）。
```

## openspec/changes/sequential-portfolio-perturbation/tasks.md

- Source: openspec/changes/sequential-portfolio-perturbation/tasks.md
- Lines: 1-36
- SHA256: f8b243c3d422c5897ddc044b3bcf2bc88775783e2b054fdd21343dbd2b352990

```md
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
```

## openspec/changes/sequential-portfolio-perturbation/specs/counterfactual-portfolio-sim/spec.md

- Source: openspec/changes/sequential-portfolio-perturbation/specs/counterfactual-portfolio-sim/spec.md
- Lines: 1-34
- SHA256: d7a0dc90f1328395f45d47dfa3a1c2bb0113c6ed9b6281da67f05a6f3d1afc09

```md
## ADDED Requirements

### Requirement: 反事实组合状态机
系统 SHALL 维护一个反事实组合状态机，字段对齐 L2 决策快照白名单（slot 占用、`_open_positions`/`_position_slots`、EV 计数、cooldown、probe、`_symbol_state`、balance、regime），与真实系统状态完全隔离。

#### Scenario: CF 状态独立于真实
- **WHEN** CF 状态机更新（开仓/平仓/计数）
- **THEN** 其 SHALL 只改 CF 实例字段，SHALL NOT 改真实 Judge/Executor/RiskGuard 状态

#### Scenario: 状态可注入真实 Judge
- **WHEN** 序列驱动要做下一个决策
- **THEN** CF 状态机 SHALL 能以 L2 `restore_state` 接受的快照格式提供当前状态

### Requirement: CF 持仓生命周期 + L1 估算 PnL 反馈
系统 SHALL 对 CF 开仓用 L1 `resolve_counterfactual`（SL/TP/24h）估算退出与净 PnL，并把估算 PnL 喂回 CF 状态（资金、EV 计数、archetype cooldown、daily-stop 累加器）。

#### Scenario: CF 开仓推进生命周期
- **WHEN** 扰动决策开一个 CF 仓
- **THEN** 系统 SHALL 记录 CF 持仓，并在其存续窗口用 `resolve_counterfactual` 求 SL/TP/24h 退出与 `net_usdt`

#### Scenario: 估算 PnL 喂回独立 cooldown
- **WHEN** 一个 CF 仓退出
- **THEN** 系统 SHALL 调 `ArchetypeCooldown.record_result(archetype, pnl)` 于**独立 CF cooldown 实例**，SHALL NOT 读或写真实 cooldown

#### Scenario: 不读真实 daily-stop/cooldown
- **WHEN** CF 决策需要 cooldown/daily-stop 状态
- **THEN** 系统 SHALL 只读 CF 实例状态，SHALL NOT 读真实 `is_cooled()` / daily-stop 状态

### Requirement: CF daily-stop 模拟
系统 SHALL 维护 CF 当日已实现 PnL 累加器，按 Reviewer 阈值常数（daily_pnl_hard_stop / consecutive_loss_limit）触发 CF 当日停开。

#### Scenario: CF 当日亏损触发停开
- **WHEN** CF 当日累计已实现 PnL 跌破 daily_pnl_hard_stop（或连续亏损达 consecutive_loss_limit）
- **THEN** 系统 SHALL 停止当日剩余 CF 开仓，次日重置
```

## openspec/changes/sequential-portfolio-perturbation/specs/perturbation-delta-report/spec.md

- Source: openspec/changes/sequential-portfolio-perturbation/specs/perturbation-delta-report/spec.md
- Lines: 1-41
- SHA256: 0128b7e96d7bbf6981d4b895fe16fb23455889f5b955b0ca04ee536b22b53243

```md
## ADDED Requirements

### Requirement: baseline-vs-perturbed delta
系统 SHALL 用同一序列、同一 CF 估算方法跑 baseline config 与 perturbed config 两臂，输出 PnL/胜率/回撤的 delta（perturbed − baseline）。

#### Scenario: 两臂同估算求 delta
- **WHEN** 跑一次扰动评估
- **THEN** 系统 SHALL 对 baseline 与 perturbed 各跑一遍序列模拟（同退出/估算方法），输出净 PnL/胜率/最大回撤的 baseline、perturbed 与 delta

#### Scenario: delta 优先于绝对值
- **WHEN** 报告结论
- **THEN** 系统 SHALL 以 delta 为主结论（系统性估算偏差两臂抵消），绝对值标为估算

### Requirement: baseline 序列保真自检（delta 信任锚）
系统 SHALL 统计 baseline 臂的每步决策与录下决策的一致率（`baseline_fidelity`）；一致率低于阈值时标 `untrustworthy` 并拒给 delta 结论。

#### Scenario: 高一致率 delta 可信
- **WHEN** baseline-sim 决策与录下决策一致率 ≥ 阈值（默认 0.8）
- **THEN** 系统 SHALL 给出 delta 结论，并随报告报出 `baseline_fidelity`

#### Scenario: 低一致率拒答
- **WHEN** baseline-sim 与录下决策一致率 < 阈值
- **THEN** 系统 SHALL 标 `untrustworthy` 并 SHALL NOT 给 delta 方向结论（baseline-sim 跟不住现实，delta 不可信）

### Requirement: 误差/置信度观测
系统 SHALL 量化结果对估算的依赖度并随结论报出。

#### Scenario: divergence 与置信度
- **WHEN** 生成 delta 报告
- **THEN** 报告 SHALL 含序列长度、CF 开仓数 vs 真实开仓数、divergence_ratio（与 baseline 决策不同的比例）、估算 PnL 占比，并经 L1 诚实 gate；高 divergence / 薄样本 SHALL 标 low_confidence 或拒答

#### Scenario: 保真标注
- **WHEN** 输出 delta 报告
- **THEN** metadata SHALL 含 `perturbed_knobs` + `fidelity_note`（退出仅 SL/TP/24h、误差沿序列累积、漏 trailing/partial/risk-close）

### Requirement: 报表 observability-only
系统 SHALL 保证 delta 报表为离线分析产物，严禁被任何 gate/veto/halt/rank/daily-stop 读取做交易决策。

#### Scenario: 报表不进决策
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其 SHALL NOT 读取 delta 报表产物
```

## openspec/changes/sequential-portfolio-perturbation/specs/sequential-perturbation-driver/spec.md

- Source: openspec/changes/sequential-portfolio-perturbation/specs/sequential-perturbation-driver/spec.md
- Lines: 1-30
- SHA256: 2c89c57981e747f743ea55dd33f0411f62d1ec8c2e4ed540b846024a328a4fc8

```md
## ADDED Requirements

### Requirement: 时间序磁带驱动
系统 SHALL 按时间顺序读决策磁带，对每条 record 用扰动 config + 当前 CF 状态注入真实 `_make_decision` 重决策，再按决策结果推进 CF 组合状态。

#### Scenario: 时间序重放
- **WHEN** driver 跑一条扰动序列
- **THEN** 系统 SHALL 按 record timestamp 升序处理，每步用 CF 状态机当前状态（非录下快照）注入 Judge

#### Scenario: 复用真实决策
- **WHEN** 每步重决策
- **THEN** 系统 SHALL 经 L2 `replay_decision`（注入 CF 状态）→ 真实 `_make_decision`，SHALL NOT 另写决策逻辑

#### Scenario: 决策推进 CF 状态
- **WHEN** 扰动决策为开仓且 CF slot/daily-stop 允许
- **THEN** 系统 SHALL 在 CF 状态机开一个 CF 仓并占 slot；为 hold/reject 则不开

### Requirement: 退出推进与到期解析
系统 SHALL 在序列推进中按时间解析到期/触发的 CF 持仓退出，更新 CF 状态。

#### Scenario: 到期 CF 仓解析
- **WHEN** 序列时间推进越过某 CF 仓的 SL/TP/24h 退出点
- **THEN** 系统 SHALL 解析其退出、计净 PnL、释放 slot、喂回反馈

### Requirement: driver observability-only write-only
系统 SHALL 保证序列 driver 为离线工具，反事实消息绝不进真实总线，严禁被任何 gate/veto/halt/rank/daily-stop 读取。

#### Scenario: 不进真实总线
- **WHEN** driver 重决策产生决策 payload
- **THEN** 系统 SHALL 只在 CF 内部消费，SHALL NOT publish 到真实 bus / Reviewer / RiskGuard
```

