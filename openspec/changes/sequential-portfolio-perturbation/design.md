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
