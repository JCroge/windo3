## ADDED Requirements

### Requirement: 体制空仓硬门(choppy/mixed + 无方向论据则拒开仓)
系统 SHALL 提供单点收口的体制空仓硬门 `Judge._classify_regime_flat_gate`,**仅作用于 `open_long`**(`open_short` 直接放行——做空已由 `_classify_short_entry_risk` 上游强制看跌论据,choppy 里无看跌论据的 short 走不到本门;本门 long-only 避免双重门),当 `effective_regime ∈ {choppy, mixed}` 且 long 候选**无方向论据**时拒绝 open(reason=`regime_flat_no_thesis`)。**方向论据** MUST 复用 `_select_rr_floor` 的 long 判定:`aligned`(symbol daily/HTF bias bullish 一致) OR `path_evidence`(bias 漏报时的客观证据:trend.strength + pre_12h_return + 24h range_pos 三阈值,禁前视)。趋势体制(bullish/bearish)直接放行。该门 MUST 由主开仓路径与三条 deferred 路径(15m/pullback/chase)统一调用,不在调用点重写。`regime_flat_gate_enabled` 默认 True,`False` 时永远放行(回滚)。

#### Scenario: choppy+无方向论据 → 拒
- **WHEN** effective_regime=choppy(或 mixed) 且开仓候选 NOT aligned 且 NOT path_evidence
- **THEN** 拒绝 open,reason=`regime_flat_no_thesis`

#### Scenario: choppy+有 path_evidence(被误判的趋势) → 放行
- **WHEN** effective_regime=choppy 但满足 path_evidence(strength/pre_12h/range_pos 三阈值)
- **THEN** 放行(不误伤被 regime 误判成 choppy 的趋势)

#### Scenario: path_evidence 用客观证据,不受 lever1 flag 门控
- **WHEN** 评估 thesis 的 path_evidence(`_path_evidence_aligned_enabled`/lever1 默认 OFF)
- **THEN** thesis 用 **ungated** 的三阈值客观判定(`path_evidence_raw`),即 lever1 关时 path_evidence 仍可成立 thesis;`_select_rr_floor` 的 floor-grant 用法仍受 lever1 门控不变(两用法解耦,防重新砍掉 bias 漏报的趋势)

#### Scenario: 趋势体制 → 放行
- **WHEN** effective_regime ∈ {bullish, bearish}
- **THEN** 硬门直接放行(不拦趋势体制)

#### Scenario: 回滚开关
- **WHEN** `regime_flat_gate_enabled=False`(或 env `REGIME_FLAT_GATE_ENABLED=false`)
- **THEN** 硬门永远放行,行为等同改动前

#### Scenario: 非开仓动作不受影响
- **WHEN** action 不是 open_long/open_short(如 close/reduce/add)
- **THEN** 硬门直接放行(只管开仓)

#### Scenario: open_short 不被本门拦(long-only)
- **WHEN** action=open_short(即便 effective_regime=choppy/mixed)
- **THEN** 本门直接放行——做空的看跌论据由 `_classify_short_entry_risk` 上游处理,本门只作用 open_long

### Requirement: 硬门 attribution 透传
系统 SHALL 在 accept 与 reject 两条路径都写入硬门归因字段 `regime_flat_gate`/`regime_flat_decision`/`has_directional_thesis`/`regime_flat_reason`,经 `_build_attribution` 与 `_rejection_attribution` 收口。

#### Scenario: 拒单带归因
- **WHEN** 硬门拒一单
- **THEN** 拒单 attribution 含 regime_flat_decision=reject、has_directional_thesis=false、regime_flat_reason=regime_flat_no_thesis

#### Scenario: 放行带归因
- **WHEN** 硬门放行一单
- **THEN** attribution 含 regime_flat_decision=allow、has_directional_thesis(真实值)

### Requirement: event_backtest 同步硬门
系统 SHALL 在 `event_backtest.py` 的开仓判定加入同构的体制空仓硬门,使回测与 live 开仓逻辑一致(改 Judge 开仓门必须同步事件回测的红线)。

#### Scenario: 回测含硬门
- **WHEN** 运行 event_backtest
- **THEN** choppy/mixed + 无方向论据的开仓在回测中同样被拒,与 live 一致
