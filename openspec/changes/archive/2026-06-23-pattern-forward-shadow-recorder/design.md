## Context

确认信号需前向验证(确认基于单一宏观周期回测,非实盘保证)。现有 shadow logger 是 Judge 1h-决策耦合 + 会破红线,不可用。需独立日线原生 record-only 记录器。复用已建:`candlestick_patterns.detect_patterns`、`cf_pattern_edge_discovery`(`context`/`atr`/`settle`/`set_interval_windows`)、`counterfactual_pnl.resolve_counterfactual`、`cf_honesty_gate.summarize_bucket`、`fetch_historical_klines`。

## Goals / Non-Goals

**Goals**:record-only 前向记录确认信号 + 成熟后结算,看 edge 是否延续;observability-only;复用最大化、零新依赖。
**Non-Goals**:不接入 live;不自动改 config/上实盘;不立即出结论(须数周);本期只记录确认的 1 信号(Bearish Engulfing|低位跌势),不重开搜索。

## Decisions

- **D1 触发与防前视**:每日运行,只在每 symbol 的**最新已闭合日线 bar**(`bars[-1]` 为已收盘日)上检测;entry=该 bar 收盘。不在未闭合当日 bar 上记录(防前视)。
- **D2 信号判据**:仅记录确认信号——`detect_patterns` 命中 `("Bearish Engulfing",-1)` **且** `context(bars,last)=="low|down"`(复用 cf_pattern_edge_discovery 的 context,日线窗 20/MA50)。其它形态本期不记(它们未通过确认)。
- **D3 记录 schema**(jsonl 一行):`{detect_date_utc, symbol, pattern, direction, context, entry, atr, stop_loss, take_profit, max_hold_days, settled:false}`。幂等键 `(symbol, detect_date_utc)`。
- **D4 结算**:`--settle` 读日志,对 `detect_date ≤ now-10d` 且未结算的,拉该 symbol 后续日线经 `resolve_counterfactual`(ATR SL/TP,SL-first 保守)算净 R,写回 `settled:true, net_r, outcome`。报滚动:n、胜率、均净 R、`cf_honesty_gate` 诚实门。
- **D5 复用而非重造**:context/atr/settle 逻辑直接 import `cf_pattern_edge_discovery`(它已 observability-only)。记录器只加"取最新 bar + 幂等追加 + 结算回写"薄层。
- **D6 红线**:`pattern_forward_shadow` 绝不被 live 决策/风控 import;守卫测试扩展。记录器自己 import 研究模块没问题(它本身是研究侧)。

## Risks / Trade-offs

- [前向样本积累慢] → 接受(本质如此);记录器幂等可每日安全重跑。
- [拉日线需网络] → 记录器失败 fail-safe(打印跳过,不崩);可手动补跑。
- [只记 1 信号样本稀] → 确认信号本就稀(低位看跌吞没);多 symbol 累积;诚实门拒答薄样本。
- [幂等] → 同 symbol+date 不重复追加;结算只跑未结算项。

## Migration Plan

纯新增孤立脚本 + 守卫扩展。回滚=删脚本 + 删 jsonl。不碰 live。cron 文档化(每日 UTC 收盘后跑 `python3 pattern_forward_shadow.py --record`,定期 `--settle`)。
