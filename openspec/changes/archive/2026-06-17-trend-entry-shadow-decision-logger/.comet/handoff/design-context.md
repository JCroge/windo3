# Comet Design Handoff

- Change: trend-entry-shadow-decision-logger
- Phase: design
- Mode: compact
- Context hash: c27d18b3d8e495c4121f01fcc269830f157a20b0cfe2efd45ab20b87bf0544b4

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/trend-entry-shadow-decision-logger/proposal.md

- Source: openspec/changes/trend-entry-shadow-decision-logger/proposal.md
- Lines: 1-37
- SHA256: 357aa114e10bf7ee393528461afdb58e0a369a5a62b4fd339fa852dc9dbae4cf

```md
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
```

## openspec/changes/trend-entry-shadow-decision-logger/design.md

- Source: openspec/changes/trend-entry-shadow-decision-logger/design.md
- Lines: 1-36
- SHA256: eb60a5c01576beacf4f126f10c36533b484caa4e53f5b8beafbb70b3a60ef857

```md
# Design (high-level): trend-entry-shadow-decision-logger

> 高层方向。深度技术设计 + 5 个待定决策由 comet-design（brainstorming）产出 Design Doc 后定稿。

## 核心思路（待 brainstorming 确认）

复用现成隔离机器，最小新增面：

- **hook 点**：live 决策磁带 chokepoint（`judge.py:2004` accept / `3093` reject），此处已 `build_bundle(tech, llm_inline, state_snapshot)`。
- **影子 = 同 bundle 再 replay 一次 flags-on**：`utils/decision_replay.py::replay_decision(bundle, {path_evidence_aligned_enabled: True, ladder_rr_enabled: True})` → 得影子决策。replay 天生隔离（mock 外部 await、用缓存 llm、捕获 publish 不进真实 bus、用 `MultiJudge.__new__` 不动 live 实例）。
- **记录**：新 observability 产物（如 `data/shadow_decision_log.jsonl`）写 `{ts, symbol, real_action+gate, shadow_action+gate, flip_kind, tech_context, plan, 结局锚}`。
- **结局锚**：影子开仓的前向结局复用 `resolve_counterfactual` + klines（与 rejected 流同口径）或 shadow-forward 结算。

## 隔离红线（observability-only write-only）

与 CF 产物 / provenance / agent-health 同性质：
- 影子决策**绝不** publish 真实 bus、**绝不**下单、**绝不** mutate live Judge / portfolio / cooldown / daily-stop 状态。
- 影子产物**严禁**任何 gate/rank/veto/halt/daily-stop 读取（红线守卫 `tests/test_cf_red_line_guard.py` 扩展禁读断言）。
- live 写影子日志允许（与 Judge 写决策磁带同性质）；禁的是决策/风控路径**读**。

## 待 brainstorming 定的设计决策

1. 影子跑法：复用 `replay_decision` 前向 vs 抽共享纯决策函数双调（性能/耦合权衡）。
2. hook 落点：决策磁带 chokepoint 内联 vs 独立 hook（避免拖慢 live 决策）。
3. 结局锚结算口径与节流。
4. 性能与失败安全（影子异常绝不破 live 决策——`getattr`/try 防御）。
5. 对比报表（驱动脚本形态，复用 cf_honesty_gate 诚实门）。

## 数据流（live 不变）

`tech_analysis → Judge._make_decision`（live 决策，照常 publish）`→ [chokepoint] 旁路:同 bundle replay flags-on → shadow_decision_log`（write-only）。live 链路零结构改动，影子是纯旁路。

## 风险与回滚

- 影子路径异常**必须** fail-safe 不影响 live 决策（防御性 getattr/try，缺则跳过本次影子记录）。
- 可经 config flag（如 `shadow_decision_logger_enabled`）整体关闭。
```

## openspec/changes/trend-entry-shadow-decision-logger/tasks.md

- Source: openspec/changes/trend-entry-shadow-decision-logger/tasks.md
- Lines: 1-10
- SHA256: f807f6c10fd8f93fa0417e8538743efbe22220c01c48c36f3f6cdc0549e337c4

```md
# Tasks: trend-entry-shadow-decision-logger

> 初始任务边界。影子跑法/隔离/结局锚/报表由 brainstorming 定后细化。

- [ ] 1. 设计定稿：brainstorming 拍板影子跑法（复用 replay_decision 前向 vs 共享纯函数）+ hook 落点 + 隔离红线 + 结局锚口径 + 性能/失败安全；产出 Design Doc + delta spec `shadow-decision-logger`。
- [ ] 2. 影子决策记录器：在 live 决策 chokepoint 旁路跑 both-levers on 影子决策（复用隔离机器），write-only 写 `shadow_decision_log.jsonl`（real vs shadow + tech_context + 结局锚）；影子异常 fail-safe 不破 live。
- [ ] 3. 隔离红线守卫：扩展 `tests/test_cf_red_line_guard.py` 禁交易决策/风控路径读影子产物；坐实影子绝不 publish 真实 bus / 不下单 / 不 mutate live 状态。
- [ ] 4. 结局锚结算 + 对比报表：影子开仓前向结局（resolve_counterfactual/klines）+ 一次性对比驱动（real lever2-only vs shadow both-levers：多开数/前向 R/lever1 增量），复用诚实门。
- [ ] 5. 全量回归 pytest 绿 + 失败安全测试（影子异常不影响 live 决策）。
- [ ] 6. 验证报告：隔离红线坐实 + 影子记录 sanity（产物 schema、tech_context 非空填了 lever1 数据墙）+ 性能影响。
```

## openspec/changes/trend-entry-shadow-decision-logger/specs/shadow-decision-logger/spec.md

- Source: openspec/changes/trend-entry-shadow-decision-logger/specs/shadow-decision-logger/spec.md
- Lines: 1-52
- SHA256: 35d39858987c7911302dcd8d77f9497ccf023845e5007f5753d59cfb21836085

```md
## ADDED Requirements

### Requirement: 前向影子决策记录

交易层每个进入决策磁带 chokepoint 的信号，SHALL 在 live 真实决策之外**旁路跑一遍 both-levers（`path_evidence_aligned_enabled=True` AND `ladder_rr_enabled=True`）on 的影子决策**，并 write-only 记录 `{timestamp, symbol, real_action+各 gate, shadow_action+各 gate, flip_kind, tech_context, plan}` 到独立产物（如 `data/shadow_decision_log.jsonl`）。影子决策 MUST 复用 `utils/decision_replay.py::replay_decision` 的隔离机器（同 bundle、flags-on overlay），MUST NOT 重写 live `_make_decision` 决策逻辑。

#### Scenario: 影子记录 real vs shadow

- **WHEN** 一个信号在决策磁带 chokepoint 产出 live 决策（lever2-only）
- **THEN** 旁路用同 bundle + `{path_evidence_aligned_enabled:True, ladder_rr_enabled:True}` 跑影子决策，记录 real 与 shadow 两个决策（含各自 action / gate / reject_reason）+ tech_context + plan

#### Scenario: 对比隔离 lever1 增量

- **WHEN** live 为 lever2-only（l2 on / l1 off）、影子为 both-levers（l2 on / l1 on）
- **THEN** 影子 − 实盘的差异即 lever1 的纯增量（flip_kind 标 lever1 解锁的单是否被下游 gate 接住）

### Requirement: observability-only write-only 隔离

影子决策与影子产物 SHALL 是 observability-only write-only：影子决策 MUST NOT publish 真实 message bus、MUST NOT 下任何单、MUST NOT mutate live Judge / portfolio / cooldown / daily-stop 状态。任何 gate/rank/veto/halt/daily-stop 交易决策或风控路径 MUST NOT 读取影子产物（红线守卫 `tests/test_cf_red_line_guard.py` 扩展禁读断言）。live 写影子日志允许（同 Judge 写决策磁带性质）。

#### Scenario: 影子不影响 live

- **WHEN** 影子决策跑出 open（lever1 解锁的趋势单）
- **THEN** 不产生任何真实 bus 消息 / 不下单 / 不改 live 任何状态；仅写影子日志

#### Scenario: 红线守卫禁读

- **WHEN** 交易决策 / executor / halt / riskguard 路径
- **THEN** 不得 import/读取影子产物；守卫测试断言禁读

### Requirement: 失败安全（影子绝不破 live）

影子旁路 SHALL fail-safe：影子决策路径任何异常 MUST NOT 影响 live 真实决策的产出与发布（防御性 `getattr`/`try`，异常时跳过本次影子记录并继续 live）。影子记录器 SHALL 可经 config flag（`shadow_decision_logger_enabled`）整体开关。

#### Scenario: 影子异常不破 live

- **WHEN** 影子 `replay_decision` 抛异常
- **THEN** live 真实决策照常产出并发布，仅本次影子记录被跳过（记 warning）

#### Scenario: config 开关

- **WHEN** `shadow_decision_logger_enabled=False`
- **THEN** 不跑影子、不写影子日志，live 行为与无此特性等价

### Requirement: 结局锚离线结算 + 对比报表

影子开仓的前向结局 SHALL 由**离线对比驱动**结算（复用 `resolve_counterfactual` + klines，与 rejected 流同口径），而非内联结算（保持 live 旁路极轻：hook 仅写日志）。对比驱动 SHALL 输出 real(lever2-only) vs shadow(both-levers) 的多开数 / 前向净 R / lever1 增量，复用诚实门（`utils/cf_honesty_gate.py`）薄样本拒答。

#### Scenario: 离线结算前向结局

- **WHEN** 影子日志累积后跑对比驱动
- **THEN** 用 klines + `resolve_counterfactual` 结算影子开仓前向结局，报 lever1 增量净期望（含亏单），薄样本经诚实门拒答
```

