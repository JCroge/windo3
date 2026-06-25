# Tasks: fix-open-direction-regression-choppy-flat-gate

## 1. config 开关

- [x] 1.1 `utils/config_loader.py`:DEFAULTS 加 `regime_flat_gate_enabled: True` + env `REGIME_FLAT_GATE_ENABLED` 覆盖;`config.yaml` 显式 `regime_flat_gate_enabled: true`。Judge `__init__` 读 `self._regime_flat_gate_enabled`(getattr 容错 True)。

## 2. 方向论据判定(复用 _select_rr_floor)

- [x] 2.1 提取/共享 `_has_directional_thesis(action, plan, tech) -> bool`:long = `aligned`(sym daily/HTF bias bullish) OR `path_evidence`(strength≥`_path_evidence_min_strength` + pre_12h≥`_path_evidence_min_pre12h_return` + range_pos≤`_path_evidence_max_range_pos`,禁前视);short = 复用 `_classify_short_entry_risk` 的结构性 daily_bearish/bias 判定(不重写短单逻辑)。与 `_select_rr_floor` 同源,避免两份判定漂移。

## 3. 体制空仓硬门(单点收口)

- [x] 3.1 `Judge._classify_regime_flat_gate(action, plan, tech) -> (allow:bool, reason:str)`:非 open→allow;`regime_flat_gate_enabled=False`→allow;`eff_regime=regime_manager.snapshot()['effective_regime']` not in {choppy,mixed}→allow;否则 `has_thesis=_has_directional_thesis(...)`,allow iff has_thesis,else (False,'regime_flat_no_thesis')。
- [x] 3.2 主开仓路径接入硬门(拒则不 open,走拒单 attribution)。
- [x] 3.3 三条 deferred 路径(15m/pullback/chase)接入同一硬门(单点收口,不重写)。

## 4. attribution

- [x] 4.1 `_build_attribution` + `_rejection_attribution` 双写 `regime_flat_gate`/`regime_flat_decision`/`has_directional_thesis`/`regime_flat_reason`。

## 5. event_backtest 同步

- [x] 5.1 `event_backtest.py` 开仓判定加同构硬门(同 regime + thesis 判定),回测/live 一致。

## 6. 测试

- [x] 6.1 `_classify_regime_flat_gate` 单测:choppy+neutral 拒 / choppy+path_evidence 放行 / bullish 体制放行 / mixed+无论据拒 / `regime_flat_gate_enabled=False` 放行 / 非 open 放行。
- [x] 6.2 `_has_directional_thesis` 单测:aligned 真、path_evidence 三阈值边界、short 复用短单门。
- [x] 6.3 deferred 三路径都调用硬门的不变量测试(防漏接)。
- [x] 6.4 attribution 四字段 accept&reject 双路径写入测试。
- [x] 6.5 event_backtest 同构硬门测试(choppy+无论据回测被拒)。
- [x] 6.6 全量 `python3 -m pytest -q` 绿(基线 1437 + 新测试);compileall 通过。

## 7. 收尾

- [x] 7.1 真跑/同构验证:确认硬门拒 choppy+neutral、放行 trend/path_evidence;attribution 正确。结论入 verify 报告。
- [x] 7.2 **改 live 需用户手动 OS 重启加载**;env `REGIME_FLAT_GATE_ENABLED=false` 可即时回滚。前向观察方向对%是否回升 + 被拒分布。不改 config 其它项、不动 ev/lever2。
