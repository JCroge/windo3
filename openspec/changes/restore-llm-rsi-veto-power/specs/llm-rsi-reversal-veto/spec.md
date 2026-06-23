## ADDED Requirements

### Requirement: 反转合流否决

当一笔 `open_long` 或 `open_short` 候选即将发出时，系统 SHALL 评估两个相互独立的反转信号是否共振：(a) LLM 给出明确的反向开仓建议（`llm_action ∈ {open_long, open_short}` 且方向与候选相反）；(b) RSI 背离与候选方向相反（候选为多遇 `bearish_div`，候选为空遇 `bullish_div`，读 `tech.momentum.rsi_divergence` 原始信号，不读被压制的背离分数）。仅当两者**同时**成立时 SHALL 触发否决，将该候选路由到等回调（`deferred_reversal_veto`），而非立即开仓，也非硬性 hold 拒单。该判定 SHALL 由单一函数（`_reversal_confluence_veto`）实现，并在**主路径即时开仓终点**（持有新鲜 LLM 与 RSI 信号处）调用。deferred 再分发路径仅在价格回调达标时触发——即 veto 期望的结果，且该处无新鲜 LLM 读取——故 SHALL NOT 在其上重复该判定（语义正确的边界，避免第二份内联实现）。

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
