# Comet Design Handoff

- Change: pseudo-resonance-downweight
- Phase: design
- Mode: compact
- Context hash: 61bd2c7f03402b446fa78f088eeaf272c25303c4a075227d9d03884fafb1141f

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/pseudo-resonance-downweight/proposal.md

- Source: openspec/changes/pseudo-resonance-downweight/proposal.md
- Lines: 1-41
- SHA256: 6d2ebac46cabbeefbda2ef768974b0a95850d5b176270159193f14ff3859aa94

```md
# Proposal: pseudo-resonance-downweight（伪共振降权 · 病根1a）

## Why

策略诊断（agent memory `strategy-no-directional-edge-diagnosis`）**病根1：伪共振**。现行 `_compute_score`（judge.py:3403）里多个打分分量**同源于一条 MA 趋势**，在 1h/4h/1d 重复计权：

- `rule_signal` entry ±35 / `ma_aligned` ±20（MA crossover/alignment）
- `trend` direction×strength → 0~±20（MA 方向×强度）
- `higher_tf_bias` ±10（高周期 MA）

四者共线非独立 → "htf_votes 确认"是幻觉，错时一起错。真正独立的信号（OI 背离 ±12、鲸鱼 ±15、taker ±8、散户 ±8、RSI 背离）权重反而小。

**真实磁带量化（189 笔 accept）**：MA簇贡献占 |score| **中位 67% / 均值 74%**；**47/189(25%) 完全靠 MA簇**（独立信号净零或反向也照开）；39% 的 accept MA簇占比≥80%。坐实"一条 MA 趋势投 3-4 票"。

## What Changes

把 `rule_signal / ma_aligned / trend / higher_tf_bias` 视为**单一「MA 趋势块」**，其合计贡献**封顶**（diminishing returns：多个同源确认不再线性叠加），使**独立信号必须说话**才能把强信号推过入场门。封顶值与各分量权重**走 config 四段式可调可回退**（现状全硬编码）。

## Scope

**In**：
- `_compute_score` 重构 MA 趋势块为封顶合计（如合计 cap ±45，vs 当前可达 ±65）。
- MA 块各分量权重 + cap 走 config（four-segment），默认值由真实磁带验证定。
- 归因记录 MA块贡献 / 独立信号贡献 / 是否触发 cap（observability，切分用）。
- CF 回放验证（红线，见下）+ 单元测试。

**Out（非目标）**：
- 不引入新的独立信号源（→ 病根1b 另起 change）。
- 不碰 RSI 背离 ≤15 压制、不碰 RSI 极端 cap / 4h RSI 折扣（保护层不动）。
- 不碰 veto（病根3 已做）、出场、体制分类、空单硬门。

## Rollback

config 把 MA 块权重/cap 调回原值（或总开关）即回退。生效需重启 live。

## Impact / Red Line

- **策略改动红线**：`event_backtest.py` 走 RobustStrategy MA 信号、**不调 `_compute_score`**，触达不到本改动；**改用 CF 确定性回放**（`utils/decision_replay.py` 跑真实 `_make_decision`→`_compute_score`，喂真实 tech 磁带）做 pre/post 验证——这是能验证 scoring 改动的保真 harness。
- **通过标准**（design 定稿）：被 cap 影响的决策子集，验证降权后 accept/reject 翻转方向与 PnL 分布；触发率合理；无新回归。
- 上线 default 与缓进据 CF 回放结果定（默认可能保守起步或先影子）。
- 生效需重启 live。
```

## openspec/changes/pseudo-resonance-downweight/design.md

- Source: openspec/changes/pseudo-resonance-downweight/design.md
- Lines: 1-44
- SHA256: 1337863fe7b3dbb74cfe70d545e796042d3b3ff10476b9d8d770edda5bc26443

```md
# Design (high-level): pseudo-resonance-downweight（病根1a）

> 高层架构决策。详细机制 + delta spec 在 comet-design 产出。

## 决策 1：MA 趋势块封顶（已定）

把四个同源分量合成一个 bloc 再封顶，而非各自线性叠加：

```
ma_bloc_raw =  rule_component(±35/±20)
             + trend_component(0~±20)
             + htf_component(±10)
ma_bloc = sign(ma_bloc_raw) * min(|ma_bloc_raw|, MA_BLOC_CAP)   # 同向封顶
score = ma_bloc + 独立信号(RSI背离 + OI + 鲸鱼 + 散户 + taker) + 保护层
```

- `MA_BLOC_CAP` 默认候选 ~45（vs 当前可达 65）：据磁带 spike（MA簇中位占 67%），cap 45 会让纯 MA簇最强情形从 65 降到 45，独立信号（最大 RSI背离35 + OI12 + 鲸鱼15…）必须参与才能把强信号推过门。**默认值 comet-design 用 CF 回放定**。
- 仅对**同向**叠加封顶（bloc 内部反向分量正常抵消后再 cap 绝对值）。

## 决策 2：config 四段式（已定，现状全硬编码）

- `pseudo_resonance_downweight_enabled`（总开关）
- `ma_bloc_cap`（默认待定）
- 可选：各分量权重键（rule/ma_aligned/trend/htf），保守起步可只放 cap，权重维持原值。

## 决策 3：保护层 / 独立信号不动（已定）

RSI 背离 ≤15 压制、RSI 极端 cap、4h RSI 折扣、OI/鲸鱼/taker/散户权重均不动——本 change 只重组 MA 共线簇。

## 归因（observability）

新增 attribution：`ma_bloc_contribution`、`independent_contribution`、`ma_bloc_capped`（bool），供 Reviewer/CF 切分降权效果。

## 验证（红线）—— CF 回放，非 event_backtest

- event_backtest 触达不到 `_compute_score`；用 `utils/decision_replay.py` 跑真实 `_make_decision` 重算 score。
- 两臂：开关 off（baseline）vs on（capped），喂真实 tech 磁带。
- **通过标准**（comet-design 定稿）：(1) 被 cap 影响子集的 accept→reject 翻转方向合理（砍掉无独立佐证的纯 MA 追势单）；(2) 该子集 PnL 分布不变差；(3) 全量无新回归（未触发 cap 的决策 score 不变）。
- 单元测试：bloc 封顶数学、同向/反向、开关 off 回退、独立信号不受影响、归因字段。

## 风险

- score 是全系统决策核心，爆炸半径大 → 默认保守 + CF 回放把关 + 开关回退。
- 单点收口：`_compute_score` 是单一函数，天然单点；不得在别处复制 bloc 逻辑。
```

## openspec/changes/pseudo-resonance-downweight/tasks.md

- Source: openspec/changes/pseudo-resonance-downweight/tasks.md
- Lines: 1-11
- SHA256: 507d0917240874b10bf4d5111dd2ce4187e2da4127d07410cd55e95ed949e5a5

```md
# Tasks: pseudo-resonance-downweight（病根1a）

> 高层任务。comet-design 细化 + delta spec 后更新。

- [ ] 1. comet-design：定稿 MA 块组成边界、cap 默认值（CF 回放）、config 键、归因字段、CF 验证方案与通过标准；Design Doc + delta spec
- [ ] 2. 重构 `_compute_score`：抽 MA 趋势块合计 + 同向封顶（单点收口）
- [ ] 3. config_loader 四段式：`pseudo_resonance_downweight_enabled` + `ma_bloc_cap`（+ 可选分量权重）；banner
- [ ] 4. 归因字段 `ma_bloc_contribution`/`independent_contribution`/`ma_bloc_capped`
- [ ] 5. 单元测试：封顶数学/同向反向/开关off回退/独立信号不变/归因
- [ ] 6. CF 回放验证（红线）：off vs on，被 cap 子集翻转方向 + PnL 分布 + 全量无回归；报告落盘
- [ ] 7. 据 CF 结果定 cap 默认值与上线缓进策略
```

## openspec/changes/pseudo-resonance-downweight/specs/pseudo-resonance-downweight/spec.md

- Source: openspec/changes/pseudo-resonance-downweight/specs/pseudo-resonance-downweight/spec.md
- Lines: 1-41
- SHA256: 7b63fd58e5edea6238cacda7d2a7c9c994475007117d6c5b4eef48fa3f98b94f

```md
## ADDED Requirements

### Requirement: MA 趋势块同向封顶

`_compute_score` 中同源于 MA 趋势的三段贡献——`rule_signal/ma_aligned`、`trend`（direction×strength）、`higher_tf_bias`——SHALL 先合成单一「MA 趋势块」再对其同向合计绝对值封顶（`ma_bloc_cap`），而非各自线性叠加到总分。封顶 SHALL 仅作用于该 MA 块；独立信号（RSI 背离、OI 背离、鲸鱼、散户反指、taker）与保护层（RSI 极端 cap、4h RSI 折扣）SHALL 不受影响。

#### Scenario: 同源贡献超 cap 被削
- **WHEN** rule_signal entry_long(+35) + trend bullish 强(+18) + htf bullish(+10) 合计 +63，`ma_bloc_cap=45`
- **THEN** MA 块贡献 SHALL 封顶为 +45（多出的同源确认被削），独立信号需补位才能把分数推过入场门

#### Scenario: 未超 cap 不变
- **WHEN** MA 块同向合计 +30，`ma_bloc_cap=45`
- **THEN** MA 块贡献 SHALL 维持 +30（未触发封顶）

#### Scenario: 块内反向先抵消
- **WHEN** rule_signal +35 但 htf bearish(-10)，trend neutral(0)
- **THEN** MA 块合计 +25，封顶绝对值后仍 +25（内部反向正常抵消，cap 作用于净值绝对值）

#### Scenario: 独立信号与保护层不受 cap 影响
- **WHEN** MA 块被封顶
- **THEN** RSI 背离 / OI / 鲸鱼 / 散户 / taker 贡献与 RSI 极端 cap、4h RSI 折扣 SHALL 与本变更前完全一致

### Requirement: 伪共振降权总开关与可配置封顶

MA 趋势块封顶 SHALL 受配置键 `pseudo_resonance_downweight_enabled` 控制，封顶值 SHALL 由 `ma_bloc_cap` 提供，二者按既有 four-segment 配置模式接入。当开关为 `false` 时，三段贡献 SHALL 线性叠加（与本变更前完全一致），提供实盘即时回退能力。

#### Scenario: 总开关关闭回退线性叠加
- **WHEN** `pseudo_resonance_downweight_enabled=false`，MA 块同向合计 +63
- **THEN** 三段 SHALL 线性叠加为 +63（与变更前一致，不封顶）

#### Scenario: 封顶值可配置
- **WHEN** `ma_bloc_cap=50` 经 config 覆盖
- **THEN** MA 块同向合计绝对值 SHALL 封顶为 50

### Requirement: 伪共振降权归因

`_compute_score`/决策归因 SHALL 记录 `ma_bloc_contribution`（封顶后 MA 块值）、`independent_contribution`（独立信号合计）、`ma_bloc_capped`（bool），供 Reviewer 与 CF 回放切分降权效果。

#### Scenario: 触发封顶时归因记录
- **WHEN** MA 块同向合计超过 `ma_bloc_cap` 被削
- **THEN** 归因 SHALL 含 `ma_bloc_capped=true` 与封顶后的 `ma_bloc_contribution`
```

