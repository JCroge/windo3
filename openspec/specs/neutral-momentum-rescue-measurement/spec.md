## ADDED Requirements

### Requirement: 测量 population 为 choppy/mixed 中性方向多单候选

测量驱动 SHALL 以**信号口径**测量,population MUST 取决策磁带中所有 `replayable` 记录里 `regime_state ∈ {choppy, mixed}` AND `tech.trend.direction == 'neutral'` 的决策(accept 与 reject 皆纳入,均按假设做多处理),而非仅限被 flat gate 拒绝的记录。

理由:信号口径要测的是"该类 setup 后续涨不涨",独立于策略其它门;限定于 flat-gate-rejected 会因过度确定(over-determination,多门联合拒绝)使样本不足。

#### Scenario: 纳入 population

- **WHEN** 一条 replayable 磁带记录 `regime_state` 为 choppy 或 mixed、`trend.direction == 'neutral'`
- **THEN** 该记录进入测量 population(无论其原始 decision 是 accept 还是 reject)

#### Scenario: 趋势体制不纳入

- **WHEN** 记录 `regime_state` 为 bullish/bearish/trend 或 `direction != 'neutral'`
- **THEN** 该记录不进入 population

### Requirement: 救援候选谓词为方向无关信号且与对照桶判别

驱动 SHALL 用**不依赖 1h `trend.direction` 标签、也不依赖 `trend.strength`** 的客观信号将 population 分为两桶,以验证谓词的判别力:

- **A 桶(救援候选)**:`(trend.daily_bias=='bullish' OR trend.higher_tf_bias=='bullish')` AND `entry_context.pre_12h_return_pct >= pre12h_min` AND `entry_context.position_in_24h_range <= range_pos_max`。
- **B 桶(对照)**:同 population 但**不**满足 A 桶谓词。

驱动 MUST NOT 在谓词中引用 `trend.strength`(它是 `direction=='bullish'` 的隐式代理,正是阀门失效根因)。判据 SHALL 为 A vs B 对比:A 桶净 R 显著为正且 B 桶不显著为正 → 谓词有判别力;A≈B 或两者皆负 → 救援无 edge。

#### Scenario: 命中救援候选(A 桶)

- **WHEN** population 内一条记录 daily_bias 为 bullish、pre_12h_return_pct ≥ 阈值、position_in_24h_range ≤ 阈值
- **THEN** 该记录归入 A 桶

#### Scenario: 对照桶(B 桶)

- **WHEN** population 内一条记录不满足 A 桶谓词(如 pre_12h_return_pct < 阈值,或 daily/htf bias 均非 bullish)
- **THEN** 该记录归入 B 桶,与 A 桶同口径结算供对比

#### Scenario: 谓词不引用 strength

- **WHEN** 审查驱动谓词实现
- **THEN** 谓词 MUST NOT 读取或依赖 `trend.strength`

### Requirement: 标准化合成退出结算

由于 reject 记录不携带 plan(`trade_decision_output` 仅含 reject_reason/attribution),驱动 SHALL 对每条候选合成标准化退出:`entry = price_at_decision`,`side = long`,`stop_loss`/`take_profit` 由**策略典型几何**派生(从磁带 choppy-long accept 流取 median `sl_dist`/`tp ladder`)。A、B 两桶 MUST 用同一退出几何,保证对比口径一致。

驱动 SHALL 报告至少 2 组退出假设的敏感性(如策略中位 / 固定 R:R=1.5 / 更紧 SL),阈值 `pre12h_min` × `range_pos_max` 亦报多组取值,不在代码中写死单一取值。

结算 MUST 用 `utils/counterfactual_pnl.py::resolve_counterfactual` + `klines_1s.db`,TP1 保守口径(同根 K 线 SL/TP 冲突取 SL-first),并按 (symbol, side, >1h gap) 簇去重。CF 结算契约 MUST 传 `entry_price`/`created_at`/`side`/`stop_loss`/`take_profit`(非原始 `entry_ref`)。无 klines 覆盖的候选 MUST 跳过并计数,不得估算填充。

#### Scenario: 合成退出结算净 R

- **WHEN** 候选有 klines_1s 覆盖
- **THEN** 驱动以 entry=price_at_decision + 策略典型 sl/tp 几何,经 resolve_counterfactual TP1 保守口径算出该候选净 R

#### Scenario: 无覆盖跳过

- **WHEN** 候选无 klines_1s 覆盖
- **THEN** 该候选被跳过并计入 skipped 计数,不参与净 R 统计

#### Scenario: 退出几何无效跳过

- **WHEN** 合成的 sl_dist ≤ 0 或 tp1_dist ≤ 0
- **THEN** 该候选被跳过(不产生伪 R)

### Requirement: 诚实门裁定不下调样本阈值

驱动 SHALL 经 `utils/cf_honesty_gate.py::summarize_bucket` 对 A、B 两桶分别裁定,`min_sample=30` 不下调;`n<30` 时输出 `INSUFFICIENT_SAMPLE`,净 R 仅作 suggestive,MUST NOT 作为改门依据。

#### Scenario: 薄样本拒答

- **WHEN** 某桶簇数 < 30
- **THEN** 诚实门对该桶输出 INSUFFICIENT_SAMPLE,报告标注 suggestive、不给出改门建议

### Requirement: observability-only 红线守卫

本 capability 的产物(`cf_neutral_momentum_rescue_ab.py` 及其输出)MUST 为 observability-only,write-only。决策/风控路径(`judge`/`executor`/`portfolio_risk_guard`/`reviewer`/`position_analyst`)MUST NOT import 或读取本驱动及其产物。`tests/test_cf_red_line_guard.py` SHALL 加守卫断言。

#### Scenario: 决策路径禁止 import

- **WHEN** 红线守卫测试扫描决策/风控模块的 import
- **THEN** 若任一模块 import `cf_neutral_momentum_rescue_ab`,测试 MUST 失败

#### Scenario: 不改运行时行为

- **WHEN** 本 change 合入
- **THEN** Judge 开仓门、`_select_rr_floor`、live、config 行为零变更(无任何门逻辑或阈值被修改;驱动甚至不实例化 Judge)
