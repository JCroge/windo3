## MODIFIED Requirements

### Requirement: 前向影子决策记录

交易层每个进入决策磁带 chokepoint 的信号，SHALL 在 live 真实决策之外**旁路跑两条复盘臂**：**baseline 臂 `replay(lever2-only)`（`path_evidence_aligned_enabled=False` AND `ladder_rr_enabled=True`，= live 当前生效配置）** 与 **shadow 臂 `replay(both-levers)`（`path_evidence_aligned_enabled=True` AND `ladder_rr_enabled=True`）**，并 write-only 记录 `{timestamp, symbol, real_action+gate, baseline_action+gate, shadow_action+gate, baseline_mismatch, flip_kind, tech_context, plan}` 到独立产物（如 `data/shadow_decision_log.jsonl`）。两条复盘臂 MUST 复用 `utils/decision_replay.py::replay_decision` 的隔离机器（同 bundle、各自 flags overlay），MUST NOT 重写 live `_make_decision` 决策逻辑。

#### Scenario: 影子记录 baseline vs shadow

- **WHEN** 一个信号在决策磁带 chokepoint 产出 live 决策
- **THEN** 用同 bundle 分别跑 `BASELINE_CONFIG={path_evidence_aligned_enabled:False, ladder_rr_enabled:True}` 与 `SHADOW_CONFIG={path_evidence_aligned_enabled:True, ladder_rr_enabled:True}`，记录 real（live）/ baseline / shadow 三个决策（含各自 action / gate / reject_reason）+ tech_context + plan

### Requirement: 对比隔离 lever1 增量

lever1 增量 SHALL 由 **`replay(both-levers)` 与 `replay(lever2-only)` 两条复盘臂之差**界定，而非 `影子 − 实盘(live)` 之差。两臂同走 `replay_decision` 复盘，使复盘机器的系统性保真偏差在 delta 中抵消（对齐 `sequential_perturbation` 两臂同估算原则）。`flip_kind` SHALL 基于 `baseline_action` vs `shadow_action` 计算（`same` / `shadow_opens` / `shadow_holds`）。

#### Scenario: lever1 增量为两臂复盘之差

- **WHEN** baseline 臂 = lever2-only（l1 off）、shadow 臂 = both-levers（l1 on），同一 bundle 复盘
- **THEN** `flip_kind=shadow_opens` 当且仅当 shadow 臂开仓而 baseline 臂未开（lever1 解锁的新单）；两臂结果相同则 `flip_kind=same`，复盘机器偏差对两臂同向不进入增量

## ADDED Requirements

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
