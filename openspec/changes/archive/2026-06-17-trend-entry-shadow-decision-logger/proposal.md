# Proposal: trend-entry-shadow-decision-logger

## Why

`trend-entry-levers-default-on` 把 lever2 默认开上了 live，但 **lever1（`path_evidence_aligned_enabled`）仍默认关——验证最弱**：其 path-evidence 输入（`pre_12h_return_pct` / `position_in_24h_range` / `trend.strength` / `sym_dir`）在历史数据里**一条都没有**（rejected 流 0/25030 含；`trend.strength`/`sym_dir` 也无法从 klines 干净重构）。所以 lever1 既无法离线 A/B，也不能贸然上 live。

用户方案：**live 走 lever2-only，影子账户走 lever1+lever2，零 live 风险地前向记录对比数据**。但架构上 Judge 对 live 和 paper 是**同一个决策**——翻 flag 会连 live 一起影响。故需建一条**并行的影子决策路径**：对每个信号，在产出实盘真实决策之外，**额外跑一遍 both-levers（lever1+lever2）on 的影子决策**，只记录、不下单、不进真实 bus。

## What

新增 **前向影子决策记录器**（observability-only）：交易层每个信号在 live 决策产出处（决策磁带 chokepoint，已捕获 tech+llm+state），**复用 `replay_decision` 的隔离机器**对**同一 bundle** 用 lever1+lever2 on 的 config 跑一遍影子决策，记录 `{real_decision, shadow_decision, tech_context, 结局锚}` 到独立产物，前向累积。

**关键复用**：决策磁带在 `judge.py:2004/3093` 两 chokepoint 已 `build_bundle(tech, llm, state_snapshot)`；`replay_decision` 已能隔离地跑真实 `_make_decision`（mock 3 个外部 await、用缓存 llm、捕获 publish **绝不进真实 bus**）。故影子 = 同 bundle 再 replay 一次 flags-on，**纯计算、无额外 LLM/网络、零新隔离机器**。

## 价值

1. **填 lever1 数据墙**：影子在决策时点天然带 tech_context → path-evidence 输入从此有数据，为 lever1 日后 A/B / 上 live 提供前向证据。
2. **零 live 风险的 l1+l2 对比**：影子决策绝不下单、绝不 publish 真实 bus；对比"实盘 lever2-only 实际开了什么"vs"both-levers 会多开什么 + 前向结局"。
3. **顺带让翻转后旧磁带断层退役**：前向新记录自带 ladder=True 自洽（见 `trend-entry-levers-default-on` Implementation Divergence）。

## 待设计阶段（brainstorming）拍板

1. **影子怎么跑**：复用 `replay_decision` 前向（同 bundle flags-on）vs 抽共享纯决策函数双调。
2. **隔离红线**：绝不 publish 真实 bus / 绝不下单 / 绝不 mutate live Judge·portfolio 状态（与 CF 产物、provenance、agent-health 同性质 observability-only write-only）。
3. **记录什么 + 结局锚**：影子开仓的前向结局怎么结算（klines / shadow resolve / 复用 rejected 流影子前向）。
4. **性能**：每信号多跑一次 `_make_decision`（纯计算）的成本与节流。
5. **对比报表形态**。

## Capabilities

新增 capability `shadow-decision-logger`（observability-only 前向影子决策记录 + 对比）。delta spec 在 design 阶段定。

## Non-goals

- 不改任何 live 决策逻辑 / 不翻 lever1 默认（lever1 仍关，影子只是"看"）。
- 不下任何影子单 / 不进真实 bus / 不动 live portfolio。
- 不做 live paper 双轨改造（影子是 Judge 决策层的并行记录，非 executor 层）。
