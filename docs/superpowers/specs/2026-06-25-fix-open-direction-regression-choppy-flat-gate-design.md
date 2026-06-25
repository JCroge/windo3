---
comet_change: fix-open-direction-regression-choppy-flat-gate
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-25-fix-open-direction-regression-choppy-flat-gate
status: final
---

# Design: fix-open-direction-regression-choppy-flat-gate

> Canonical 需求源 = `openspec/changes/fix-open-direction-regression-choppy-flat-gate/specs/regime-flat-entry-gate/spec.md`。本文档只记技术实现/风险/测试。

## 1. 背景

方向质量回归(止损无关 48h MFE/MAE,lifecycle 69 笔):方向对% 改前 60%(<06-17)→ 改后 0%(≥06-20)。按体制:**趋势单 80%/+16.9%、choppy/mixed/neutral 15%→0%/−6.8%——跨期不变**;变的是开仓结构(lever2 06-17 + ev-decouple 06-18 推成 100% choppy/mixed/neutral)。修法=精准砍 choppy/mixed+无方向论据的 open,保留趋势单(含被 regime 误判成 choppy 的)。**不回滚 ev/lever2**(钝器:全局衰减胜率/砍趋势单口径修正)。

## 2. 代码地基(已探明)

- `_select_rr_floor`(judge.py:2541+):`aligned = sym_dir=='bullish'`(与 daily/HTF bias 一致);`path_evidence = sym_dir=='bullish' AND trend.strength>=_path_evidence_min_strength(60) AND ectx.pre_12h_return_pct>=_path_evidence_min_pre12h_return(0.03) AND ectx.position_in_24h_range<=_path_evidence_max_range_pos(0.92)`。**复用做本门放行条件。**
- regime:`self._regime_manager.snapshot()['effective_regime']`。
- 单点收口先例:`_classify_short_entry_risk`/`_check_entry_position_policy`,主+deferred 共用。
- `_get_p_win`(3786):`ev_winrate_gate_enabled=True` 用全局 `_recent_win_rate`(不分体制)——故不回滚(会拦趋势单)。

## 3. 关键决策

1. **新单点收口 `_classify_regime_flat_gate(action, plan, tech) -> (allow:bool, reason:str)`**(long-only):
   - `action != 'open_long'` → `(True,'')`(open_short 与非 open 都放行)。
   - `not regime_flat_gate_enabled` → `(True,'flag_off')`。
   - `eff = regime_manager.snapshot()['effective_regime']`;`eff not in {'choppy','mixed'}` → `(True,'regime_trend')`。
   - `has = _has_directional_thesis(plan, tech)`;`has` → `(True,'has_thesis')`,else `(False,'regime_flat_no_thesis')`。
2. **`_has_directional_thesis(plan, tech, score) -> bool`** = `aligned OR path_evidence_raw`:
   - ⚠️ **关键:path_evidence 必须 ungated**。`_select_rr_floor` 里 path_evidence 被 `_path_evidence_aligned_enabled`(lever1,默认 **OFF**)门控——若原样复用,thesis=aligned-only,会**重新砍掉 bias 漏报、被误判成 choppy 的趋势**(正是要保护的)。故提取共享 `_compute_directional_evidence(plan, tech, score) -> (aligned, path_evidence_raw)`,其中 `path_evidence_raw` = 三阈值客观判定**不含** lever1 flag。`_select_rr_floor` floor-grant 仍 `path_evidence = path_evidence_raw AND _path_evidence_aligned_enabled`(行为零变);flat gate thesis 用 `aligned OR path_evidence_raw`(ungated)。thesis 用法比 floor 用法更弱更安全(只阻止拒单、不授favorable RR),ungated 合理。
   - **前置验证(build)**:确认 `tech.entry_context`(pre_12h_return_pct/position_in_24h_range)在 live 决策中**无论 lever1 开关都被填充**;若否则 path_evidence_raw 恒 False、flat gate 退化为 aligned-only(可接受,verify 标注)。
3. **long-only**:open_short 由短单门上游处理,本门放行 short。
4. **choppy+mixed 都拦**;path_evidence/aligned 救回真趋势。
5. **调用点**:主开仓 + 15m/pullback/chase 三 deferred,与现有门并列(reject 走拒单 attribution)。
6. **attribution**:`regime_flat_gate`(版本字符串)/`regime_flat_decision`(allow|reject)/`has_directional_thesis`(bool)/`regime_flat_reason`,`_build_attribution`(放行)+`_rejection_attribution`(拒单)双写。
7. **event_backtest 同步**:event_backtest 开仓判定加同构门(同 regime + thesis;backtest 无组合级 regime 时取其 regime 近似,与现有 backtest regime 口径一致——设计阶段确认 backtest 是否有 effective_regime,无则用 raw/tech 近似并在 verify 标注差异)。
8. **config**:`config_loader.DEFAULTS['regime_flat_gate_enabled']=True` + env `REGIME_FLAT_GATE_ENABLED`;`config.yaml` 显式 true;Judge `__init__` `self._regime_flat_gate_enabled = config.get(...,True)`。

## 4. 风险 / 取舍

| 风险 | 缓解 |
|---|---|
| path_evidence 阈值过严误伤真趋势 | 用现有 lever1 阈值(已调过);env 秒回 |
| regime 系统性误判 choppy | path_evidence 兜底 + env 回滚 + 前向看方向对% |
| 开仓骤降(衰减期趋势 setup 少) | 预期/正确("choppy 空仓");attribution 监控被拒分布 |
| event_backtest 无 effective_regime | 设计阶段查;无则用 backtest regime 近似,verify 标注口径差异(non-regression 为主,意图由同构门满足) |
| 样本薄(改后 n=14) | 默认开但可逆;前向验证非一次性赌 |

## 5. 测试策略

- **`_classify_regime_flat_gate` 单测**:choppy+neutral(无论据)拒 / choppy+path_evidence 放行 / bullish 体制放行 / mixed+无论据拒 / open_short 放行(long-only) / 非 open 放行 / `regime_flat_gate_enabled=False` 放行。
- **`_has_directional_thesis` 单测**:aligned 真;path_evidence 三阈值各自边界(strength/pre12h/range_pos);与 `_select_rr_floor` 同源(改一处两处一致)。
- **deferred 三路径不变量**:主+15m+pullback+chase 都调用本门(防漏接)。
- **attribution**:四字段在 accept(放行 open)与 reject(拒单)双路径写入。
- **event_backtest 同构**:choppy+无论据在回测中同样被拒。
- **全量**:`pytest -q` 绿(基线 1437 + 新测试)+ compileall。
- **真跑/同构验证**:确认拒 choppy+neutral、放行 trend/path_evidence,结论入 verify。
