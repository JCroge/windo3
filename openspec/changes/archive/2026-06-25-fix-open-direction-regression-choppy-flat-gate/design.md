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
