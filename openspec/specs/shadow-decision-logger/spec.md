## ADDED Requirements

### Requirement: 前向影子决策记录

交易层每个进入决策磁带 chokepoint 的信号，SHALL 在 live 真实决策之外**旁路跑两条复盘臂**：**baseline 臂 `replay(lever2-only)`（`path_evidence_aligned_enabled=False` AND `ladder_rr_enabled=True`，= live 当前生效配置）** 与 **shadow 臂 `replay(both-levers)`（`path_evidence_aligned_enabled=True` AND `ladder_rr_enabled=True`）**，并 write-only 记录 `{timestamp, symbol, real_action+gate, baseline_action+gate, shadow_action+gate, baseline_mismatch, flip_kind, tech_context, plan}` 到独立产物（如 `data/shadow_decision_log.jsonl`）。两条复盘臂 MUST 复用 `utils/decision_replay.py::replay_decision` 的隔离机器（同 bundle、各自 flags overlay），MUST NOT 重写 live `_make_decision` 决策逻辑。

#### Scenario: 影子记录 baseline vs shadow

- **WHEN** 一个信号在决策磁带 chokepoint 产出 live 决策
- **THEN** 用同 bundle 分别跑 `BASELINE_CONFIG={path_evidence_aligned_enabled:False, ladder_rr_enabled:True}` 与 `SHADOW_CONFIG={path_evidence_aligned_enabled:True, ladder_rr_enabled:True}`，记录 real（live）/ baseline / shadow 三个决策（含各自 action / gate / reject_reason）+ tech_context + plan

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

### Requirement: baseline 复现自检闸

影子记录器 SHALL 对每条记录做 **baseline 复现自检**：`replay(lever2-only)` 复盘出的 accept/reject 类别 MUST 与 live record 的 accept/reject 类别一致，否则该条标 `baseline_mismatch=True`。被标 `baseline_mismatch` 的记录 SHALL 排除出 lever1 增量统计（对齐 `perturbation_replay` 的 baseline 复现自检：复盘复现不出 live 的记录不可作为翻转结论的依据）。自检 MUST 只对比 accept/reject 二元类别（开仓 vs 非开仓），不要求 plan 连续字段字节级一致。

#### Scenario: baseline 复盘复现 live → 记录可信

- **WHEN** `replay(lever2-only)` 复盘结果与 live record 同为 accept（或同为 reject）
- **THEN** `baseline_mismatch=False`，该条进入 lever1 增量统计

#### Scenario: baseline 复盘背离 live → 标记排除

- **WHEN** `replay(lever2-only)` 复盘结果与 live record 的 accept/reject 类别不一致（复盘失真）
- **THEN** `baseline_mismatch=True`，该条排除出 lever1 增量统计，离线对比驱动 MUST 过滤掉这些记录

#### Scenario: 离线驱动按自检过滤

- **WHEN** `cf_shadow_lever1_compare.py` 筛 `flip_kind=shadow_opens` 结算 lever1 增量
- **THEN** MUST 先剔除 `baseline_mismatch=True` 的记录，只对 baseline 复现可信的记录统计前向净 R

### Requirement: 对比隔离 lever1 增量

lever1 增量 SHALL 由 **`replay(both-levers)` 与 `replay(lever2-only)` 两条复盘臂之差**界定，而非 `影子 − 实盘(live)` 之差。两臂同走 `replay_decision` 复盘，使复盘机器的系统性保真偏差在 delta 中抵消（对齐 `sequential_perturbation` 两臂同估算原则）。`flip_kind` SHALL 基于 `baseline_action` vs `shadow_action` 计算（`same` / `shadow_opens` / `shadow_holds`）。

#### Scenario: lever1 增量为两臂复盘之差

- **WHEN** baseline 臂 = lever2-only（l1 off）、shadow 臂 = both-levers（l1 on），同一 bundle 复盘
- **THEN** `flip_kind=shadow_opens` 当且仅当 shadow 臂开仓而 baseline 臂未开（lever1 解锁的新单）；两臂结果相同则 `flip_kind=same`，复盘机器偏差对两臂同向不进入增量
