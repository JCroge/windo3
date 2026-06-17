---
comet_change: trend-entry-shadow-decision-logger
role: technical-design
canonical_spec: openspec
---

# Design Doc: trend-entry-shadow-decision-logger（前向影子决策记录器）

> OpenSpec 为需求真相源。本文档承载技术设计。observability-only，不碰 live 交易。

## 背景

lever2 已默认开上 live（`trend-entry-levers-default-on`），但 lever1（`path_evidence_aligned_enabled`）仍默认关——验证最弱：其 path-evidence 输入历史数据墙（rejected 流 0/25030 含，`trend.strength`/`sym_dir` 不可从 klines 干净重构）。用户方案：live 走 lever2-only，**影子前向记录 both-levers 决策**对比，零 live 风险地为 lever1 攒证据。因 Judge 对 live/paper 同一决策，必须建**并行影子决策路径**而非翻 flag。

## 决策 D1：影子跑法 = 复用 `replay_decision` 前向

在 live 决策磁带 chokepoint（`judge.py:2004` accept / `3093` reject，已 `build_bundle(tech, llm_inline, state_snapshot)`），拿同一 bundle 调 `replay_decision(bundle, {path_evidence_aligned_enabled:True, ladder_rr_enabled:True})` 得影子决策。

- **依据**：replay_decision 已隔离跑真实 `_make_decision`（mock 3 外部 await、用缓存 llm、捕获 publish **绝不进真实 bus**、`MultiJudge.__new__` **不碰 live 实例**）。影子 = 同 bundle 再 replay 一次 flags-on，**纯计算、零额外 LLM/网络、零新隔离代码**。
- **否决 B（抽共享纯函数双调）**：碰 live `_make_decision` 决策逻辑，风险高、违"不改 live"。
- **否决 C（克隆 Judge 直跑）**：重造 replay 已有隔离轮子。

## 决策 D2：对比语义 = 影子(both) − 实盘(lever2-only) = lever1 增量

live 现 lever2-only（l2 on/l1 off），影子 both-levers（l2 on/l1 on）→ 差异即 **lever1 纯增量**（要验证的杠杆）。记录 `{ts, symbol, real_action+各 gate, shadow_action+各 gate, flip_kind, tech_context, plan}`。flip_kind 前向看 lever1 解锁的单是否被下游 gate 接住（over-determination 前向版）。

## 决策 D3：隔离红线（observability-only write-only）

与 CF 产物 / provenance / agent-health 同性质：
- 影子决策**绝不** publish 真实 bus / 下单 / mutate live Judge·portfolio·cooldown·daily-stop。
- 影子产物（`data/shadow_decision_log.jsonl`）**严禁**任何 gate/rank/veto/halt/daily-stop 读取——`tests/test_cf_red_line_guard.py` 扩展禁读断言。
- live 写影子日志允许（同 Judge 写决策磁带）；禁的是决策/风控路径**读**。

## 决策 D4：hook 落点内联 + 失败安全

chokepoint 内联旁路（record_decision 后），**try/getattr 防御**——影子任何异常绝不影响 live 决策产出/发布，跳过本次影子记录记 warning。整体 config flag `shadow_decision_logger_enabled`（默认开/关待 brainstorming 收尾时定——倾向默认开，因 observability 无 live 风险且要尽快攒 lever1 数据）。

## 决策 D5：结局锚离线结算

hook **只写日志**（real+shadow+tech+plan），不内联结算（保持 live 旁路极轻）。前向结局由离线对比驱动用 `resolve_counterfactual`+klines 结算（同 rejected 流口径），复用 `cf_honesty_gate` 诚实门。

## 数据流（live 链路零结构改动）

`tech_analysis → Judge._make_decision`（live 决策照常 publish）`→ [chokepoint] 旁路: 同 bundle replay flags-on → shadow_decision_log`（write-only）。影子是纯旁路。

## 性能

每信号多跑一次 replay_decision（`__new__`+`restore_state`+`_make_decision`，纯内存）。per-signal 节奏（每标的每决策周期）下可忽略；config flag 可关；fail-safe。

## 测试策略

- 隔离红线守卫扩展（禁读影子产物 + 影子不 publish/不下单/不 mutate）。
- 失败安全测试：影子 replay 抛异常 → live 决策不受影响。
- 影子记录 schema sanity：产物含 real+shadow+tech_context（非空=填了 lever1 数据墙）。
- config flag off → 无影子、live 等价。
- 全量回归 pytest 绿。

## 不做（YAGNI）

- 不改 live 决策逻辑 / 不翻 lever1 默认（影子只"看"）。
- 不下影子单 / 不进真实 bus / 不动 live portfolio。
- 不内联结算前向结局（离线驱动）。
- 不做三臂（neither/l2/l1+l2）全归因——real(l2-only) 已是 live 基线，影子(both)−real=lever1 足够；lever2 标准效果已有 +0.181R/簇 A/B。
