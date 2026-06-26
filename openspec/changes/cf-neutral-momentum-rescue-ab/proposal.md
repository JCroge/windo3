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
