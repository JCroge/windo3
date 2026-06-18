# Comet Design Handoff

- Change: fix-cf-lab-fidelity-epoch-resolution
- Phase: design
- Mode: compact
- Context hash: bea7803cf674eb6505a69466b9601abe1407410cb2e28a2f1b162e9741e502fd

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/fix-cf-lab-fidelity-epoch-resolution/proposal.md

- Source: openspec/changes/fix-cf-lab-fidelity-epoch-resolution/proposal.md
- Lines: 1-38
- SHA256: 6a68b71a5ec5d535abb42c9eb85d59f70b05ca9b975bc0a75698a7cb8b38f97e

```md
## Why

CF 反事实实验室的两个保真度测试跌破 0.85 阈值（`test_sequential_baseline_fidelity_restored` fidelity=0.732、`test_production_baseline_restores_fidelity` 同失败，base-ref 1bbbc24 即失败，与 rotation change 无关）。若 lab 再度 untrustworthy，据其做的方向推荐不可信。

explore 阶段（全只读实测）定位三层：

1. **主因（纪元 pin bug）**：`utils/decision_replay.py:96` `effective = {**base, **(config or {})}` 把测试传的 `{"ladder_rr_enabled": False}` 作扰动 override **无条件压过** `record.config_snapshot`。磁带横跨两纪元（1655 旧 v2 无 snapshot + 1189 新 v3 含 snapshot 且 `ladder_rr_enabled=True`，录于 06-17 lever2 默认开之后）。全局 pin ladder=False 对新纪元记录系统性发散。实测：global_false **0.729** / naked **0.525** / 逐记录纪元解析 **0.890**（过阈值，接近测试注释期望的 ~0.91）。

2. **可信度被严格指标低估**：accept/reject 二元保真 v3=**0.991** / full=**0.985**——lab 对方向（开/不开仓）决策其实可信。gate 严格保真（哪个门拦）惩罚了"同为 reject、仅门归因不同"的情况，低估了真实可信度。

3. **残余（range_position→ev_gate，占 v3 不一致 84%/203 次，均 reject→reject）**：已**证伪** capture 缺口（字段 `position_in_24h_range=0.1755` 在 `tech_analysis.entry_context/short_context` 都录上）与 ev_winrate 纪元错配（补该键纪元值后 v3 保真纹丝不动）。机制收窄到 **ev_gate EV 计算在回放 pass→fail**，真因待逐记录追 EV 内部。

附带发现：config_snapshot 纪元不完整——`ev_winrate_gate_enabled`/`ev_neutral_p_win` 仅在 298/1205 条 v3 记录里（06-18「EV胜率门解耦」才加进 DEFAULTS）。泛化问题：**任何"录制后才进 snapshot 的键"，在缺键记录回放时会用当前 production 默认而非录制纪元默认**——默认翻转即漂移。

## What Changes

- **修纪元解析分层**：`replay_decision` 的有效 config 合并从"override 无条件压过 snapshot"改为正确三层——`production_base_config()` < **纪元兜底**（缺键按录制纪元默认补齐：`ladder_rr_enabled` 缺→False、`ev_winrate_gate_enabled` 缺→True 等）< `record.config_snapshot` < **真扰动 override**（CF 实验扰动机制保持，仍在最顶层）。`run_arm` 同步对齐传 config 方式。
- **两个失败测试改用纪元解析**：baseline 回放不再传全局 `{"ladder_rr_enabled": False}` pin，改由 harness 逐记录纪元解析。
- **新增 accept/reject 二元保真为主可信度指标**（SHALL ≥0.95，实测 0.985），gate 严格保真降为诊断性次指标（保留但不作硬可信门，或放宽阈值并标注其语义）。
- **range_position→ev_gate 残余深挖（调查任务）**：逐记录对比录制 vs 回放的 ev_gate EV 内部输入/输出，钉死 pass→fail 真因；据结果决定本 change 内修复或记 follow-up（起点证据：非 capture 缺口、非 ladder/ev_winrate 纪元）。

## Capabilities

### New Capabilities

<!-- 无新 capability -->

### Modified Capabilities

- `deterministic-replay-harness`: 「回放有效 config 与 live 生产一致」需求修订——缺键 fallback 从"当前 production 基线"改为"录制纪元默认"（抗默认漂移），明确纪元兜底与扰动 override 的分层顺序；新增 accept/reject 二元保真作为主可信度判据。

## Impact

- **代码**：`utils/decision_replay.py`（`replay_decision` 有效 config 三层合并 + 纪元兜底 helper）、`utils/sequential_perturbation.py`（`run_arm` 传 config 对齐）。
- **测试**：`tests/test_decision_replay.py`、`tests/test_sequential_perturbation.py`（baseline 改纪元解析 + 新增 accept/reject 断言）。
- **observability-only**：CF lab 全程离线、write-only，不进生产决策链路；无 live 行为变更，不需重启交易进程。
- **诊断产物**：range_position/ev_gate 深挖结论记入验证报告或 follow-up。
- **下游**：恢复 lab 可信后，`cf_direction_recommendation.py` 等方向推荐工具结论方可信赖。
```

## openspec/changes/fix-cf-lab-fidelity-epoch-resolution/design.md

- Source: openspec/changes/fix-cf-lab-fidelity-epoch-resolution/design.md
- Lines: 1-52
- SHA256: 51e8f643f95edde9d3ad3c169fa960795f06c20a0d1e7069ca0680c33526012c

```md
# Design (高层)

> 深度技术设计见 comet-design 阶段产出的 Design Doc（`docs/superpowers/specs/`）。本文件只记高层架构决策。canonical spec = openspec。

## 架构决策

**纪元解析三层合并（采纳）。** `replay_decision` 的有效 config 改为：

```
production_base_config()        # 生产静态默认（会随默认翻转漂移）
  < 纪元兜底 epoch_defaults(rec) # 缺键按"录制纪元"补：ladder 缺→False、ev_winrate 缺→True
  < record.config_snapshot       # 录制时实际值（每条自描述，优先）
  < 真扰动 override (config)      # CF 实验扰动旋钮，仍在最顶层
```

否决：
- **现状（override 压 snapshot）**：测试把"纪元 pin"塞进"扰动 override"层，对新纪元记录系统性发散（0.729）。
- **全局 pin / 裸回放**：磁带横跨两纪元 + production 默认已翻转，单一纪元救不了任一侧（naked 0.525）。
- **只用 v3 完整 snapshot 记录**：v3-only 仍 0.797（残余非纪元问题），且丢弃数据。

## 可信度指标改判

gate 严格保真（哪个门拦）对"同 reject、门归因不同"过度敏感（range_position vs ev_gate 短路顺序），低估可信度。**新增 accept/reject 二元保真为主判据**（≥0.95，实测 v3 0.991 / full 0.985），gate 保真降为诊断次指标。

## 数据流

```
record ──┬─ config_snapshot (每条自描述纪元, 缺键则纪元兜底补)
         ├─ tech_analysis (完整, 含 entry_context.position_in_24h_range)
         └─ state_snapshot (_recent_win_rate 等)
              ↓ replay_decision 三层合并有效 config
         真实 Judge._make_decision → gate / accept-reject
              ↓ 对比
         _gate_of_recorded vs _gate_of_replayed  (诊断次指标)
         accept/reject(recorded) vs accept/reject(replayed)  (主可信度指标)
```

## 残余调查（range_position→ev_gate）

逐记录追 ev_gate EV 内部输入/输出，钉死 pass→fail 真因。已排除：capture 缺口（字段在）、ladder/ev_winrate 纪元。据结论决定本 change 修或 follow-up。

## 边界

- observability-only：全程离线 write-only，红线守卫禁生产链路 import，无 live 行为变更。
- 纪元兜底表是显式 map（键→录制纪元默认），新键加 DEFAULTS 时需登记其"加入前纪元默认"。

## 测试策略

- 纪元解析后 baseline gate 保真 ≥0.85（实测 0.890）。
- accept/reject 二元保真 ≥0.95（实测 0.985）。
- 扰动 override 仍能翻转目标旋钮（CF 实验机制不破，回归 perturbation 测试）。
- 纪元兜底对缺键记录补对值（单测 ladder 缺→False、ev_winrate 缺→True）。
```

## openspec/changes/fix-cf-lab-fidelity-epoch-resolution/tasks.md

- Source: openspec/changes/fix-cf-lab-fidelity-epoch-resolution/tasks.md
- Lines: 1-24
- SHA256: d93f690a3b2b0859f2cbbdc5ef5f2f900e641f97ff8e233ecc22ba5e26415975

```md
# Tasks

> 详细任务在 comet-build 阶段细化。本清单为 open 阶段初始边界。

## 纪元解析分层（utils/decision_replay.py）
- [ ] 新增纪元兜底 helper（缺键按录制纪元默认补：ladder 缺→False、ev_winrate_gate_enabled 缺→True、ev_neutral_p_win 缺→0.55）
- [ ] `replay_decision` 有效 config 改三层：production_base < 纪元兜底 < config_snapshot < 扰动 override
- [ ] `utils/sequential_perturbation.py` `run_arm` 传 config 对齐（不再让单一 arm config 压过 per-record snapshot）

## 测试改纪元解析 + accept/reject 主指标
- [ ] tests/test_decision_replay.py：baseline 不再传全局 ladder pin，改纪元解析；gate 保真 ≥0.85
- [ ] tests/test_sequential_perturbation.py：同上
- [ ] 新增 accept/reject 二元保真断言 ≥0.95（实测 0.985）
- [ ] gate 严格保真降为诊断次指标（保留断言但放宽/标注语义）
- [ ] 回归 perturbation 测试确认扰动 override 仍能翻转目标旋钮

## 残余深挖（调查任务）
- [ ] 逐记录对比 range_position→ev_gate 发散记录的录制 vs 回放 ev_gate EV 内部输入/输出
- [ ] 钉死 ev_gate pass→fail 真因（已排除 capture 缺口 / ladder / ev_winrate 纪元）
- [ ] 据结论：本 change 内修复 或 记 follow-up（写入验证报告）

## 验证
- [ ] `python3 -m pytest tests/test_decision_replay.py tests/test_sequential_perturbation.py -q` 全绿
- [ ] 全量回归无退化
```

## openspec/changes/fix-cf-lab-fidelity-epoch-resolution/specs/deterministic-replay-harness/spec.md

- Source: openspec/changes/fix-cf-lab-fidelity-epoch-resolution/specs/deterministic-replay-harness/spec.md
- Lines: 1-42
- SHA256: 489a61a9736e2e0b5d28ac6cb4abf47f8547b0bb7792ff77e6f1e1b41abefb3b

```md
## MODIFIED Requirements

### Requirement: 回放有效 config 与 live 生产一致
回放 harness 的有效决策 config SHALL 与**录制该决策时的纪元** live 生产 config 一致，不得用空 config 致 `_install_config_flags` 把 Phase-2 等 flag 默认到与生产相反的值。当某 config 键在录制之后才加入 DEFAULTS（默认值随之翻转），缺该键的旧记录回放 SHALL 用**录制纪元默认**而非当前 production 默认，避免默认漂移致系统性发散。有效 config 的合并优先级 SHALL 为：`production_base_config()` < 纪元兜底（缺键的旧纪元默认）< `record.config_snapshot`（录制实际值优先）< 扰动 override（CF 实验旋钮，最顶层）。

#### Scenario: 优先用录制 config_snapshot
- **WHEN** 回放一条带 `config_snapshot` 的记录
- **THEN** harness SHALL 用该 `config_snapshot` 的键值覆盖 production 基线与纪元兜底（录制实际值优先）

#### Scenario: 缺键用录制纪元默认 fallback
- **WHEN** 回放的记录其 `config_snapshot` 缺少某个当前 DEFAULTS 中存在的键（该键在录制后才加入）
- **THEN** harness SHALL 用该键的**录制纪元默认**（来自纪元兜底表，如 `ladder_rr_enabled`→False、`ev_winrate_gate_enabled`→True），SHALL NOT 用当前 production 默认（其默认可能已翻转）

#### Scenario: 扰动 override 不被纪元解析覆盖
- **WHEN** 回放传入扰动 override（CF 实验旋钮）
- **THEN** 该 override SHALL 在最顶层生效，覆盖纪元解析后的 baseline（保证 CF 扰动机制不被纪元修复破坏）

#### Scenario: 纪元解析恢复 baseline 保真
- **WHEN** 用纪元解析（逐记录按录制纪元）对全量真实磁带跑零扰动 baseline 回放
- **THEN** gate-level baseline_fidelity SHALL 跨过可信阈值（实测全局 pin 0.729 → 纪元解析 0.890）

### Requirement: accept/reject 二元保真为主可信度判据
CF lab 的主可信度判据 SHALL 为 accept/reject 二元保真（录制与回放在"开仓 vs 不开仓"上的一致率），而非 gate-level 严格保真（哪个门拦）。gate-level 严格保真对"同为 reject、仅门归因短路顺序不同"的情况过敏，低估真实可信度，SHALL 降为诊断性指标（记录/打印，不作硬可信门）。

#### Scenario: accept/reject 二元保真作硬门
- **WHEN** 对全量真实磁带跑零扰动 baseline 回放
- **THEN** accept/reject 二元保真 SHALL ≥0.95（实测 0.985），作为 lab 可信度硬断言

#### Scenario: gate 严格保真降为诊断
- **WHEN** baseline 回放计算 gate-level 严格保真
- **THEN** 其值 SHALL 被记录/打印供诊断，SHALL NOT 作为 lab 可信度的硬失败门

### Requirement: 纪元兜底表防静默漂移守卫
纪元兜底表（`_EPOCH_FALLBACK`）SHALL 覆盖所有"在 DEFAULTS 中存在、却缺于部分历史记录 `config_snapshot`、且影响 gate 决策"的键。系统 SHALL 提供守卫测试：任何此类缺键若未在 `_EPOCH_FALLBACK` 或显式 `_GATE_IRRELEVANT` allowlist 中分类，则测试失败，强制人工登记，防止未来默认翻转致保真度静默复发。

#### Scenario: 缺键必须被显式分类
- **WHEN** 扫描磁带发现某 DEFAULTS 键缺于部分记录的 `config_snapshot`
- **THEN** 守卫测试 SHALL 断言该键 ∈ `_EPOCH_FALLBACK` ∪ `_GATE_IRRELEVANT`，否则失败

#### Scenario: 纪元兜底键不悬空
- **WHEN** 校验 `_EPOCH_FALLBACK`
- **THEN** 其每个键 SHALL 存在于当前 `config_loader` DEFAULTS（无 stale/typo 条目）
```

