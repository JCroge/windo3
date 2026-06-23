---
comet_change: pseudo-resonance-downweight
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-23-pseudo-resonance-downweight
status: final
---

# Design Doc: pseudo-resonance-downweight（伪共振降权 · 病根1a）

> 针对策略诊断病根1。高层决策（MA 趋势块封顶 + config 化；不引入新信号；保护层不动）已由 OpenSpec + brainstorming 定。本文档为技术实现设计。

## 1. 问题（真实磁带量化）

`_compute_score`(judge.py:3403) 中 `rule_signal(±35/±20)` + `trend(0~±20)` + `higher_tf_bias(±10)` 同源于一条 MA 趋势，线性叠加 → 伪共振。189 笔 accept：MA簇占 |score| **中位 67%/均值 74%**，**47/189(25%) 纯靠 MA簇**（独立信号净零/反向也照开），39% MA簇占比≥80%。

## 2. 核心改动：MA 趋势块封顶

```python
# _compute_score 内，把三段同源贡献先合成 bloc，再同向封顶
rule_c = +35/-35/+20/-20/0          # rule_signal / ma_aligned（现有逻辑）
trend_c = ±(20 * effective_strength/100)  # 现有趋势分量（含 RSI 超买/卖 ×0.3）
htf_c   = +10/-10/0                  # higher_tf_bias
ma_bloc_raw = rule_c + trend_c + htf_c
cap = self._ma_bloc_cap             # 默认待 CF 回放定，候选 ~45
ma_bloc = math.copysign(min(abs(ma_bloc_raw), cap), ma_bloc_raw) if ma_bloc_raw else 0.0
score += ma_bloc
# 独立信号(RSI背离/OI/鲸鱼/散户/taker) + 保护层(RSI cap/4h RSI) 维持原样
```

- **同向封顶**：bloc 内部反向分量先自然抵消，再对合计绝对值封顶。cap 不影响独立信号与保护层。
- **diminishing returns**：当 rule(35)+trend(18)+htf(10)=63 > cap(45) 时，多出的同源"确认"被削掉，独立信号必须补位才能把强信号推过入场门（has_rule_signal 门槛 25 / htf 对齐 35）。
- 现有 RSI 超买/卖对 trend 的 ×0.3、RSI 极端 cap、4h RSI 折扣**在 bloc 之外/之后**照常作用（保护层不动）。

## 3. 实现要点（单点收口）

- `_compute_score` 是单一函数 = 天然单点；改动集中在其内部的 bloc 合成段，不在别处复制。
- 三段贡献当前散落在 `_compute_score` 三处（§0 rule、§1 trend、§7 htf），需重构成"先各自算 component，合成 bloc 封顶后一次性加到 score"。保留各 component 的现有条件逻辑（RSI 折扣等）不变，仅改"如何汇总"。

## 4. config（four-segment，现状全硬编码）

| 键 | 默认 | 说明 |
|---|---|---|
| `pseudo_resonance_downweight_enabled` | 见 §6 | 总开关；false=三段线性叠加（回退旧行为） |
| `ma_bloc_cap` | 待 CF 回放定（候选 45） | MA 块同向合计封顶绝对值 |

各分量权重（rule/ma/trend/htf）保守起步**维持原硬编码值**，只引入 cap；如需再调权重，后续增量。

## 5. 归因（observability）

新增 attribution：`ma_bloc_contribution`（封顶后 bloc 值）、`independent_contribution`（独立信号合计）、`ma_bloc_capped`（bool，是否触发 cap）。供 Reviewer/CF 切分降权效果。

## 6. 验证（红线）—— CF 回放

- **event_backtest 不适用**：走 RobustStrategy MA 信号、不调 `_compute_score`。
- **CF 确定性回放**（`utils/decision_replay.py`）：跑真实 `_make_decision`→`_compute_score`，喂真实 tech 磁带，两臂 off vs on（不同 cap）。
- **通过标准**：(1) 被 cap 影响子集（ma_bloc_capped=true 且决策翻转）的 accept→reject/defer 方向合理——砍掉 25% 无独立佐证的纯 MA 追势单；(2) 该子集 PnL 分布不变差；(3) 未触发 cap 的决策 score/决策不变（全量无回归）。
- **cap 默认值 + 上线缓进**：依 CF 回放结果定（可能保守起步如 cap=50 先观察，再收到 45）。
- 单元测试：bloc 封顶数学（同向超 cap 削、未超不动、内部反向抵消）、开关 off 回退（线性叠加）、独立信号/保护层不受影响、归因字段。

## 7. 边界与风险

- 不引入新独立信号源（病根1b）；不碰 RSI 背离压制 / RSI 极端 cap / 4h RSI 折扣 / veto / 出场 / 体制 / 空单硬门。
- score 是全系统核心，爆炸半径大 → 默认保守 + CF 回放把关 + 开关回退 + 重启生效。
