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
