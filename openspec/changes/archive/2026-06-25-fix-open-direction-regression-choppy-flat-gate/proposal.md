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
