---
comet_change: pattern-forward-shadow-recorder
role: technical-design
canonical_spec: openspec
---

# Design Doc: pattern-forward-shadow-recorder

技术 RFC。需求事实源 = OpenSpec delta spec `specs/pattern-forward-shadow/spec.md`,本文只定 HOW。

## Context

已确认信号 `Bearish Engulfing | 低 range_pos | 跌势`(日线 +0.326R / 4h 时间对齐 +0.208R)需前向验证(确认基于单一宏观周期回测,非实盘保证)。现有 `utils/shadow_decision_logger.py` 是 Judge 1h-决策耦合(lever1/lever2 复盘)+ 挂形态会破红线,不可用。建独立日线原生 record-only 记录器,最大化复用:`cf_pattern_edge_discovery`(`context`/`atr`/`settle`/`set_interval_windows`)、`candlestick_patterns.detect_patterns`、`counterfactual_pnl.resolve_counterfactual`、`cf_honesty_gate.summarize_bucket`、`fetch_historical_klines`。

## Goals / Non-Goals

**Goals**:record-only 前向记录确认信号 + 成熟后结算看 edge 是否延续;observability-only;复用最大化、零新依赖;幂等可每日安全重跑。
**Non-Goals**:不接入 live;不自动改 config/上实盘;不立即出结论(须数周);本期只记确认的 1 信号(不重开搜索)。

## Decisions

### D1 触发与防前视
每日运行 `--record`;遍历 klines.db 各 symbol 的日线,只在**最新已闭合 bar**(运行时点当日 bar 未闭合 → 取 `bars[-1]` 为最近已收盘日)检测,entry=该 bar 收盘。MUST NOT 用未闭合 bar。*备选:实时 tick 触发 — 否决,日线信号无此需要且引入前视风险。*

### D2 信号判据(只记已确认)
命中 `("Bearish Engulfing", -1)` **且** `cf_pattern_edge_discovery.context(bars, last_idx) == "low|down"`(复用其 context:trailing 20 日 range_pos<0.25 且 close<MA50)。其它形态/上下文不记——本期前向验证范围严格限定为已通过 OOS+FDR+4h 确认的那 1 信号。*备选:把 2 候选都记下对比 — 否决,Evening Star 已被 4h 否决,记它只增噪;聚焦确认信号。*

### D3 记录 schema(jsonl write-only)
`{detect_date_utc, symbol, pattern, direction, context, entry, atr, stop_loss, take_profit, max_hold_days, settled:false}`。幂等键 `(symbol, detect_date_utc)`——追加前扫已有行去重。

### D4 结算
`--settle`:读 jsonl,对 `detect_date ≤ now-10d` 且 `settled==false` 的,从 klines.db 取该 symbol detect 日之后的日线,构造 cf record 经 `resolve_counterfactual`(SL/TP=记录值,max_hold_sec=10×86400,SL-first 保守)算净 R=net_usdt/(size×sl_dist),回写 `settled:true,net_r,outcome`。滚动报告:n / 胜率 / 均净 R / `summarize_bucket` 诚实门(薄样本 INSUFFICIENT_SAMPLE 拒答)。

### D5 复用而非重造
context/atr/settle 直接 `from cf_pattern_edge_discovery import context, atr, set_interval_windows`(调用前 `set_interval_windows("1d")`)。记录器只新增:取最新已闭合 bar、幂等追加、结算回写、报告。薄层。

### D6 红线
`pattern_forward_shadow` 绝不被 live 决策/风控(judge/executor/risk_guard/reviewer/position_analyst)import。守卫 `test_decision_paths_do_not_read_pattern_research` 的 forbidden 集加入 `pattern_forward_shadow`。记录器自身 import 研究模块无妨(它本就在研究侧)。

## Risks / Trade-offs

- [前向样本积累慢(数周)] → 接受,本质如此;幂等可每日安全重跑。
- [拉/读日线缺数据] → fail-safe 跳过该 symbol 不崩。
- [只记 1 信号样本稀] → 多 symbol 累积 + 诚实门拒答薄样本;不为凑数放宽判据。
- [幂等] → (symbol,date) 去重;结算只处理未结算项,可重复跑。

## Migration Plan

纯新增孤立脚本 + 守卫扩展。回滚 = 删 `pattern_forward_shadow.py` + `data/pattern_forward_shadow.jsonl`。不碰 live/config/状态。每日 cron 文档化(UTC 收盘后 `--record`,定期 `--settle`)。

## Open Questions

无(D1-D6 已覆盖)。前向结论须等数周累积——属运行期,非本 change 范围。
