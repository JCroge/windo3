# Comet Design Handoff

- Change: fix-shadow-logger-replay-baseline-parity
- Phase: design
- Mode: compact
- Context hash: 3c6a3f5f4ab0a2a4909e8dac859ea77794cd959406f6f48809fdce65c5304a8a

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/fix-shadow-logger-replay-baseline-parity/proposal.md

- Source: openspec/changes/fix-shadow-logger-replay-baseline-parity/proposal.md
- Lines: 1-35
- SHA256: 59656b6829224b5cd3eff8a596c54487379edbd03501cd22a72a7fac13a9c9a3

```md
## Why

前向影子决策记录器（`trend-entry-shadow-decision-logger`，2026-06-17）的对比口径是 **`live(real) vs replay(both-levers)`**——拿一个**真·live 决策**去对比一个**复盘决策**，把复盘保真误差混进了"lever1 纯增量"。实证：截至 2026-06-20 累积 3809 条影子记录里 37 条 `shadow_holds`（real 开仓但 shadow hold）经本地重放证明**全部不是 lever1 效应**：

- 用同一磁带 bundle 本地重跑 `replay(lever2-only)` vs `replay(both-levers)`，两臂对这 37 条**零分歧**（lever1 真实增量 = 0）。
- 其中 **13/37** 是 `replay(lever2-only)` 也复现不出 live 当时的 accept（复盘失真、方向偏保守 hold）。

即 `shadow − real(live)` 这个差里装的是**复盘失真**，不是 lever1。同仓库已建成的 `perturbation_replay` / `sequential_perturbation` 都用 **replay-vs-replay 两臂同复盘**（系统性偏差在 delta 抵消）+ **baseline 复现自检闸**（replay-baseline 不复现 live 即标 `baseline_mismatch` 排除）规避此问题；影子记录器两条都缺，导致其 lever1 增量结论当前不可信。

> 注：诊断初期曾假设是 ev-gate config parity（live `config.yaml` 把 `ev_winrate_gate_enabled` 改 false 而复盘用 true）。该假设经实测**证伪**——34/37 条记录的 `config_snapshot.ev_winrate_gate_enabled` 正确为 `False`、0 条为 `True`。本 change **不动** ev-gate config。

## What Changes

- 影子对比口径从 `live(real) vs replay(both-levers)` 改为 **`replay(lever2-only baseline) vs replay(both-levers shadow)`**：lever1 增量 = 两臂复盘之差，系统性复盘偏差在 delta 抵消（对齐 `sequential_perturbation` 两臂同估算原则）。
- 新增 **baseline 复现自检闸**：`replay(lever2-only)` 的 accept/reject 必须复现 live record 的 accept/reject，否则该条标 `baseline_mismatch=True`，排除出 lever1 增量统计（对齐 `perturbation_replay` 的 `baseline_mismatch` 守卫）。
- 影子日志 jsonl 新增字段：`baseline_action`、`baseline_gate`、`baseline_mismatch`；`flip_kind` 改为基于 `baseline vs shadow`（而非 `real(live) vs shadow`）。
- 红线不变：observability-only write-only、fail-safe 影子绝不破 live、影子/baseline 复盘绝不 publish 真实 bus / 不下单 / 不 mutate live 状态。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `shadow-decision-logger`: 「对比隔离 lever1 增量」要求从 `影子 − 实盘(live)` 改为 `replay(both-levers) − replay(lever2-only)`，并新增 baseline 复现自检闸 requirement（low-fidelity 记录须标 `baseline_mismatch` 并排除出增量统计）。

## Impact

- `utils/shadow_decision_logger.py`：新增 baseline 复盘臂（再跑一次 `replay_decision(bundle, BASELINE_CONFIG)`）+ 自检逻辑 + 新 record 字段；`flip_kind` 改基于 baseline vs shadow。
- `agents/trading/judge.py`：shadow chokepoint 接线把 live record 的 accept/reject 传入用于自检（不改决策逻辑）。
- `tests/`：新增/更新影子记录器单测（baseline 自检、两臂 delta、`baseline_mismatch` 排除、fail-safe 不破 live）；红线守卫不回归。
- 离线驱动 `cf_shadow_lever1_compare.py`：按新字段筛选（排除 `baseline_mismatch`）。
- observability-only：不碰 live 决策、不改 ev-gate config、live 行为零回归。
```

## openspec/changes/fix-shadow-logger-replay-baseline-parity/design.md

- Source: openspec/changes/fix-shadow-logger-replay-baseline-parity/design.md
- Lines: 1-42
- SHA256: 129ea62f990c7ea8d4f70932352fffc2ecf152d9791e713578b124b2c3c13096

```md
## 高层架构决策（详细技术设计见 comet-design 阶段的 Superpowers Design Doc）

### 问题本质

影子记录器把 **live 决策（无复盘偏差）** 与 **replay(both-levers)（有复盘偏差）** 直接相减，差里混入复盘保真误差。实测 37 条 shadow_holds 全是复盘失真（lever1 两臂复盘 delta = 0；13/37 baseline 复盘复现不出 live accept）。

### 方案：两臂同复盘 + baseline 自检闸

```
现状(错):
  live(real, 无复盘偏差) ── 减 ──> replay(both, 有复盘偏差)
                                    差 = lever1 + 复盘偏差   ✗

改后(对):
  replay(lever2-only, baseline) ── 减 ──> replay(both, shadow)
                                          差 = lever1（复盘偏差两臂抵消） ✓
  + 自检: replay(lever2-only).accept/reject == live.accept/reject ?
            否 → baseline_mismatch=True → 排除出增量统计
```

### 关键决策

1. **两臂都走 `replay_decision`**：baseline 臂 `{path_evidence:False, ladder:True}`（= live 现配置）、shadow 臂 `{path_evidence:True, ladder:True}`。lever1 增量 = shadow − baseline，复盘机器的系统性偏差对两臂同向、在 delta 抵消。依据：`sequential_perturbation` 已确立"两臂同估算 → 偏差在 delta 抵消"原则。

2. **保留 live record 仅用于自检，不用于增量**：live 的 accept/reject 作为 baseline 复盘的"金标准"，baseline 复盘背离它即标 `baseline_mismatch`、排除。依据：`perturbation_replay` 的 baseline 复现自检闸。

3. **自检只比 accept/reject 二元类别**：不要求 plan 连续字段一致（复盘 plan 容差是另一层，参 golden-master <0.5%）。开仓 vs 非开仓的二元一致即认为 baseline 可信，符合 `fix-cf-lab-fidelity-epoch-resolution` 把可信度判据定为 accept/reject 二元保真 ≥0.95 的先例。

4. **多一次 replay 的成本**：每信号现在跑 2 次 `replay_decision`（baseline + shadow）而非 1 次。复盘是纯计算（mock 外部 await、缓存 llm、无网络），fire-and-forget 在 publish 后，对 live 零延迟；成本可接受。

### 红线（不变）

- observability-only write-only：两条复盘臂绝不 publish 真实 bus / 不下单 / 不 mutate live Judge·portfolio·cooldown·daily-stop。
- fail-safe：任一臂复盘异常 → 跳过本次影子记录、绝不破 live 决策。
- 不动 ev-gate config（config-parity 假设已证伪）。
- 红线守卫 `tests/test_cf_red_line_guard.py` 禁读影子产物不回归。

### 非目标

- 不深挖复盘失真的具体未还原状态根因（baseline 自检闸对失真源不可知地兜底，无需定位到字段）。
- 不改 lever1/lever2 策略本身、不改 ev-gate。
- 不补影子日志 retention（既有 follow-up，另议）。
```

## openspec/changes/fix-shadow-logger-replay-baseline-parity/tasks.md

- Source: openspec/changes/fix-shadow-logger-replay-baseline-parity/tasks.md
- Lines: 1-32
- SHA256: fa8539990ab732524eaa48c556a0d5f755dda9336e695505ae39d1496f551733

```md
## 1. 影子记录器：两臂复盘 + 自检

- [ ] 1.1 `utils/shadow_decision_logger.py` 加 `BASELINE_CONFIG={path_evidence_aligned_enabled:False, ladder_rr_enabled:True}`，与现有 `SHADOW_CONFIG` 并列
- [ ] 1.2 `log_shadow_decision` 跑两条复盘臂：`baseline=replay(bundle, BASELINE_CONFIG)` + `shadow=replay(bundle, SHADOW_CONFIG)`
- [ ] 1.3 `compute_flip_kind` 改基于 `baseline_action` vs `shadow_action`（替换原 `real_action` vs `shadow_action`）
- [ ] 1.4 新增 baseline 自检：`baseline_mismatch = (baseline 复盘 accept/reject 类别 != live record accept/reject 类别)`；抽 `_is_accept(action)` helper
- [ ] 1.5 `build_shadow_record` 新增字段 `baseline_action` / `baseline_gate` / `baseline_mismatch`，保留 `real_action`/`real_gate`（供自检追溯）
- [ ] 1.6 fail-safe 不变：任一臂异常 → 跳过本次记录、返回 None、绝不抛

## 2. judge chokepoint 接线

- [ ] 2.1 `agents/trading/judge.py` shadow hook 把 live record 的 accept/reject（real action）传入 `log_shadow_decision` 供自检（不改决策逻辑、防御性 getattr）
- [ ] 2.2 确认 hook 仍在 publish 之后、fire-and-forget、异常 fail-safe（不回归 2026-06-17 契约）

## 3. 离线驱动

- [ ] 3.1 `cf_shadow_lever1_compare.py` 筛 `flip_kind=shadow_opens` 时先剔除 `baseline_mismatch=True` 记录
- [ ] 3.2 报表注明被排除的 `baseline_mismatch` 条数（透明，不静默丢弃）

## 4. 测试

- [ ] 4.1 单测：baseline 复盘复现 live accept → `baseline_mismatch=False`，进增量
- [ ] 4.2 单测：baseline 复盘背离 live（复盘 hold / live accept）→ `baseline_mismatch=True`，排除
- [ ] 4.3 单测：两臂复盘相同 → `flip_kind=same`；shadow 开/baseline 不开 → `shadow_opens`
- [ ] 4.4 单测：任一臂 `replay_decision` 抛异常 → fail-safe 跳过、live 不受影响
- [ ] 4.5 红线守卫 `tests/test_cf_red_line_guard.py` 不回归（决策/风控路径禁读影子产物）
- [ ] 4.6 main() 登记新用例，全量回归零退化

## 5. 文档

- [ ] 5.1 更新 CLAUDE.md 风控红线里影子记录器条目（对比口径改两臂复盘 + baseline 自检闸）
- [ ] 5.2 comet-design 阶段产出 Superpowers Design Doc（深度技术设计）
```

## openspec/changes/fix-shadow-logger-replay-baseline-parity/specs/shadow-decision-logger/spec.md

- Source: openspec/changes/fix-shadow-logger-replay-baseline-parity/specs/shadow-decision-logger/spec.md
- Lines: 1-40
- SHA256: 46eab67e958f6d2775d7344869f0b3f645810a615ed2db9da766fd40c6b41aeb

```md
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
```

