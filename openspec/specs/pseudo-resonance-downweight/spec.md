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
