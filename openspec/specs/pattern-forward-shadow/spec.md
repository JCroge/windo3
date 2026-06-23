## ADDED Requirements

### Requirement: 确认信号前向记录(record-only,防前视)
系统 SHALL 提供每日运行的记录器,在每个 symbol 的**最新已闭合日线 bar** 上检测确认信号(`Bearish Engulfing` 且 context=`low|down`),命中则以该 bar 收盘为 entry、ATR(1.5×SL/3.0×TP/10日) 构造 would-be 信号并 write-only 追加到 `data/pattern_forward_shadow.jsonl`;MUST NOT 在未闭合 bar 上记录(防前视)。

#### Scenario: 命中确认信号则记录
- **WHEN** 某 symbol 最新已闭合日线命中 Bearish Engulfing 且 context=low|down
- **THEN** 追加一条 `{detect_date_utc,symbol,pattern,direction,context,entry,atr,stop_loss,take_profit,max_hold_days,settled:false}`

#### Scenario: 非确认信号不记录
- **WHEN** 命中其它形态或 context≠low|down
- **THEN** 不写入(本期只前向验证已确认的 1 信号)

#### Scenario: 幂等
- **WHEN** 同日对同 symbol 重复运行记录器
- **THEN** 同 (symbol, detect_date_utc) 不重复追加

### Requirement: 成熟信号结算与诚实报告
系统 SHALL 提供结算子命令,对检测日 ≥10 日前且未结算的记录,用已实现日线价经 `resolve_counterfactual`(ATR SL/TP,同根 SL-first 保守)结算净 R 并回写 `settled/net_r/outcome`,并报滚动 n / 胜率 / 均净 R + `cf_honesty_gate` 诚实门(薄样本拒答)。

#### Scenario: 结算成熟信号
- **WHEN** 一条记录检测日已过 10 日且未结算
- **THEN** 结算其净 R、回写 settled:true,并纳入滚动报告

#### Scenario: 薄样本诚实拒答
- **WHEN** 已结算样本数低于诚实门阈值
- **THEN** 报告标 INSUFFICIENT_SAMPLE,不下前向 edge 结论

### Requirement: Observability-only 红线
`pattern_forward_shadow` 及其产物 SHALL 为 observability-only;任何交易决策/风控路径(judge/executor/risk_guard/reviewer/position_analyst)MUST NOT import 它。

#### Scenario: 红线守卫
- **WHEN** 运行 `tests/test_cf_red_line_guard.py`
- **THEN** 存在断言验证决策/风控路径未 import `pattern_forward_shadow`,违反则失败
