# Comet Design Handoff

- Change: cf-neutral-momentum-rescue-ab
- Phase: design
- Mode: compact
- Context hash: 2ee13fd322882772a657a65bbe7e4e42c40801536f8ac9278faf3210231f7af1

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/cf-neutral-momentum-rescue-ab/proposal.md

- Source: openspec/changes/cf-neutral-momentum-rescue-ab/proposal.md
- Lines: 1-40
- SHA256: f64e6c4d12aae9d74e591c2e84fa1b23e09d1607f2d31231a65a7ac938cbdbc4

```md
## Why

体制空仓硬门 `Judge._classify_regime_flat_gate`(2026-06-25 上线)的 path_evidence "救趋势" 阀门**实际失效**:它本意是当某标的真在走上行、却被 1h `direction` 误标为 `neutral`、组合体制误判成 choppy 时,用客观动量把它救回放行。但该阀门是**双重失效**——

1. `_compute_directional_evidence` 里 `path_evidence_raw` 硬要求 `sym_dir == 'bullish'`;
2. 更隐蔽:它同时要求 `trend.strength >= 60`,而 `tech_analyst.py:192-200` 的 strength 计算决定了 `direction == 'neutral'` 的标的 strength **结构上封顶 ~50**(neutral 拿不到多周期共振 +20),**永远到不了 60**。`strength >= 60` 本身就是 `sym_dir == 'bullish'` 的隐式代理。

结果:上线以来 20 次 flat-gate 拦截**全部** `direction == 'neutral'`,救援阀门一次都没触发(决策磁带实证)。"救回被误判趋势" 的设计意图从未兑现。

放回这类 neutral-方向多单,等价于给刚关上的 choppy 多单门重开一道有条件的缝,**风险是放回假突破**(实证 LAB 被拒后 +1.84% 又回落到 −0.62%,正是这种长相)。是救真趋势还是放假突破,**先验不可判,必须实测**。本项目惯例(cf lab / shadow logger / AB 驱动)是**先测量再改 live**——本 change 即测量步,不改门。

## What Changes

- **新增 observability-only 测量驱动** `cf_neutral_momentum_rescue_ab.py`(repo 根,镜像 `cf_choppy_neutral_tp1_floor_ab.py` / `cf_ev_decouple_ab.py` 结构):对决策磁带按"救援候选谓词"筛选历史决策,用 `resolve_counterfactual` + klines TP1 保守口径结算前向反事实 PnL,簇去重,`cf_honesty_gate`(min_sample=30 不下调)裁定。
  - **救援候选谓词**:`effective_regime ∈ {choppy, mixed}` AND `trend.direction == 'neutral'` AND `(daily_bias=='bullish' OR higher_tf_bias=='bullish')` AND `pre_12h_return_pct >= 阈值` AND `position_in_24h_range <= 阈值`。这些信号**全部方向无关**,不依赖被误标的 1h direction,也不用 `strength`(它是方向代理)。
  - **对照设计**:被救援桶(满足谓词、当前被拒)vs 参照桶,统一 CF 结算口径比净 R/簇。
- **红线守卫**扩展 `tests/test_cf_red_line_guard.py`:决策/风控路径(judge/executor/portfolio_risk_guard/reviewer/position_analyst)禁 import 本驱动。
- **结论产物**:净 R 显著为正且诚实门通过 → 记录"救援有 edge",作为后续改门 change 的依据;为负或诚实门 INSUFFICIENT → 记录"阀门失效但本就不该救",结案。

### 明确不做(Non-goals)

- **不改** `Judge._classify_regime_flat_gate` / `_compute_directional_evidence` / `_has_directional_thesis` 任何逻辑。
- **不改** `_select_rr_floor` 的 `path_evidence_raw`(它被 floor-grant 路径消费,必须零回归)。
- **不碰** live、lever2、ev 解耦、短单门、TechAnalyst 的 direction/strength 标注。
- 不下单、不改 config。

## Capabilities

### New Capabilities
- `neutral-momentum-rescue-measurement`: observability-only 反事实测量能力——量化"被误标 neutral 方向但有客观上行动量"的多单候选在 choppy/mixed 体制下的前向反事实 PnL,以决定救援路径是否值得建。复用 `resolve_counterfactual` + `cf_honesty_gate`,红线禁决策/风控路径读取。

### Modified Capabilities
<!-- 无:本 change 不改任何现有 capability 的 spec-level 行为(不改 regime-flat-entry-gate 等门的需求) -->

## Impact

- **新增文件**:`cf_neutral_momentum_rescue_ab.py`(repo 根驱动);对应测试。
- **修改文件**:`tests/test_cf_red_line_guard.py`(加禁读断言)。
- **复用(只读)**:`utils/counterfactual_pnl.py::resolve_counterfactual`、`utils/cf_honesty_gate.py::summarize_bucket`、`data/decision_replay_tape.jsonl`、`data/klines_1s.db`。
- **零** live / Judge / executor / config 改动;observability-only,与 `cf-choppy-neutral-tp1-floor-ab` 同性质。
```

## openspec/changes/cf-neutral-momentum-rescue-ab/design.md

- Source: openspec/changes/cf-neutral-momentum-rescue-ab/design.md
- Lines: 1-57
- SHA256: 16eca0077481d9ea9e019b07702ebc0f502c34c09ba30eade0cd89d8fc5fbed7

```md
## Context

体制空仓硬门 `Judge._classify_regime_flat_gate`(2026-06-25)的 path_evidence 救援阀门设计意图是救回"被误标 neutral 但真在走上行"的趋势单,实际**双重失效**:

- `_compute_directional_evidence` 的 `path_evidence_raw` 硬要求 `sym_dir=='bullish'`;
- 同时要求 `trend.strength >= 60`,而 `tech_analyst.py:192-200` 决定 `direction=='neutral'` 的 strength 封顶 ~50(neutral 凑不齐三周期共振 +20)→ `strength>=60` 是 `bullish` 的隐式代理。

上线以来 20 次拦截全 `direction=='neutral'`,阀门从未触发。本 change 不修门,先**测量**"放回这类 neutral 多单"在 choppy/mixed 体制下的前向反事实期望,判定救援是否有 edge。属本项目 "先测量后改 live" 惯例的测量步,与 `cf-choppy-neutral-tp1-floor-ab` 同性质。

**约束**:observability-only;`tests/test_cf_red_line_guard.py` 守卫决策/风控路径禁 import CF/测量产物;复用既有 `resolve_counterfactual` + `cf_honesty_gate`;决策磁带 `decision_replay_tape.jsonl` + `klines_1s.db` 为只读数据源。

## Goals / Non-Goals

**Goals:**
- 新增 `cf_neutral_momentum_rescue_ab.py` 驱动,量化救援候选单的前向反事实净 R/簇,诚实门裁定。
- 用**方向无关**信号(daily/htf bias + 真实 12h 涨幅 + 区间位置)定义救援候选谓词,绕开被误标的 1h direction 与 strength 代理。
- 产出可信结论:救援是否有 edge → 决定是否起后续改门 change。

**Non-Goals:**
- 不改 `_classify_regime_flat_gate` / `_compute_directional_evidence` / `_has_directional_thesis`。
- 不改 `_select_rr_floor` 的 `path_evidence_raw`(floor-grant 消费,须零回归)。
- 不碰 live / lever2 / ev 解耦 / 短单门 / TechAnalyst direction·strength 标注。
- 不下单、不改 config、不自动改任何阈值。

## Decisions

**D1: 数据源用决策磁带 + resolve_counterfactual(而非 event_backtest 为主)。**
理由:磁带记录的是 live 真实决策时点的 tech 快照(direction/daily_bias/htf_bias/pre_12h_return_pct/position_in_24h_range 全有,已实证可读),保真度高于 event_backtest 的历史重算;且镜像现有 `cf_choppy_neutral_tp1_floor_ab.py` / `cf_ev_decouple_ab.py` 基础设施,复用 `resolve_counterfactual` + klines TP1 保守结算。event_backtest 作为样本不足时的交叉验证补充(design 阶段定)。
*备选*:event_backtest 为主——历史样本大但 regime/entry_context 保真低,且无法对齐 live 体制判定。否决为主、保留为辅。

**D2: 救援候选谓词全部方向无关。**
`effective_regime ∈ {choppy, mixed}` AND `direction=='neutral'` AND `(daily_bias=='bullish' OR higher_tf_bias=='bullish')` AND `pre_12h_return_pct >= 阈值` AND `position_in_24h_range <= 阈值`。
理由:阀门失效的根因正是依赖了 direction 与其代理 strength;救援证据必须建立在不受 1h direction 标签污染的客观信号上。`strength` 明确**不用**。
*备选*:沿用 strength>=60——已证明对 neutral 恒不触发,无意义。否决。

**D3: 阈值参数化、不写死、不调 live。**
`pre_12h_return_pct` / `position_in_24h_range` 阈值作驱动参数(可与 `path_evidence_raw` 现值 0.03 / 0.92 对齐作基准),报多组取值的敏感性,不挑一个塞回代码。

**D4: 诚实门 min_sample=30 不下调,薄样本 INSUFFICIENT 拒答。**
与所有 cf 驱动一致;suggestive 读数只记录方向、不作改门依据。

## Risks / Trade-offs

- [磁带样本不足,诚实门拒答] → 接受;记录 suggestive 方向 + 装周更 cron 累积(同 `cf-choppy-neutral-tp1-floor-ab`),或 event_backtest 交叉验证补样本(design 阶段评估)。
- [CF 结算口径乐观高估救援 edge] → 用 TP1 保守口径 + SL-first 同根冲突,与现有驱动一致;两桶同口径,系统性偏差在对比中抵消。
- [谓词选得太宽/太窄,结论失真] → 报谓词命中数 + 阈值敏感性多组,而非单点;命中数过低显式标注。
- [被误读为"已支持放回 neutral 多单"] → proposal/design/README 显式声明 observability-only、不改门;红线守卫测试机器拦截 import。
- [klines_1s 覆盖有限(近数日/数十标的)] → 无覆盖簇跳过并计数,与现有驱动一致。

## Migration Plan

无 live 部署。新增驱动 + 测试,`pytest` 全绿即可。运行驱动产出报告供人审。无回滚需求(不改任何运行时行为)。

## Open Questions

- 救援桶的"参照桶"取什么最有说服力:同体制下已被放行的 aligned 多单?还是"满足谓词但 pre12h 不达标"的近邻单?→ comet-design 阶段定。
- 是否需要配套 forward-shadow recorder(若磁带历史命中过少)→ 视 design 阶段命中数评估,默认先只跑磁带回放。
```

## openspec/changes/cf-neutral-momentum-rescue-ab/tasks.md

- Source: openspec/changes/cf-neutral-momentum-rescue-ab/tasks.md
- Lines: 1-29
- SHA256: e1d1f82858879c88bce2aafe716c05cb8dc93b44d61927dbe54e0f8090d89081

```md
# Tasks

## 1. 谓词与数据接入

- [ ] 1.1 在 `cf_neutral_momentum_rescue_ab.py` 实现救援候选谓词:`effective_regime ∈ {choppy, mixed}` AND `direction=='neutral'` AND `(daily_bias=='bullish' OR higher_tf_bias=='bullish')` AND `pre_12h_return_pct >= pre12h_min` AND `position_in_24h_range <= range_pos_max`(阈值参数化,基准对齐 0.03 / 0.92);谓词 MUST NOT 引用 `trend.strength`
- [ ] 1.2 从 `decision_replay_tape.jsonl` 读 `replayable` 记录,提取 tech 快照字段(direction/daily_bias/higher_tf_bias/pre_12h_return_pct/position_in_24h_range)+ regime;缺字段 fail-safe 跳过并计数

## 2. CF 结算与桶统计

- [ ] 2.1 救援候选用 `resolve_counterfactual` + `klines_1s.db` TP1 保守口径(SL-first)结算前向净 R;无覆盖跳过并计数
- [ ] 2.2 按 (symbol, side, 时间窗 >1h) 簇去重,与现有 cf 驱动一致
- [ ] 2.3 设参照桶(comet-design 定:同体制已放行 aligned 多单,或"谓词近邻但 pre12h 不达标"桶),两桶同口径比净 R/簇
- [ ] 2.4 报阈值敏感性(pre12h_min × range_pos_max 多组取值),非单点

## 3. 诚实门与结论

- [ ] 3.1 经 `cf_honesty_gate.summarize_bucket`(min_sample=30 不下调)裁定;n<30 输出 INSUFFICIENT_SAMPLE,仅 suggestive
- [ ] 3.2 报告输出:谓词命中数、各桶簇数/净 R、诚实门裁定、阈值敏感表;显式声明"救援有 edge → 起后续改门 change / 为负或 INSUFFICIENT → 结案"

## 4. 红线守卫与测试

- [ ] 4.1 `tests/test_cf_red_line_guard.py` 加断言:judge/executor/portfolio_risk_guard/reviewer/position_analyst 禁 import `cf_neutral_momentum_rescue_ab`
- [ ] 4.2 驱动单元测试:谓词命中/不命中(bearish 不救、趋势体制不进桶)、无覆盖跳过、诚实门薄样本拒答、CF 结算契约传 `entry_price`/`created_at`/`side`/`stop_loss`/`take_profit`(非 `entry_ref`)
- [ ] 4.3 全量 `pytest -q` 绿(基线 1460 + 新增)

## 5. 运行与归档

- [ ] 5.1 真跑驱动产出报告,记录结论(净 R、诚实门、是否值得改门)
- [ ] 5.2 验证:`python3 -m compileall` + 驱动测试通过;确认零 live/Judge/config 改动
```

## openspec/changes/cf-neutral-momentum-rescue-ab/specs/neutral-momentum-rescue-measurement/spec.md

- Source: openspec/changes/cf-neutral-momentum-rescue-ab/specs/neutral-momentum-rescue-measurement/spec.md
- Lines: 1-87
- SHA256: 6e831b12578d51db275971e66eae071ca6a61c075e79042d50b653cf00e9b8c6

[TRUNCATED]

```md
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

```

Full source: openspec/changes/cf-neutral-momentum-rescue-ab/specs/neutral-momentum-rescue-measurement/spec.md

