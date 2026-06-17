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
