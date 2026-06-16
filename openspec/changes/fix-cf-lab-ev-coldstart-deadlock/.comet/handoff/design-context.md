# Comet Design Handoff

- Change: fix-cf-lab-ev-coldstart-deadlock
- Phase: design
- Mode: compact
- Context hash: 2dd9dcb92307a6d75e22a0f302bc29de04fdb7bc5902b8ee296b5ba7d197645a

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/fix-cf-lab-ev-coldstart-deadlock/proposal.md

- Source: openspec/changes/fix-cf-lab-ev-coldstart-deadlock/proposal.md
- Lines: 1-31
- SHA256: d3e2119f4769fac3ac5c228e9ffa14af8fe3bb4452829ce5f382cf5224aab223

```md
## Why

反事实策略实验室 L1-L4 全建成、磁带也累积充足（v2 修复后 573 条、klines_1s 真 1s 粒度且完全覆盖窗口），但 `cf_direction_recommendation.py` 兑现时**全程 `cf_open=0 / div=0 / delta=0 / no_actionable_direction`**，实验室对任何单旋钮都给不出方向。多轮证伪后定位到真根因：**L3b 序列组合模拟存在 EV-gate 冷启动死锁**——`utils/sequential_perturbation.py::run_arm` 经 `_inject_cf_state` 把 CF 组合自身的**冷 EV 状态**（`p_win=40% bayesian_prior`）灌进记录再 `replay_decision`，使真实 Judge 的 EV gate 算出负 EV 直接拒开仓。死锁链：开仓需正 EV → EV 靠累计 CF 胜率 → 没单开成 → 胜率不累计 → EV 永远冷 → 永不开仓。

关键矛盾：这些单 live 当时撞的是 `rr_below_floor`（说明 live 的 EV gate **是过的**），但 CF-sim 注入的冷 EV 比现实更悲观，把本该过的 EV gate 拦死。于是无论怎么扫 R:R 地板，上游 EV gate 先把所有候选灭掉。

> **已证伪、勿写进 scope**：tape tech 捕获正确、L2 回放忠实（同一记录直接 `replay_decision({rr_floor_default:0.3})` → `open_long`；经 `_inject_cf_state` 后 → `hold (EV=-0.41<0.05 p_win=40%)`）。早先怀疑的"抓错 tech 快照 / hold 当 reject 的虚假保真"机制均不成立。

## What Changes

- **修复 EV 冷启动死锁（主）**：让 CF-sim 在 EV gate 处的状态贴近 live 决策时的真实 EV，使被扰动旋钮（如 R:R 地板）真正能影响开仓结果。具体修法（贴 live EV / 用录制 EV / 调整 `_seed_cf_prior` 暖启动 / CF-sim EV gate 改读录制 EV）留 design 阶段 brainstorm 定夺。
- **保真改 gate-level 比对**：`baseline_fidelity` 当前把 `live=reject` 与 `CF-sim=hold(换 EV gate 拦)` 都归"非-accept"类即算复现，对"换了个 gate 拦"是盲的 → 改为 `reject_reason` / 实际触达 gate 一致才算复现，使 EV-gate 误拦能被保真指标暴露而非掩盖。
- **驱动按 v2 过滤**：`cf_direction_recommendation.py::load_records` 全量喂入含 932 条 stale `replayable=true` 的 v1 旧空记录 → 改为按 `schema v2 AND tech 非空` 过滤，不盲信写入时已固化的 stale `replayable` 标志。
- 全程 **observability-only / write-only**，红线守卫（`tests/test_cf_red_line_guard.py`）维持：禁止任何 gate/rank/veto/halt/daily-stop 读取 CF 产物。

## Capabilities

### New Capabilities
<!-- 无新增能力；本 change 修正既有 L3b/L4 能力的需求级行为 -->

### Modified Capabilities
- `counterfactual-portfolio-sim`: CF 组合在 EV gate 判定处使用的 EV/胜率状态须避免冷启动死锁——不得因初始先验比 live 决策时更悲观而系统性拦死本该通过 EV gate 的候选。
- `sequential-perturbation-driver`: `_inject_cf_state` 注入的 CF 状态须与 live 决策时的真实 EV 一致性可控，不得引入比现实更悲观的 EV 而使被扰动旋钮失效；`run_arm` 在两臂均开仓为 0 时须可与"旋钮无效"区分。
- `perturbation-delta-report`: `baseline_fidelity` 须按 gate-level（`reject_reason` / 触达 gate）比对而非 accept-vs-非accept 类，确保"换 gate 拦"被计为不复现并反映到 untrustworthy / fidelity_note。
- `replay-report-driver`: 报告驱动须按 `schema v2 AND tech 非空`过滤可回放记录，不盲信 stale `replayable` 标志。

## Impact

- 代码：`utils/sequential_perturbation.py`、`utils/cf_portfolio.py`、`utils/knob_sweep.py`、`cf_direction_recommendation.py`，及对应测试（`tests/` 下 cf/perturbation/portfolio/sweep 相关 + 红线守卫）。
- 行为：仅影响反事实实验室的回放/扫描产物质量；**不触及交易决策路径、不改 live Judge 逻辑、不改 choppy R:R 地板 1.50、不新增 LLM 旋钮扰动**。
- 风险红线：CLAUDE.md 反事实回放产物 observability-only write-only 约束不变；无需 `event_backtest` 同构（不改策略公式）。
```

## openspec/changes/fix-cf-lab-ev-coldstart-deadlock/design.md

- Source: openspec/changes/fix-cf-lab-ev-coldstart-deadlock/design.md
- Lines: 1-39
- SHA256: 4ed7db38b18e8d19c84e174e046534d143647febe5b93dfe26d2587c8a715b4e

```md
# Design (high-level) — fix-cf-lab-ev-coldstart-deadlock

> 本文件是 OpenSpec 高层架构草图。详细技术 RFC + 方案权衡定夺在 comet-design 阶段的 Superpowers Design Doc 完成。

## 问题边界（已坐实）

```
record (v2, tech 非空, 有 state_snapshot)
   │  直接 replay_decision({rr_floor_default:0.3})  → open_long      ✅ 旋钮生效
   │
   └─ run_arm: _inject_cf_state(record, cf) 灌入 CF 组合冷 EV 状态
          │
          ▼  replay_decision(injected, config)
        EV gate: EV = -0.41 < 0.05  (p_win=40% bayesian_prior)  → hold  ❌ 永远拦死
```

死锁：开仓需正 EV → EV 靠累计 CF 胜率 → 没单开成 → 胜率不累计 → EV 永远冷。
矛盾锚点：live 当时这些单撞 `rr_below_floor`（live EV gate 已过），CF-sim 注入的 EV 比现实更悲观。

## 三个待修点

1. **EV 冷启动（主）** — `cf_portfolio` / `sequential_perturbation._inject_cf_state` / `_seed_cf_prior`。
2. **gate-level 保真** — `perturbation_delta_report.build_delta_report` 的 `baseline_fidelity` 比对粒度。
3. **驱动 v2 过滤** — `cf_direction_recommendation.load_records`（次要、独立、低风险）。

## 候选方案（comet-design 定夺，勿在此拍板）

EV 冷启动修法（互斥/可组合，需 brainstorm）：
- **A. CF-sim EV gate 改读录制 EV**：回放时 EV gate 直接用 tape 录制的 live EV/p_win（live 已算过），不冷重算。最忠实，但需确认 tape 是否录了 EV 输入。
- **B. 暖启动 `_seed_cf_prior`**：用磁带窗口前真实战绩给 CF EV 一个代表性先验，避免 40% 冷拒。简单，但先验来源/代表性需论证。
- **C. CF EV 状态贴 live**：每步注入时把 CF 的 archetype EV 状态对齐 live 决策时快照里的 EV 相关字段。

每个方案都要回答：会不会人为抬高 baseline_fidelity / 掩盖级联（L3b 最终审查修过的核心陷阱）。

## 不变量 / 红线

- observability-only write-only：禁止任何交易决策路径读取 CF 产物（`tests/test_cf_red_line_guard.py` 守卫不放松）。
- 不改 live Judge 决策逻辑、不改 choppy R:R 地板 1.50、不新增 LLM 旋钮扰动、无需 event_backtest。
- 两臂同估算 → 系统性偏差在 delta 抵消的设计原则保持。
```

## openspec/changes/fix-cf-lab-ev-coldstart-deadlock/tasks.md

- Source: openspec/changes/fix-cf-lab-ev-coldstart-deadlock/tasks.md
- Lines: 1-22
- SHA256: 9c634b60fe738d8a6e0e9ef4d45ddab450f16f65b2d17cb4368c5ed04900bff5

```md
# Tasks — fix-cf-lab-ev-coldstart-deadlock

> 骨架任务，comet-design 阶段定方案后细化/拆分。

## 设计（comet-design 阶段细化）
- [ ] brainstorm 选定 EV 冷启动修法（方案 A 读录制 EV / B 暖启动先验 / C 贴 live，含掩盖级联风险评估）
- [ ] 确认 tape 是否录有 EV gate 所需输入（决定方案 A 可行性）
- [ ] 产出 Superpowers Design Doc + delta spec（counterfactual-portfolio-sim / sequential-perturbation-driver / perturbation-delta-report / replay-report-driver）

## 实现
- [ ] 修 EV 冷启动死锁（按选定方案改 cf_portfolio / sequential_perturbation）
- [ ] baseline_fidelity 改 gate-level（reject_reason / 触达 gate 一致才算复现）
- [ ] cf_direction_recommendation.load_records 按 schema v2 AND tech 非空过滤

## 测试
- [ ] 端到端：rr_below_floor 记录在 build_delta_report 下放宽地板须产生 perturbed_cf_open>0（坐实死锁已解）
- [ ] gate-level 保真：CF-sim 换 gate 拦（EV vs rr）须计为不复现 / 反映到 untrustworthy
- [ ] 红线守卫 `tests/test_cf_red_line_guard.py` 维持通过（observability-only 不放松）
- [ ] 全量 pytest 回归（基线 1238，不回退）

## 验收
- [ ] 重跑 cf_direction_recommendation.py：能产出非零 delta 或可信的 no_actionable_direction（区别于死锁空转）
```

## openspec/changes/fix-cf-lab-ev-coldstart-deadlock/specs/counterfactual-portfolio-sim/spec.md

- Source: openspec/changes/fix-cf-lab-ev-coldstart-deadlock/specs/counterfactual-portfolio-sim/spec.md
- Lines: 1-16
- SHA256: 9dd7560af83e72447ccf05a6ccdc90d8d5fa0440814906a606ae5992de8b22ca

```md
## ADDED Requirements

### Requirement: CF rolling 胜率(EV-gate 保真,镜像 Reviewer 窗口)
CF 组合 SHALL 维护一个与 live Reviewer 滚动窗口同语义的 rolling 胜率,供注入真实 Judge 的 EV gate 使用,避免冷启动死锁。窗口 SHALL 只吸收 CF 自身已结算的结果,SHALL NOT per-record 注入 reality 当时的演化计数。

#### Scenario: 窗口语义对齐 live
- **WHEN** CF 组合提供 `_recent_win_rate`
- **THEN** 其 SHALL 为最近 `rolling_window_size`(默认 20)笔 CF 已结算结果的 win 数 / 窗口长(与 Reviewer `_calculate_rolling_metrics` 一致),而非累计 `_recent_wins/_total_completed_trades`

#### Scenario: 从 CF 自身结果演化
- **WHEN** 某 CF 仓结算
- **THEN** 系统 SHALL 把该笔 win/loss 推入 rolling 窗口(FIFO 顶最老),使窗口随 CF 自身级联演化;SHALL NOT 用现实演化计数覆盖

#### Scenario: to_snapshot emit rolling 率
- **WHEN** CF 状态机 `to_snapshot`
- **THEN** `_recent_win_rate` SHALL 取 rolling 窗口率;`_recent_wins`/`_total_completed_trades` SHALL 保留累计语义(供 bayesian fallback);其它读者(cooldown 等)行为 SHALL 不变
```

## openspec/changes/fix-cf-lab-ev-coldstart-deadlock/specs/perturbation-delta-report/spec.md

- Source: openspec/changes/fix-cf-lab-ev-coldstart-deadlock/specs/perturbation-delta-report/spec.md
- Lines: 1-16
- SHA256: 9ea99a758db90c65597427d3e0040468e4975ac0064e352ecc15f585f9352de2

```md
## MODIFIED Requirements

### Requirement: baseline 序列保真自检（delta 信任锚）
系统 SHALL 统计 baseline 臂的每步决策与录下决策的一致率（`baseline_fidelity`）；比对 SHALL 在 **gate-level** 进行——复现须触达同一 gate（accept，或同一 `reject_reason` 类别），不得把"换了个 gate 拦下"误判为复现。一致率低于阈值时标 `untrustworthy` 并拒给 delta 结论。

#### Scenario: 高一致率 delta 可信
- **WHEN** baseline-sim 决策与录下决策在 gate-level 一致率 ≥ 阈值（默认 0.8）
- **THEN** 系统 SHALL 给出 delta 结论，并随报告报出 `baseline_fidelity`

#### Scenario: 低一致率拒答
- **WHEN** baseline-sim 与录下决策 gate-level 一致率 < 阈值
- **THEN** 系统 SHALL 标 `untrustworthy` 并 SHALL NOT 给 delta 方向结论

#### Scenario: 换 gate 拦计为不复现
- **WHEN** 录下决策为某 gate 拒（如 `rr_below_floor`），baseline-sim 却被另一 gate 拒（如 `ev_gate` / `daily_bearish_required`）或反之
- **THEN** 系统 SHALL 将该步计为不复现（计入 divergence / 拉低 baseline_fidelity），SHALL NOT 因二者同属"非-accept"类即算复现
```

## openspec/changes/fix-cf-lab-ev-coldstart-deadlock/specs/replay-report-driver/spec.md

- Source: openspec/changes/fix-cf-lab-ev-coldstart-deadlock/specs/replay-report-driver/spec.md
- Lines: 1-12
- SHA256: bbbb55de7c32fab27f1cd130e3c4c90f56ab00374c567b35dccce0e22c14e2a3

```md
## ADDED Requirements

### Requirement: 可回放记录过滤按内容而非 stale 标志
报表/方向驱动加载决策磁带时 SHALL 按记录内容判定可回放（`schema_version` 为当前版本 AND `tech_analysis` 非空），SHALL NOT 盲信写入时固化的 `replayable` 标志——旧版本空记录写入时即被标 `replayable=true`，修复不回改磁盘旧记录。

#### Scenario: 驱动过滤旧空记录
- **WHEN** `cf_direction_recommendation` 等驱动 `load_records` 加载磁带
- **THEN** 系统 SHALL 只收 `schema_version=='decision_replay_record.v2' AND tech_analysis 非空` 的记录，过滤掉 v1 旧空记录

#### Scenario: 不盲信 stale replayable
- **WHEN** 某记录 `replayable=true` 但 `schema_version` 为旧版本或 `tech_analysis` 为空
- **THEN** 系统 SHALL 排除该记录，不喂入回放/扫描（避免短路 hold 稀释结论）
```

## openspec/changes/fix-cf-lab-ev-coldstart-deadlock/specs/sequential-perturbation-driver/spec.md

- Source: openspec/changes/fix-cf-lab-ev-coldstart-deadlock/specs/sequential-perturbation-driver/spec.md
- Lines: 1-16
- SHA256: 04acc0d66798cf0b94c454047957383b4573fb2e46ba28b16c98b421128ffa82

```md
## ADDED Requirements

### Requirement: CF EV 状态暖启动播种(破冷启动死锁)
序列驱动 SHALL 在序列起点用录制的滚动胜率把 CF 的 rolling 窗口暖启动播种,使 CF EV gate 起步即贴近 live 决策时的真实胜率,而非冷启动 bayesian 先验导致拒所有开仓的死锁。

#### Scenario: 用录制滚动率播种窗口
- **WHEN** `_seed_cf_prior` 在序列起点初始化 CF
- **THEN** 系统 SHALL 用第一条 record 录制的 `_recent_win_rate`(磁带窗口前真实滚动胜率)等价填满 CF 的 rolling 窗口(按比例的 win/loss 合成条目),使起步 `_recent_win_rate` 等于该录制率

#### Scenario: 合成种子被 CF 真实结果挤出
- **WHEN** CF 自身结算累计达窗口长
- **THEN** rolling 窗口 SHALL 100% 由 CF 自身结果构成(合成种子已 FIFO 挤出),级联真实;合成种子 SHALL NOT 人为抬高 baseline_fidelity

#### Scenario: 两臂共享同一播种
- **WHEN** baseline 臂与 perturbed 臂分别跑序列
- **THEN** 两臂 SHALL 从同一播种起步,各自用自身 CF 结果累计,使 delta 干净(系统性偏差在两臂抵消)
```

