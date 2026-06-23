# Comet Design Handoff

- Change: pattern-forward-shadow-recorder
- Phase: design
- Mode: compact
- Context hash: c409c66589356e13d58337a54f84ef5b68a46adc6aefb8a74ba786f0408d108c

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/pattern-forward-shadow-recorder/proposal.md

- Source: openspec/changes/pattern-forward-shadow-recorder/proposal.md
- Lines: 1-30
- SHA256: 5b9e488aa08915c5ce95ddca7fb6142c7b145e7bd49ff6c2f755e1eff9b1494c

```md
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
```

## openspec/changes/pattern-forward-shadow-recorder/design.md

- Source: openspec/changes/pattern-forward-shadow-recorder/design.md
- Lines: 1-28
- SHA256: f05f7880fcb743244e479b04d9f23ff895308bcc2de87e1c6a16aed01d0a6b0a

```md
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
```

## openspec/changes/pattern-forward-shadow-recorder/tasks.md

- Source: openspec/changes/pattern-forward-shadow-recorder/tasks.md
- Lines: 1-19
- SHA256: 53efbbc6398ee1e5f76f5067850af0508eed897d7bf7260fa76d9885183fcefa

```md
# Tasks: pattern-forward-shadow-recorder

## 1. 记录器
- [ ] 1.1 新建 `pattern_forward_shadow.py`:`--record` 子命令,拉/读最新日线(复用 fetch 或直接读 klines.db),对每 symbol 最新已闭合 bar 检测 `Bearish Engulfing` 且 `cf_pattern_edge_discovery.context==low|down`
- [ ] 1.2 命中→构造 would-be 信号(entry=收盘,ATR via cf_pattern_edge_discovery.atr,SL 1.5×/TP 3.0×/10日),write-only 追加 `data/pattern_forward_shadow.jsonl`,幂等键 (symbol,detect_date_utc)
- [ ] 1.3 防前视:只用已闭合 bar(bars[-1] 为已收盘日);网络/数据缺失 fail-safe 跳过不崩

## 2. 结算器
- [ ] 2.1 `--settle` 子命令:读 jsonl,对 detect_date ≤ now-10d 且 settled:false 的,拉后续日线经 `resolve_counterfactual` 算净 R,回写 settled/net_r/outcome
- [ ] 2.2 滚动报告:n/胜率/均净R + `cf_honesty_gate.summarize_bucket` 诚实门(薄样本拒答)

## 3. 红线守卫 + 测试
- [ ] 3.1 `tests/test_cf_red_line_guard.py` 守卫扩展:决策/风控路径禁 import `pattern_forward_shadow`
- [ ] 3.2 单测 `tests/test_pattern_forward_shadow.py`:构造命中/不命中/幂等/防前视 + 结算回写,用合成日线(不依赖网络)
- [ ] 3.3 全量 pytest 无新回归

## 4. 调度文档 + 收尾
- [ ] 4.1 README/runbook 注记每日 cron:`python3 pattern_forward_shadow.py --record`(UTC 收盘后)+ 定期 `--settle`
- [ ] 4.2 record-only smoke:对现有 klines.db 跑 `--record` 验证写入 + 幂等;诚实汇报(前向样本需数周,当前仅起步)
```

## openspec/changes/pattern-forward-shadow-recorder/specs/pattern-forward-shadow/spec.md

- Source: openspec/changes/pattern-forward-shadow-recorder/specs/pattern-forward-shadow/spec.md
- Lines: 1-34
- SHA256: 18f7ec30b91eebb0e2ba8f1530e130c8e68a0657f9ef3483c4bd821e754a1c59

```md
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
```

