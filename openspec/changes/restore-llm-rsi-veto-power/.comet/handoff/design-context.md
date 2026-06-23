# Comet Design Handoff

- Change: restore-llm-rsi-veto-power
- Phase: design
- Mode: compact
- Context hash: dbc20782ffe0041e8645b4501d64a700e39356334c4f7232ebffdae966e4ef01

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/restore-llm-rsi-veto-power/proposal.md

- Source: openspec/changes/restore-llm-rsi-veto-power/proposal.md
- Lines: 1-48
- SHA256: db96f8da20e55caa009fea65a8ab4932e1cb73e642a37673af0ba49c6a2f092c

```md
# Proposal: restore-llm-rsi-veto-power

## Why

策略诊断（agent memory `strategy-no-directional-edge-diagnosis`）的**病根3**：规则信号不可否决，反转预警被自我压制。现行代码已核实（judge.py，行号 2026-06-23 实测）：

- **rule_signal 锁方向**：`_compute_score`（judge.py:3316）触发时强制 ±35 主导分数，docstring（judge.py:3319）自称"回测验证83%胜率"——但 live 实测仅 27%，典型过拟合。
- **LLM 不能否决**：判断逻辑（judge.py:1251-1310）注释明写"rule_signal 触发时 LLM 只能降低仓位，不能阻止入场"。LLM 看反 → 只缩仓 60%；LLM hold → 只缩仓 30%。诊断实测 `llm_relation=agree` 4/4 方向全反 = LLM 的"同意"无意义，而它的"反对"又被剥夺否决权。
- **RSI 背离被压制**：背离计分（judge.py:3381-3400）在 HTF 对齐且 RSI 非极端时，把背离分压到 ≤15——压住了唯一的独立反转预警。

净效果：当一笔追势开仓其实买在反转点上时，**没有任何独立反转信号能拦住它**，照样发出 → 放大亏损（病根诊断 + HYPE/marginal60 实证）。

## What Changes

新增**反转合流否决（reversal confluence veto）**：当一笔开仓即将发出，且**同时**满足两个相互独立的反转信号——

1. **LLM 明确看反向**（llm_action 为开仓且方向与待开方向相反，即现有 judge.py:1295-1310「强冲突」分支已识别的条件）；
2. **RSI 背离与开仓方向相反**（待开多单遇 `bearish_div`，待开空单遇 `bullish_div`）——

则把这笔开仓**改路由到已有的 `deferred_pullback`**（等回调再评估），而非立即开仓。

只有**两者共振**才触发（合流，最保守，最小化误杀）。读 `rsi_divergence` 原始布尔信号，**不动打分权重**，因此不与病根1 纠缠。

## Scope

**In**：
- judge.py 新增反转合流检测 + 触发 defer 路由（挂在 LLM 强冲突分支旁 / L1 质量门层，rule_signal 绕不过）。
- 归因字段（observability）：记录是否触发、两路信号取值、被 defer 的方向，供 Reviewer/backtest 切分。
- config 开关 + 阈值（走 config_loader，可回退），因现状打分权重 100% 硬编码。
- event_backtest 验证（**CLAUDE.md 红线**）。
- 单元测试覆盖合流触发/不触发/单信号不触发/defer 路由。

**Out（非目标）**：
- 不下调 rule_signal ±35 强权重（→ 病根1 另起 change）。
- 不改打分各分量权重、不引入独立信号源（→ 病根1）。
- 不动 RSI 背离的 ≤15 分数压制本身（那是 scoring，归病根1；本 change 只用背离的原始布尔信号做 veto 输入）。
- 不碰 RSI≤30 空单硬门（judge.py:890/1015/1443）。
- 不碰出场、体制分类、槽位逻辑。

## Rollback

config 开关关闭即回退旧行为（LLM/RSI 仍只缩仓）；阈值可调。生效需重启 live 交易进程。

## Impact / Red Line

- **策略改动 → 必须经 event_backtest 验证才能上 live**（CLAUDE.md 红线）。design/plan 必须含 event_backtest 验证方案与通过标准。
- 生效需重启 live。
- 鉴于"双信号合流"本就罕见，误杀风险低；上线 default 与缓进策略在 event_backtest 结果后于 design 阶段定稿。
```

## openspec/changes/restore-llm-rsi-veto-power/design.md

- Source: openspec/changes/restore-llm-rsi-veto-power/design.md
- Lines: 1-49
- SHA256: 639423cffd60a914a8cf65a61b021c1324266d9bf3b2bcd3906ff42a4ad7b0cd

```md
# Design (high-level): restore-llm-rsi-veto-power

> 高层架构决策。详细机制 + delta spec 在 comet-design 阶段产出（`docs/superpowers/specs/`）。

## 决策 1：触发 = 双信号合流（已定）

veto 仅在两个**相互独立**的反转信号同时成立时触发：

```
待开方向 dir ∈ {long, short}
  veto_trigger =
       LLM_counter(dir)          # LLM 给出与 dir 相反的开仓建议（强冲突）
   AND RSI_div_against(dir)      # bearish_div(若 dir=long) 或 bullish_div(若 dir=short)
```

- 合流（AND）→ 误杀最少，适配策略衰减期"先立足再放宽"。
- LLM 单边方向实测不可靠（4/4 反），故不让 LLM 单独 veto；RSI 背离单独也不够强 → 必须共振。

## 决策 2：动作 = 转 deferred_pullback（已定）

触发后**不硬拒**，复用已有 `deferred_pullback` 路径让该笔等回调再评估，保留回调后入场的机会，与 regime-aware-long-entry-guard 同哲学。

## 决策 3：不动 scoring（±35 / 背离压制留病根1，已定）

veto 读 `tech.momentum.rsi_divergence` 原始布尔 + LLM action，**不改 `_compute_score` 任何权重**。归因可独立度量 veto 效果。

## 插入点（待 comet-design 对现行代码定稿）

候选：judge.py:1295-1310「has_rule_signal AND llm 反向 → 缩仓60%」分支旁——该处已识别 LLM 强冲突，叠加 RSI 背离判定后改走 defer。需确认主路径 + 三条 deferred 路径（15m/pullback/chase）的覆盖与单点收口（避免病根3 P1-03 那种"第二份内联实现"红线）。

## 配置（走 config_loader 四段式）

- 总开关 `llm_rsi_reversal_veto_enabled`（可回退）。
- 可能的 LLM 置信下限阈值（避免低置信 LLM 反向也触发）——comet-design 定。
- default 值与缓进策略：**event_backtest 结果出来后定稿**。

## 归因（observability）

新增 attribution 字段：`reversal_veto_triggered`、`reversal_veto_llm_action`、`reversal_veto_rsi_div`、`reversal_veto_deferred_dir`。放行与 defer 双路径都写，供 Reviewer 分桶与 backtest pre/post 对比。

## 验证（红线）

- **event_backtest**：构造/复用含"追势买在反转点"的历史样本，对比开 veto 前后该类样本的 PnL/胜率分布；通过标准 comet-design 定（至少：被 veto 样本集净 PnL 不变差、整体不引入新回归）。
- 单元测试：合流触发 defer / 仅 LLM 反向不触发 / 仅 RSI 背离不触发 / 开关 off 回退 / 主路径与 deferred 路径 parity。

## 风险

- 过冻：合流罕见，风险低；开关 + 阈值兜底。
- 单点收口：必须避免多份内联实现（参考既往短单 gate 红线整改）。
```

## openspec/changes/restore-llm-rsi-veto-power/tasks.md

- Source: openspec/changes/restore-llm-rsi-veto-power/tasks.md
- Lines: 1-11
- SHA256: 74fa0bd8fb8da6330757adb2518c19177e145cedd53fcedaaf1c7c27a5b7c12b

```md
# Tasks: restore-llm-rsi-veto-power

> 高层任务清单。comet-design 阶段细化 + 产出 delta spec 后会更新。

- [ ] 1. comet-design：对现行 judge.py 定稿插入点（主路径 + 3 条 deferred 路径单点收口）、config 键、归因字段、event_backtest 验证方案与通过标准；产出 Design Doc + delta spec
- [ ] 2. 实现反转合流检测 helper（读 LLM action + rsi_divergence 原始布尔，判 `LLM_counter AND RSI_div_against`），单点收口
- [ ] 3. 触发时路由到 deferred_pullback；放行/defer 双路径写归因字段
- [ ] 4. config_loader 接入总开关 `llm_rsi_reversal_veto_enabled` + 阈值（四段式：DEFAULTS/HARD_LIMITS/env/yaml），banner 显示
- [ ] 5. 单元测试：合流触发 defer / 仅 LLM 反向不触发 / 仅 RSI 背离不触发 / 开关 off 回退 / 主路径与 deferred parity
- [ ] 6. event_backtest 验证（红线）：追势买在反转点样本 pre/post 分布对比，达通过标准
- [ ] 7. 确定上线 default 与缓进策略（据 event_backtest 结果）
```

## openspec/changes/restore-llm-rsi-veto-power/specs/llm-rsi-reversal-veto/spec.md

- Source: openspec/changes/restore-llm-rsi-veto-power/specs/llm-rsi-reversal-veto/spec.md
- Lines: 1-50
- SHA256: 294037977b5d8e96fc535a36d89fc7dbabbb9e0e5c9c5569fb8f3e830fe75840

```md
## ADDED Requirements

### Requirement: 反转合流否决

当一笔 `open_long` 或 `open_short` 候选即将发出时，系统 SHALL 评估两个相互独立的反转信号是否共振：(a) LLM 给出明确的反向开仓建议（`llm_action ∈ {open_long, open_short}` 且方向与候选相反）；(b) RSI 背离与候选方向相反（候选为多遇 `bearish_div`，候选为空遇 `bullish_div`，读 `tech.momentum.rsi_divergence` 原始信号，不读被压制的背离分数）。仅当两者**同时**成立时 SHALL 触发否决，将该候选路由到等回调（`deferred_pullback`），而非立即开仓，也非硬性 hold 拒单。该判定 SHALL 由单一函数实现并被所有开仓终点共用，避免出现第二份内联实现。

#### Scenario: 双信号合流触发等回调
- **WHEN** 一个 `open_long` 候选，LLM 建议 `open_short`，且 `rsi_divergence='bearish_div'`
- **THEN** 系统 SHALL 触发反转合流否决
- **AND** SHALL 将该候选路由到 `deferred_pullback`（等回调再评估），不立即开仓

#### Scenario: 空单候选合流同样触发
- **WHEN** 一个 `open_short` 候选，LLM 建议 `open_long`，且 `rsi_divergence='bullish_div'`
- **THEN** 系统 SHALL 触发反转合流否决并路由到 `deferred_pullback`

#### Scenario: 仅 LLM 反向不触发
- **WHEN** 一个 `open_long` 候选，LLM 建议 `open_short`，但 `rsi_divergence` 非 `bearish_div`
- **THEN** 系统 SHALL NOT 触发否决（保留现有 LLM 强冲突缩仓行为）

#### Scenario: 仅 RSI 背离不触发
- **WHEN** 一个 `open_long` 候选，`rsi_divergence='bearish_div'`，但 LLM 未给出反向开仓建议
- **THEN** 系统 SHALL NOT 触发否决

### Requirement: 反转合流否决总开关

反转合流否决 SHALL 受配置键 `llm_rsi_reversal_veto_enabled` 控制（按既有 four-segment 配置模式接入）。当其为 `false` 时，系统 SHALL NOT 触发该否决，行为与本变更前完全一致（LLM/RSI 仅缩仓、不否决），提供实盘即时回退能力。

#### Scenario: 总开关关闭回退旧行为
- **WHEN** `risk.llm_rsi_reversal_veto_enabled=false`，一个 `open_long` 候选满足双信号合流条件
- **THEN** 系统 SHALL NOT 触发否决，按变更前逻辑（强冲突缩仓）处理

### Requirement: 反转合流否决归因

无论是否触发，开仓决策的 attribution SHALL 写入反转合流否决的观测字段：`reversal_veto_triggered`（bool）、`reversal_veto_llm_action`（LLM 当时 action）、`reversal_veto_rsi_div`（rsi_divergence 取值）；触发时另写 `reversal_veto_deferred_dir`（被 defer 的方向）。放行路径与 defer 路径均 SHALL 写入，供 Reviewer 分桶与回测 pre/post 分布对比。

#### Scenario: 触发时写入归因
- **WHEN** 反转合流否决触发并路由到 `deferred_pullback`
- **THEN** decision attribution SHALL 含 `reversal_veto_triggered=true`、`reversal_veto_llm_action`、`reversal_veto_rsi_div` 与 `reversal_veto_deferred_dir`

#### Scenario: 未触发也写入观测字段
- **WHEN** 一个开仓候选未触发反转合流否决并正常放行
- **THEN** decision attribution SHALL 含 `reversal_veto_triggered=false`

### Requirement: 不改打分与既有硬门

本能力 SHALL NOT 修改 `_compute_score` 的任何权重（含 rule_signal ±35 与 RSI 背离 ≤15 分数压制），SHALL NOT 修改空单 `RSI<=30` 硬门，SHALL NOT 修改出场、体制分类与槽位逻辑。反转合流否决仅读取 `rsi_divergence` 原始信号与 LLM action 作为否决输入。

#### Scenario: scoring 不受影响
- **WHEN** 反转合流否决评估一个候选
- **THEN** 该候选的 `signal_score` 计算 SHALL 与本变更前完全一致（否决只影响是否转 defer，不改分数）
```

