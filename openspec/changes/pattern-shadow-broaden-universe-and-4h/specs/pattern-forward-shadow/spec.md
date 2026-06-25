## MODIFIED Requirements

### Requirement: 确认信号前向记录(record-only,防前视)
系统 SHALL 提供 **interval 参数化（`--interval ∈ {1d, 4h}`，默认 1d）** 的记录器,在每个 symbol 的**最新已闭合 `<interval>` bar** 上检测确认信号(`Bearish Engulfing` 且 context=`low|down`),命中则以该 bar 收盘为 entry、ATR(1.5×SL/3.0×TP/10日时间口径) 构造 would-be 信号并 write-only 追加到 **按 interval 分离的 jsonl**（1d→`data/pattern_forward_shadow.jsonl`，4h→`data/pattern_forward_shadow_4h.jsonl`）;MUST NOT 在未闭合 bar 上记录(防前视)。上下文/退出时窗经 `set_interval_windows(interval)` 按 `BARS_PER_DAY` 换算成 bar 数,使不同周期时间对齐;检测/退出阈值(形态库、ATR 1.5×/3.0×、10 日成熟)冻结不随 interval 变。

#### Scenario: 命中确认信号则记录
- **WHEN** 某 symbol 最新已闭合 `<interval>` bar 命中 Bearish Engulfing 且 context=low|down
- **THEN** 追加一条 `{detect_date_utc,symbol,pattern,direction,context,entry,atr,stop_loss,take_profit,max_hold_days,settled:false}` 到该 interval 对应的 jsonl

#### Scenario: 非确认信号不记录
- **WHEN** 命中其它形态或 context≠low|down
- **THEN** 不写入(本期只前向验证已确认的 1 信号)

#### Scenario: 幂等
- **WHEN** 同 bar 对同 symbol 重复运行记录器
- **THEN** 同 (symbol, detect_date_utc, interval) 不重复追加

#### Scenario: interval 分离
- **WHEN** 以 `--interval 4h` 运行
- **THEN** 只读 4h bar、只写 `pattern_forward_shadow_4h.jsonl`,绝不污染日线 jsonl;1d 与 4h 记录/结算互不混

### Requirement: 成熟信号结算与诚实报告
系统 SHALL 提供 **per-interval** 结算子命令,对检测日 ≥10 日前且未结算的记录,用已实现 `<interval>` 价经 `resolve_counterfactual`(ATR SL/TP,同根 SL-first 保守,10 日 = `max_hold_sec` 时间口径与 interval 无关)结算净 R 并回写 `settled/net_r/outcome`,并报滚动 n / 胜率 / 均净 R + `cf_honesty_gate` 诚实门(薄样本拒答);1d 与 4h 各自独立滚动报告、独立诚实门裁定。

#### Scenario: 结算成熟信号
- **WHEN** 一条记录检测日已过 10 日且未结算
- **THEN** 结算其净 R、回写 settled:true,并纳入该 interval 的滚动报告

#### Scenario: 薄样本诚实拒答
- **WHEN** 某 interval 已结算样本数低于诚实门阈值(n<30)
- **THEN** 该 interval 报告标 INSUFFICIENT_SAMPLE,不下前向 edge 结论(1d/4h 互不借样本)

## ADDED Requirements

### Requirement: 扩展且冻结的 symbol universe(前向=回测同人群)
系统 SHALL 使用一份**冻结的 ~100 binance USDT-spot symbol 快照**(构建期按 24h 成交量排序、排除稳定币与杠杆代币后取 top~100,固化成代码常量),作为 `fetch_historical_klines` 抓取、`cf_pattern_edge_discovery` 回测、前向 runner 记录的**同一 universe**。该列表 MUST NOT 每次运行动态 re-query(漂移会破坏前向与回测的可比性);刷新快照须另起 change。

#### Scenario: 前向与回测同人群
- **WHEN** 跑前向 runner 与 `cf_pattern_edge_discovery` 回测
- **THEN** 二者跑在完全相同的冻结 symbol 列表上,edge 数值口径可比

#### Scenario: universe 冻结
- **WHEN** 多次运行 record / fetch
- **THEN** symbol 集合不变(来自代码常量,非动态查询)

#### Scenario: 排除非交易标的
- **WHEN** 构建快照
- **THEN** 稳定币(USDC/FDUSD/TUSD/DAI 等)与杠杆代币(*UP/*DOWN/*BULL/*BEAR)被排除
