# Comet Design Handoff

- Change: trend-entry-levers-default-on
- Phase: design
- Mode: compact
- Context hash: 1de7f89db4bee4b141d67cc970847253fd2b09ccc3daf97a7cc76326f287bc7a

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/trend-entry-levers-default-on/proposal.md

- Source: openspec/changes/trend-entry-levers-default-on/proposal.md
- Lines: 1-36
- SHA256: efd1a064d209b0b2bfd5af7169db6862fa58f4ae2270ef6df76cb8d81c4cfd7a

```md
# Proposal: trend-entry-levers-default-on

## Why

`trend-entry-rr-fidelity`（2026-06-17 归档）实现了两个入场杠杆但**默认关**，因为当时无法有把握验证。后续诊断把验证补齐了：

- **lever2（`ladder_rr_enabled`）是修口径 bug**：`effective_rr` 用 TP1-only，而 executor 真实是 **50% @TP1 / 25% @TP2 / 25% trailing** 阶梯离场（`agents/trading/executor.py:1354`），系统性低估趋势单 R:R（HYPE 1.19 vs 真实几何 1.58）。lever2 唯一的乐观成分"TP2 必达"已定价：被 `rr_below_floor` 拒的干净趋势 long 实测 **P(达TP2)=68% / P(TP2|达TP1)=90%**，且把 TP2/TP3 按到达频率打折后 R:R 仍 **1.76~1.80**（对频率不敏感，因 TP1 50% 权重 + 第3档封顶 +1R 已扛大头）。rejected 流忠实 A/B 在**保守 TP1 结算**（零 TP2 信用）下仍 **+0.21R/簇含亏单**。
- **lever1（`path_evidence_aligned_enabled`）补授干净趋势 1.30 对齐地板**：根因是 regime 误判 choppy + htf/daily bias 漏报，让干净趋势拿 default 1.50。lever1 用入场前客观路径证据补授 1.30。**但 lever1 验证最弱**——其目标人群（中性 bias + 干净趋势）当时在 CF 回放磁带里隔离不出来，`trend-entry-rr-fidelity` 只为它加了 `tech_context` 埋点等数据累积。

这是一个**趋势跟随系统**，却用一个系统性给趋势单打低分的测量挡掉自己的核心 edge。把（至少）lever2 默认开，是把口径修对。

## What

把入场杠杆的 config 默认值从关改为开。**当前两个 flag 不在 `config_loader.DEFAULTS` 中**，仅靠 `judge.py:169,174` 的 `config.get(..., False)` 兜底默认关；故"默认开"= 把它们加入 `config_loader.DEFAULTS = True`（配置项新增）+ 对齐 judge 兜底。

## 待设计阶段（brainstorming）拍板的关键决策

1. **范围：lever2-only vs lever1+lever2。** lever2 验证扎实、是口径修正；lever1 验证弱、但 HYPE 等需它的 1.30 地板才能真正落地开仓（lever2 把 R:R 修高，但部分干净趋势单仍 < 1.50 default，需 lever1 降地板）。权衡：先上稳的 lever2 看效果、lever1 等埋点数据；还是两个一起上。
2. **验证方法张力。** `event_backtest.py` 有自己的 `_build_plan`（line 579），**不复用** Judge 的 `_compute_ladder_rr`/`_select_rr_floor`，且跑 RobustStrategy MA 信号 ≠ 线上 LLM-Judge → 对本口径改动**已知失真**。须按 CLAUDE.md 红线跑 event_backtest（至少做非崩溃/回归），但**真正验证证据 = rejected 流 A/B（已 +0.21R/簇）+ tier 定价（已做）+ paper 前向**。设计须明确：event_backtest 是红线合规项，不是通过凭证。
3. **风控链确认。** lever1 授 <1.5 地板的 long 必经 `low_rr_policies` 缩仓/降杠杆/独立 slot（`judge.py:1480, 3027` 两处已含 `long_aligned_path_evidence`）。默认开后须确认这条链对新开放的趋势单正确生效。

## Capabilities

修改既有 capability `trend-aligned-rr-floor`（lever1）与 `ladder-weighted-rr`（lever2）的默认启用场景（从默认关 → 默认开）。delta spec 在 design 阶段定。

## Impact

- **改 live 开仓决策**：默认开后，被 R:R 地板误拒的干净趋势单会开始开仓。这是预期效果，也是风险点——须 paper 前向 + 小额灰度观察。
- 非趋势单：lever2 的 ladder 口径影响**所有** open 决策的 R:R 评分（不只趋势），须确认对 scalp/震荡单无意外放开。
- 风控：低 R:R 单走缩仓/降杠杆/独立 slot，单笔风险受控。

## Non-goals

- 不做 lever2 v2 概率频率校准（已证 R:R 对频率不敏感，不值得为它等数据）。
- 不重写 event_backtest 使其复用 Judge plan 逻辑（可作为独立 follow-up）。
- 不改 R:R 地板**阈值**（1.50/1.30 数值不动，已多次证伪降阈值无效）。
```

## openspec/changes/trend-entry-levers-default-on/design.md

- Source: openspec/changes/trend-entry-levers-default-on/design.md
- Lines: 1-24
- SHA256: beafdf836002b748395da9034183a44989b2b6f71864aa64ebf9d88503979713

```md
# Design (high-level): trend-entry-levers-default-on

> 高层方向。深度技术设计 + 范围/验证决策由 comet-design（brainstorming）产出 Design Doc 后定稿。

## 改动点（机械部分）

- `utils/config_loader.py`：把 `ladder_rr_enabled`（lever2）、视决策可能含 `path_evidence_aligned_enabled`（lever1）加入 `DEFAULTS`，值 `True`；加入 `HARD_LIMITS`/env 覆盖映射（与现有 bool flag 一致），保留可经 env 关闭的逃生阀。
- `agents/trading/judge.py:169,174`：兜底默认与 DEFAULTS 对齐（`config.get(..., True)`），保证无 config 时行为一致。
- 不改 lever 本体逻辑（`_compute_ladder_rr`/`_select_rr_floor`/`low_rr_policies` 已实现且接好）。

## 待 brainstorming 定的设计决策

1. **范围**：DEFAULTS 里开一个（lever2）还是两个（lever1+lever2）。
2. **验证**：event_backtest 同构的深度（仅回归/非崩溃 vs 端口 lever 逻辑进 event_backtest 的 `_build_plan`），以及主验证证据栈（rejected 流 A/B + tier 定价 + paper 前向）如何在验证报告中呈现。
3. **灰度策略**：默认开后是否配合 env 逃生阀 + paper 前向先行 + 小额 live 观察窗口。

## 数据流（不变）

`tech_analysis → Judge._build_plan`（effective_rr 走 ladder 口径）`→ _select_rr_floor`（趋势补授 1.30）`→ low_rr_policies`（缩仓/降杠杆/独立 slot）`→ trade_decision.v2`。本 change 只改默认开关与 config 落点，不改链路结构。

## 风险与回滚

- 回滚 = env `LADDER_RR_ENABLED=false` / `PATH_EVIDENCE_ALIGNED_ENABLED=false` 即时关闭，无需改代码（设计须保留 env 逃生阀）。
- 主风险：默认开后对**非趋势单**的 R:R 评分也变（lever2 全局生效），须回归 + event_backtest 确认无意外放开。
```

## openspec/changes/trend-entry-levers-default-on/tasks.md

- Source: openspec/changes/trend-entry-levers-default-on/tasks.md
- Lines: 1-10
- SHA256: c3f2d49316daf32f90e28c1acac747f6fef694ca35af6233fce7c738fa8205de

```md
# Tasks: trend-entry-levers-default-on

> 初始任务边界。范围（lever2-only vs +lever1）与验证深度由 brainstorming 定后细化。

- [ ] 1. 设计定稿：brainstorming 拍板范围（lever2-only / +lever1）+ 验证方法栈 + 灰度策略；产出 Design Doc + delta spec。
- [ ] 2. config 默认开：`config_loader.DEFAULTS` 加 flag=True + HARD_LIMITS + env 覆盖（逃生阀）；`judge.py` 兜底对齐。
- [ ] 3. 风控链确认：测试坐实 lever1 授 <1.5 地板 long 经 `low_rr_policies` 缩仓/降杠杆/独立 slot 正确生效；lever2 ladder 口径对非趋势单无意外放开。
- [ ] 4. event_backtest 同构（红线合规）：跑 event_backtest 确认非崩溃/回归；记录其对 Judge 级口径改动的已知失真，主验证证据指向 rejected 流 A/B + tier 定价 + paper 前向。
- [ ] 5. 全量回归 pytest 绿 + 更新相关单测（默认值变更涉及的断言）。
- [ ] 6. 验证报告：汇总主证据栈 + event_backtest 结果 + 灰度/回滚（env 逃生阀）说明。
```

## openspec/changes/trend-entry-levers-default-on/specs/ladder-weighted-rr/spec.md

- Source: openspec/changes/trend-entry-levers-default-on/specs/ladder-weighted-rr/spec.md
- Lines: 1-25
- SHA256: 4e90bbf24d3cda62c6248a063ba1692435ea6bb8cb8dd899c3aa367197f8b959

```md
## ADDED Requirements

### Requirement: ladder_rr_enabled 默认启用（lever2 背书已满足）

阶梯加权 effective_rr（`ladder_rr_enabled`）的默认值 SHALL 为启用（True）。其全样本 A/B 背书已满足：rejected 流忠实 A/B 在保守 TP1 结算（零 TP2 信用）下含亏单净 **+0.21R/簇**；tier 到达频率定价表明被 `rr_below_floor` 拒的干净趋势 long **P(达TP2)=68% / P(TP2|达TP1)=90%**，且把 TP2/TP3 按到达频率打折后 effective_rr 仍 **1.76~1.80**（对"TP2 必达"假设不敏感，因 TP1 50% 权重 + 第3档封顶 +1R 已扛主导）。默认值 SHALL 经 `config_loader.DEFAULTS` 提供，并保留 env 覆盖（`LADDER_RR_ENABLED`）作为**即时关闭逃生阀**，无需改代码即可回滚。

#### Scenario: 默认启用

- **WHEN** 未显式配置 `ladder_rr_enabled`（既不在 env 也不在 config 文件）
- **THEN** `effective_rr` 使用阶梯加权口径（lever2 生效），被 TP1-only 口径误拒的趋势单按真实 50/25/25 离场评分

#### Scenario: env 逃生阀即时关闭

- **WHEN** 环境变量 `LADDER_RR_ENABLED=false`
- **THEN** `effective_rr` 回退到改动前 TP1-only 口径，无需改代码（满足既有「config 灰度开关」回退场景）

#### Scenario: lever1 不随本 change 默认开

- **WHEN** 本 change 默认开 lever2
- **THEN** lever1（`path_evidence_aligned_enabled`）SHALL 保持默认关——其目标人群（中性 bias + 干净趋势）验证待 `tech_context` 埋点数据累积，另起独立 change；本 change 不动 lever1 默认值

#### Scenario: lever2 抬高 R:R 过正常地板而非走低 R:R 策略

- **WHEN** lever2 把某趋势单 effective_rr 从 <地板 抬到 ≥1.50 default 地板
- **THEN** 该单作为正常 R:R 单开仓（全尺寸、不触发 `low_rr_policies` 缩仓/降杠杆/独立 slot——那是 lever1 授 <1.5 地板时的路径）
```

