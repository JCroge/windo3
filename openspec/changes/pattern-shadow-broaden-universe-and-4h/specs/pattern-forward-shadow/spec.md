## MODIFIED Requirements

### Requirement: 确认信号前向记录(record-only,防前视)
系统 SHALL 提供 **interval 参数化（`--interval ∈ {1d, 4h}`，默认 1d）** 的记录器,在每个 symbol 的**最新已闭合 `<interval>` bar** 上检测确认信号(`Bearish Engulfing` 且 context=`low|down`),命中则以该 bar 收盘为 entry、ATR(1.5×SL/3.0×TP/10日时间口径) 构造 would-be 信号并 write-only 追加到 **按 interval 分离的 jsonl**（1d→`data/pattern_forward_shadow.jsonl`，4h→`data/pattern_forward_shadow_4h.jsonl`）;MUST NOT 在未闭合 bar 上记录(防前视)。上下文/退出时窗经 `set_interval_windows(interval)` 按 `BARS_PER_DAY` 换算成 bar 数,使不同周期时间对齐;检测/退出阈值(形态库、ATR 1.5×/3.0×、10 日成熟)冻结不随 interval 变。

#### Scenario: 命中确认信号则记录
- **WHEN** 某 symbol 最新已闭合 `<interval>` bar 命中 Bearish Engulfing 且 context=low|down
- **THEN** 追加一条 `{detect_date_utc,detect_bar_open_time,symbol,pattern,direction,context,entry,atr,stop_loss,take_profit,max_hold_days,interval,settled:false}` 到该 interval 对应的 jsonl（含检测 bar 的 `open_time` 作为 bar 身份）

#### Scenario: 非确认信号不记录
- **WHEN** 命中其它形态或 context≠low|down
- **THEN** 不写入(本期只前向验证已确认的 1 信号)

#### Scenario: 幂等(按 bar 身份去重)
- **WHEN** 重复运行记录器
- **THEN** 去重键为 `(symbol, detect_bar_open_time, interval)`——日线一日一 bar 等价于按日去重；4h 同一 UTC 日的多根 4h bar 各自独立记录、不互相覆盖（不可用 `(symbol, detect_date_utc)` 否则 4h 同日多信号塌缩）

#### Scenario: interval 分离
- **WHEN** 以 `--interval 4h` 运行
- **THEN** 只读 4h bar、只写 `pattern_forward_shadow_4h.jsonl`,绝不污染日线 jsonl;1d 与 4h 记录/结算互不混

### Requirement: 成熟信号 settle-when-determinable 结算与诚实报告
系统 SHALL 提供 **per-interval** 结算子命令,按 **outcome-determinable** 而非固定日历天数结算未结算记录:取检测 bar 之后的**已闭合 `<interval>` bar**,窗口上限 = `max_hold_days × bars_per_day(interval)` 个 bar（1d→10、4h→60，= 回测 `set_interval_windows` 口径，时间均为 10 日）。结算规则——(a) 窗口内出现 ATR SL/TP 退出（同根 SL-first 保守）→ 立即结算 `outcome∈{sl,tp}`；(b) 无退出且**已凑满整窗已闭合 bar** → 结算 `outcome=expired`；(c) 无退出且窗口未满 → **保持未结算**（不提前判 expired）。结算经 `resolve_counterfactual`/同口径出净 R 回写 `settled/net_r/outcome`,并报滚动 n / 胜率 / 均净 R + `cf_honesty_gate` 诚实门(薄样本拒答);1d 与 4h 各自独立滚动报告、独立诚实门裁定。结算只用检测 bar 之后的已闭合 bar（无前视），净 R 数值与等满 10 日再算**完全一致**——仅 `settled:true` 的时点提前。

#### Scenario: 早退出立即结算（4h 快的来源）
- **WHEN** 一条记录在窗口内某已闭合 bar 触 ATR SL 或 TP
- **THEN** 立即结算该 outcome（不等满 10 日）、回写 settled:true、纳入该 interval 滚动报告

#### Scenario: 无退出且整窗满 → expired
- **WHEN** 一条记录窗口内（`max_hold_days×bars_per_day` 个已闭合 bar）无 SL/TP 退出且整窗 bar 已齐
- **THEN** 结算 outcome=expired

#### Scenario: 窗口未满不提前判 expired
- **WHEN** 一条记录尚无退出且检测后已闭合 bar 数 < 整窗
- **THEN** 保持 settled:false，不结算（防提前判 expired）

#### Scenario: interval 窗口口径
- **WHEN** interval=4h
- **THEN** 窗口上限按 `10 日 × 6 bars/日 = 60` 个 4h bar（与日线 10 bar 同为 10 日时间口径）

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
