## Why

`daily-pattern-edge-lab` + `fix-fetch-subdaily-backward-pagination` 已确认 1 个信号:`Bearish Engulfing | 低 range_pos | 跌势`(日线 +0.326R / 4h 时间对齐 +0.208R 跨周期确认)。但确认基于历史回测(单一宏观周期),**确认 ≠ 实盘保证盈利**——上线前必须前向影子验证(用真实新数据、record-only,看 edge 是否延续)。

现有 `utils/shadow_decision_logger.py` 不适配:它硬接 Judge 1h 决策管线做 lever1/lever2 复盘对比,挂形态信号既属误用、又会让 live 决策路径 import 形态模块从而**违反红线** `test_decision_paths_do_not_read_pattern_research`。需建一个**独立、日线原生、observability-only** 的前向影子记录器。

## What Changes

- **新建 `pattern_forward_shadow.py`**:每日运行一次——拉最新日线(复用 `fetch_historical_klines`)→ 在每个 symbol 的最新已闭合日线 bar 上检测确认信号(`Bearish Engulfing` 且 `context==low|down`,复用 `candlestick_patterns` + `cf_pattern_edge_discovery` 的 context/ATR)→ would-be 信号(entry=收盘、ATR SL 1.5×/TP 3.0×、max 10 日、context、检测日 UTC)**write-only** 追加 `data/pattern_forward_shadow.jsonl`(幂等:同 symbol+date 不重复)。
- **结算/报告**(同文件 `--settle` 子命令):读日志,对**成熟**信号(检测日 ≥10 日前)用已实现日线价经 `resolve_counterfactual` 结算,报滚动前向净 R + `cf_honesty_gate` 诚实门。
- **`tests/test_cf_red_line_guard.py`** 守卫扩展:决策/风控路径禁 import `pattern_forward_shadow`。
- **文档化每日 cron 调用**(README/runbook 注记)。

**非破坏**:纯新增孤立脚本 + 守卫扩展;不碰 live 决策/config/状态文件。

## Capabilities

### New Capabilities
- `pattern-forward-shadow`: 对已确认日线形态信号做 record-only 前向影子记录 + 成熟后结算,量化真实前向 edge 是否延续;observability-only,绝不接入 live 决策。

### Modified Capabilities
<!-- 无 -->

## Impact

- 新增 `pattern_forward_shadow.py` + delta spec;改 `tests/test_cf_red_line_guard.py`(守卫扩展)。
- 数据:write-only `data/pattern_forward_shadow.jsonl`(幂等)。
- 复用 `candlestick_patterns` / `cf_pattern_edge_discovery`(context/atr/settle)/ `counterfactual_pnl` / `cf_honesty_gate` / `fetch_historical_klines`。
- 红线:决策/风控路径禁读,守卫扩展覆盖。
- **时间属性**:前向验证须数周累积(新日线 + 10 日持仓成熟),本 change 仅建"开始记录"基建。
