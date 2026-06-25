# Comet Design Handoff

- Change: fix-open-direction-regression-choppy-flat-gate
- Phase: design
- Mode: compact
- Context hash: 306a3433f50bc5c605285c59f70fa1fa4026cf870ce712eba88212e632e7d007

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/fix-open-direction-regression-choppy-flat-gate/proposal.md

- Source: openspec/changes/fix-open-direction-regression-choppy-flat-gate/proposal.md
- Lines: 1-40
- SHA256: 8c0813c10c299218dc324a5421e609b386f33eb63572a3f88267d113e8a76740

```md
## Why

**开仓方向质量出现回归。** 方向质量时间线分析（止损无关，48h MFE/MAE，源 `live_position_lifecycle.json` 69 笔真实开仓 × klines）：

| 期间 | n | 方向对% | 终点收益中位 |
|---|---|---|---|
| 改前 <06-17 | 42 | **60%** | +0.79% |
| 改后 ≥06-20 | 7 | **0%** | −6.78% |

按体制拆开,病根清晰:**趋势单方向一直对(80%/+16.9%),choppy/mixed/neutral 单方向一直错(15%→0%/−6.8%)**——两者跨期不变。真正变的是**开仓结构**:`lever2 默认开`(06-17)+ `ev-胜率解耦`(06-18)把开仓从混合(趋势为主、choppy 占 31%)推成 **100% choppy/mixed/neutral**(趋势归零)。即系统从"choppy 市拒单空仓(对的)"变成"choppy 市照开然后亏"。边缘60(13/13 choppy+neutral)、cf-choppy-tp1-floor 是同一现象的不同切面。

## What Changes

- 新增**单点收口的「体制空仓硬门」** `Judge._classify_regime_flat_gate`：当 `effective_regime ∈ {choppy, mixed}` 且**无方向论据**时,拒绝 open（reason=`regime_flat_no_thesis`）。
- **方向论据**复用 `_select_rr_floor` 现有判定(防误伤被误判成 choppy 的趋势)：`aligned`(日线/HTF bias 与方向一致) OR `path_evidence`(bias 漏报时客观证据:trend.strength≥阈值 + pre_12h_return≥阈值 + range_pos≤阈值)。
- 硬门在**主开仓路径 + 三条 deferred 路径(15m/pullback/chase)**统一调用(单点收口,不在调用点重写)。
- config `regime_flat_gate_enabled` 默认 **True**(这是 fix)；env `REGIME_FLAT_GATE_ENABLED=false` 即时回滚。
- attribution 加 `regime_flat_gate`/`regime_flat_decision`/`has_directional_thesis`/`regime_flat_reason`,accept/reject 双路径都写。
- 同步 `event_backtest.py`(红线:改开仓门必须同步事件回测)。

**不做**(代码核查证明是钝器,会误伤趋势单):
- ❌ 不回滚 ev-decouple:`_get_p_win` 恢复后用**全局** `_recent_win_rate`(衰减期~25%)不分体制 → 把趋势单也拦死。
- ❌ 不回滚 lever2:阶梯口径是给趋势单抬地板的(那批 80% 好单);choppy+无方向已被硬门拒,无需动 lever2。

## Capabilities

### New Capabilities
- `regime-flat-entry-gate`: 体制空仓硬门——choppy/mixed 体制且无方向论据(非 aligned 非 path_evidence)时拒开仓,单点收口、主+deferred 路径共用、config 可回滚、event_backtest 同步、attribution 透传。

### Modified Capabilities
<!-- 不改 regime-aware-long-entry-guard(那是 overheat range_pos 阈值,正交);不改 open-gate-ev(不回滚解耦)。 -->

## Impact

- **修改**:`agents/trading/judge.py`(新增 `_classify_regime_flat_gate` + 主/deferred 路径调用 + attribution)、`utils/config_loader.py`(DEFAULTS `regime_flat_gate_enabled` + env)、`event_backtest.py`(同步硬门)、`config.yaml`(显式 true)。
- **改 live 开仓决策**:choppy/mixed + 无方向论据将不再开仓(预期:回到趋势单为主、choppy 空仓)。需手动 OS 重启 live 加载。
- **可逆**:env `REGIME_FLAT_GATE_ENABLED=false` 一键回滚。
- **测试**:`_classify_regime_flat_gate` 单测(choppy+neutral 拒/choppy+path_evidence 放行/trend 放行/mixed/short 方向)+ event_backtest 同步 + attribution 四字段 accept&reject 写入。
- **风险**:若 path_evidence 阈值过严会误伤真趋势(用现有 lever1 阈值,已调过);若 regime 分类系统性误判,硬门可 env 秒回。
- **样本警告**:方向分析改后桶 n=14(薄)、24 单 regime 未 join——方向强、统计样本薄;硬门默认开但保留即时回滚,前向观察方向对%是否回升。
```

## openspec/changes/fix-open-direction-regression-choppy-flat-gate/design.md

- Source: openspec/changes/fix-open-direction-regression-choppy-flat-gate/design.md
- Lines: 1-59
- SHA256: a5d1d6f6e124d5dffdb8ca0980ec40d401aeeac856edfad2a81022fcfb66353e

```md
## Context

方向质量回归(改前 60% → 改后 0%)。病根=开仓结构被 lever2(06-17)+ev-decouple(06-18)推成 100% choppy/mixed/neutral。趋势单方向一直对(80%)、choppy 一直错(0-15%)——edge 在趋势单,不是脑子坏。修法=精准砍掉"choppy+无方向"开仓,保留趋势单(含被 regime 误判成 choppy 的)。

代码地基(已探明):
- `_select_rr_floor`(judge.py:2541+) 已有"方向论据"判定:`aligned`(`sym_dir=='bullish'` 与 daily/HTF bias 一致)与 `path_evidence`(bias 漏报时:`trend.strength>=_path_evidence_min_strength(60)` + `pre_12h_return_pct>=_path_evidence_min_pre12h_return(0.03)` + `position_in_24h_range<=_path_evidence_max_range_pos(0.92)`)。**复用它做硬门的"放行"条件。**
- regime 取 `self._regime_manager.snapshot()['effective_regime']`(与 `_apply_regime_policy`/`_resolve_long_range_thresholds` 同源)。
- 现有开仓门单点收口先例:`_classify_short_entry_risk`/`_check_entry_position_policy`/`_select_rr_floor`,主+deferred 路径共用。

## Goals / Non-Goals

**Goals:**
- 精准砍掉 choppy/mixed + 无方向论据的 open(那批 0% 方向对),保留趋势单与 path-evidence 救回的趋势。
- 单点收口 + 主/deferred 路径共用 + attribution + event_backtest + config 可逆。
- 不误伤趋势单。

**Non-Goals:**
- 不回滚 ev-decouple(钝器:全局衰减胜率会拦死趋势单)。
- 不回滚 lever2(服务趋势单的口径修正;choppy+无方向已被硬门拒)。
- 不改 regime 分类器本身(误判由 path_evidence 兜底)。
- 不改 overheat 阈值(regime-aware-long-entry-guard 正交,各管各)。

## Decisions

1. **新单点收口 `Judge._classify_regime_flat_gate(action, plan, tech) -> (allow: bool, reason: str)`**:
   - 仅对 open(open_long/open_short)生效;非 open 直接 allow。
   - `eff_regime = regime_manager.snapshot()['effective_regime']`;若 `eff_regime not in {choppy, mixed}` → allow(趋势体制本来就好)。
   - choppy/mixed 时:`has_thesis = _has_directional_thesis(action, plan, tech)`;`has_thesis` → allow,否则 reject(reason=`regime_flat_no_thesis`)。
   - `regime_flat_gate_enabled=False` → 永远 allow(回滚)。
2. **long-only(已定)**:本门只作用 `open_long`,`open_short` 直接放行——做空已由 `_classify_short_entry_risk` 上游强制看跌论据(choppy 里无看跌论据的 short 走不到本门),long-only 避免双重门、不重写短单逻辑;证据也是 long 主导。
3. **`_has_directional_thesis(plan, tech)`**(long)复用 `_select_rr_floor` 的 `aligned`(sym bias bullish) OR `path_evidence`(bullish 客观证据,三阈值禁前视)判定,提取共享 helper,与 `_select_rr_floor` 同调避免漂移。
4. **体制范围(已定)**:choppy AND mixed 都拦(坏单是 choppy/mixed/neutral 一锅),path_evidence/aligned 救回两体制里真有方向的。
3. **调用点**:主开仓路径 + 15m/pullback/chase 三 deferred,与其它门并列(单点收口,不在调用点重写)。
4. **attribution**:`regime_flat_gate`(版本)/`regime_flat_decision`(allow/reject)/`has_directional_thesis`(bool)/`regime_flat_reason`,经 `_build_attribution` + `_rejection_attribution` 双写。
5. **event_backtest 同步**:在 event_backtest 的开仓判定加同构硬门(红线)。
6. **config**:`regime_flat_gate_enabled` 默认 True;env `REGIME_FLAT_GATE_ENABLED`。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| path_evidence 阈值过严 → 误伤真趋势 | 用现有 lever1 阈值(已调过);env 秒回 |
| regime 系统性误判 choppy | path_evidence 兜底 + env 回滚 + 前向观察方向对% |
| short 的 thesis 定义与短单门重复 | 设计阶段定:short 复用 `_classify_short_entry_risk` 已有结构判定,不重写 |
| 开仓频率骤降(衰减期趋势 setup 本就少) | 这是预期/正确行为("choppy 空仓");用 attribution 监控被拒分布 |
| 样本薄(改后 n=14) | 默认开但可逆,前向看方向对%回升;非一次性赌 |

## 数据流

```
open 候选 → ... 现有门(R:R/EV/short-gate/overheat) ...
            → _classify_regime_flat_gate:
                 eff_regime ∈ {choppy,mixed}?
                   ├─ 否(趋势体制) → allow
                   └─ 是 → has_directional_thesis(aligned OR path_evidence)?
                            ├─ 有 → allow(被误判的趋势,救回)
                            └─ 无 → reject regime_flat_no_thesis  ← 砍 0%方向对的 choppy+neutral
            → trade_decision.v2 open / 拒单 attribution
```
```

## openspec/changes/fix-open-direction-regression-choppy-flat-gate/tasks.md

- Source: openspec/changes/fix-open-direction-regression-choppy-flat-gate/tasks.md
- Lines: 1-37
- SHA256: bed52323f8e7a16a2c44e9408e65e622c50385a4e8c6f162ed9a5ce2d3eefe95

```md
# Tasks: fix-open-direction-regression-choppy-flat-gate

## 1. config 开关

- [ ] 1.1 `utils/config_loader.py`:DEFAULTS 加 `regime_flat_gate_enabled: True` + env `REGIME_FLAT_GATE_ENABLED` 覆盖;`config.yaml` 显式 `regime_flat_gate_enabled: true`。Judge `__init__` 读 `self._regime_flat_gate_enabled`(getattr 容错 True)。

## 2. 方向论据判定(复用 _select_rr_floor)

- [ ] 2.1 提取/共享 `_has_directional_thesis(action, plan, tech) -> bool`:long = `aligned`(sym daily/HTF bias bullish) OR `path_evidence`(strength≥`_path_evidence_min_strength` + pre_12h≥`_path_evidence_min_pre12h_return` + range_pos≤`_path_evidence_max_range_pos`,禁前视);short = 复用 `_classify_short_entry_risk` 的结构性 daily_bearish/bias 判定(不重写短单逻辑)。与 `_select_rr_floor` 同源,避免两份判定漂移。

## 3. 体制空仓硬门(单点收口)

- [ ] 3.1 `Judge._classify_regime_flat_gate(action, plan, tech) -> (allow:bool, reason:str)`:非 open→allow;`regime_flat_gate_enabled=False`→allow;`eff_regime=regime_manager.snapshot()['effective_regime']` not in {choppy,mixed}→allow;否则 `has_thesis=_has_directional_thesis(...)`,allow iff has_thesis,else (False,'regime_flat_no_thesis')。
- [ ] 3.2 主开仓路径接入硬门(拒则不 open,走拒单 attribution)。
- [ ] 3.3 三条 deferred 路径(15m/pullback/chase)接入同一硬门(单点收口,不重写)。

## 4. attribution

- [ ] 4.1 `_build_attribution` + `_rejection_attribution` 双写 `regime_flat_gate`/`regime_flat_decision`/`has_directional_thesis`/`regime_flat_reason`。

## 5. event_backtest 同步

- [ ] 5.1 `event_backtest.py` 开仓判定加同构硬门(同 regime + thesis 判定),回测/live 一致。

## 6. 测试

- [ ] 6.1 `_classify_regime_flat_gate` 单测:choppy+neutral 拒 / choppy+path_evidence 放行 / bullish 体制放行 / mixed+无论据拒 / `regime_flat_gate_enabled=False` 放行 / 非 open 放行。
- [ ] 6.2 `_has_directional_thesis` 单测:aligned 真、path_evidence 三阈值边界、short 复用短单门。
- [ ] 6.3 deferred 三路径都调用硬门的不变量测试(防漏接)。
- [ ] 6.4 attribution 四字段 accept&reject 双路径写入测试。
- [ ] 6.5 event_backtest 同构硬门测试(choppy+无论据回测被拒)。
- [ ] 6.6 全量 `python3 -m pytest -q` 绿(基线 1437 + 新测试);compileall 通过。

## 7. 收尾

- [ ] 7.1 真跑/同构验证:确认硬门拒 choppy+neutral、放行 trend/path_evidence;attribution 正确。结论入 verify 报告。
- [ ] 7.2 **改 live 需用户手动 OS 重启加载**;env `REGIME_FLAT_GATE_ENABLED=false` 可即时回滚。前向观察方向对%是否回升 + 被拒分布。不改 config 其它项、不动 ev/lever2。
```

## openspec/changes/fix-open-direction-regression-choppy-flat-gate/specs/regime-flat-entry-gate/spec.md

- Source: openspec/changes/fix-open-direction-regression-choppy-flat-gate/specs/regime-flat-entry-gate/spec.md
- Lines: 1-46
- SHA256: 3bd4ce9adb59d755732fe074813dbc715ad754a1798d919b11b40bdbda2e4492

```md
## ADDED Requirements

### Requirement: 体制空仓硬门(choppy/mixed + 无方向论据则拒开仓)
系统 SHALL 提供单点收口的体制空仓硬门 `Judge._classify_regime_flat_gate`,**仅作用于 `open_long`**(`open_short` 直接放行——做空已由 `_classify_short_entry_risk` 上游强制看跌论据,choppy 里无看跌论据的 short 走不到本门;本门 long-only 避免双重门),当 `effective_regime ∈ {choppy, mixed}` 且 long 候选**无方向论据**时拒绝 open(reason=`regime_flat_no_thesis`)。**方向论据** MUST 复用 `_select_rr_floor` 的 long 判定:`aligned`(symbol daily/HTF bias bullish 一致) OR `path_evidence`(bias 漏报时的客观证据:trend.strength + pre_12h_return + 24h range_pos 三阈值,禁前视)。趋势体制(bullish/bearish)直接放行。该门 MUST 由主开仓路径与三条 deferred 路径(15m/pullback/chase)统一调用,不在调用点重写。`regime_flat_gate_enabled` 默认 True,`False` 时永远放行(回滚)。

#### Scenario: choppy+无方向论据 → 拒
- **WHEN** effective_regime=choppy(或 mixed) 且开仓候选 NOT aligned 且 NOT path_evidence
- **THEN** 拒绝 open,reason=`regime_flat_no_thesis`

#### Scenario: choppy+有 path_evidence(被误判的趋势) → 放行
- **WHEN** effective_regime=choppy 但满足 path_evidence(strength/pre_12h/range_pos 三阈值)
- **THEN** 放行(不误伤被 regime 误判成 choppy 的趋势)

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
```

