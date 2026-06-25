# Comet Design Handoff

- Change: pattern-shadow-broaden-universe-and-4h
- Phase: design
- Mode: compact
- Context hash: b60847adf501081e8ec3eeeb9eb8aa24efa4053aa780c463859e164166c409dd

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/pattern-shadow-broaden-universe-and-4h/proposal.md

- Source: openspec/changes/pattern-shadow-broaden-universe-and-4h/proposal.md
- Lines: 1-28
- SHA256: 9aa18f91116c1b2a99d345f5f555e84c127b03a99c1481864b6cbba0976586d5

```md
## Why

形态前向影子当前只扫 **30 个币、仅日线**，3 天才出 5 条信号、5 条全未结算——要靠每日 record 攒**数周到数月**才到诚实门所需的 n≥30。两条合法加速（都不碰单条信号的真实成熟期、不引前视）：① **扩盘口** 30→~100 流动币（信号产出 ~3-4×）；② **加 4h 并行影子**（4h ~40h≈不到 2 天成熟，作更快的领先指标）。06-23 跨周期工作已把 `fetch_historical_klines`(`--intervals 1d,4h`) 与 `cf_pattern_edge_discovery`(`main(interval)` 含 `set_interval_windows`) 建成 interval 感知，**只剩前向 runner 是日线硬编码**，故本 change 复用度高。

## What Changes

- **扩展 universe 30→~100**：`fetch_historical_klines.py` 的 `DEFAULT_SYMBOLS` + runner `SYMBOLS` 改为 **binance USDT-spot 24h 成交量 top~100 的冻结快照**（排除稳定币 USDC/FDUSD/TUSD/DAI 等 + 杠杆代币 *UP/*DOWN/*BULL/*BEAR）。**冻结**（快照成常量、不每次 re-query），保证前向与回测跑在**完全相同的人群**上。
- **re-fetch + re-validate**：抓新增 ~70 币的 1d+4h 历史入 `data/klines.db`（现有 30 币 1d+4h 已在库），重跑 `cf_pattern_edge_discovery` 的 **1d 与 4h** 回测，确认 `Bearish Engulfing|低位跌势` edge 在宽 universe 仍过诚实门（前向/回测同口径可比）。
- **4h 并行影子**：给前向 runner 加 `--interval {1d,4h}` 参数（镜像回测的 `set_interval_windows`），4h 信号写独立 `data/pattern_forward_shadow_4h.jsonl`（与日线不混）；新增 launchd **4h record 每 4h / 4h settle 每日**；现有日线 record(09:17)/settle(周一 09:47) 自动覆盖扩展后的 universe。
- **不改**：检测/退出/诚实门逻辑（阈值、ATR 1.5×/3.0×、10 日成熟、Wilson/bootstrap、n<30 拒答）全部冻结不动；不接 live、不改 config.yaml。

## Capabilities

### Modified Capabilities
- `pattern-forward-shadow`: 记录器从「仅日线」泛化为 **interval 参数化（1d + 4h）**，记录/结算按 interval 在最新已闭合 bar 上进行、写按 interval 分离的 jsonl；symbol universe 扩为**冻结的 ~100 快照**（前向与回测共用同一人群）。

### New Capabilities
<!-- 无新 capability：4h 是既有 forward-shadow 能力的 interval 扩展，universe 是其参数。 -->

## Impact

- **修改**：`fetch_historical_klines.py`（DEFAULT_SYMBOLS 扩~100）、`scripts/fwdshadow_runner.py` + 部署副本 `~/Library/Application Support/cryptoarb-fwdshadow/fwdshadow_runner.py`（加 `--interval`、universe 同步）、`pattern_forward_shadow.py`（lab 版加 `--interval`）。
- **新增**：launchd plist `com.cryptoarb.pattern-forward-shadow.record4h` / `.settle4h`；`data/pattern_forward_shadow_4h.jsonl`（运行期产生）。
- **数据**：`data/klines.db` 增 ~70 币 × {1d,4h}（网络 fetch，binance；新上币历史不足按实抓）。
- **re-run（不改码）**：`cf_pattern_edge_discovery.py` 跑 1d+4h 出宽-universe edge 报告（结论入 verify）。
- **零 live 改动**：不碰 judge/executor/任何决策风控路径；红线守卫 `test_cf_red_line_guard` 不变（forbidden 集已含 `pattern_forward_shadow`）。
- **文档**：README §日线形态前向影子记录器（扩 universe + 4h + 冻结口径说明）。
- **已知边界**：4h margin 比日线薄（回测 4h +0.208R vs 日线 +0.326R），只作早期领先指标、置信度低于日线；诚实门 n<30 仍拒答；扩盘只加速样本累积、不缩短单条 10 日成熟期。
```

## openspec/changes/pattern-shadow-broaden-universe-and-4h/design.md

- Source: openspec/changes/pattern-shadow-broaden-universe-and-4h/design.md
- Lines: 1-64
- SHA256: 36ed26a3fcf89278641bb75ea008735418f0d5b4962c16b72af4e5004ff8d8e7

```md
## Context

形态前向影子（`pattern-forward-shadow`，2026-06-23）record-only 验证 `Bearish Engulfing|低位跌势` 日线 edge。当前瓶颈=信号稀（30 币 × 仅日线 × 严格低位跌势过滤 → 3 天 5 条），诚实门 n≥30 要数周/月。

已就绪（06-23 `fix-fetch-subdaily-backward-pagination` 跨周期工作）：
- `fetch_historical_klines.py`：`--intervals 1d,4h`、`--symbols`、按 `(symbol,interval,open_time)` 去重落 `klines.db`。
- `cf_pattern_edge_discovery.py`：`main(interval)` + `set_interval_windows(interval)`（按 `BARS_PER_DAY` 把天数窗口换成 bar 数，时窗时间对齐），`load(interval)`。
- `klines.db`：30 币的 1d(28229 行) + 4h(116061 行) 已在库。

仅缺：前向 runner（部署版 `fwdshadow_runner.py` 用 `ccxt.binance()` + `"1d"` 硬编码；repo lab 版 `pattern_forward_shadow.py` 读 `interval='1d'` 硬编码，但已 import 回测的 interval 感知 helper）。

## Goals / Non-Goals

**Goals:**
- universe 30→~100 冻结快照（前向=回测同人群），加速信号产出 ~3-4×。
- 前向 runner interval 参数化（1d + 4h），4h 作 ~2 天成熟的领先指标。
- re-fetch 新币 + 重跑 1d/4h 回测，确认 edge 在宽 universe 仍过诚实门。
- observability-only、冻结检测/退出/诚实门逻辑、不接 live、不改 config。

**Non-Goals:**
- 不缩短单条信号 10 日真实成熟期（不可能、也是前向验证的意义）。
- 不改检测阈值/ATR 倍数/诚实门口径。
- 不把 4h 信号当定论（margin 薄、置信度低于日线，只作早期信号）。
- 不接 live 决策、不改 config.yaml。

## Decisions

1. **universe 冻结快照，不动态 top-100**：构建期跑一次 binance USDT-spot 24h quoteVolume 排序、排除稳定币 + 杠杆代币、取 top~100，**把结果固化成代码常量**（`DEFAULT_SYMBOLS` + runner `SYMBOLS` 同一份）。理由：动态 universe 每次漂移 → 前向人群 ≠ 回测人群 ≠ 可比性破。冻结=可复现 + 前向/回测对齐。
2. **runner interval 参数化（单 runner 双配置）**：`fwdshadow_runner.py` 加 `--interval`（默认 1d），记录读 `interval` bar、`set_interval_windows(interval)`、4h 写 `pattern_forward_shadow_4h.jsonl`（1d/4h 文件分离，settle 各管各）。比"另起 4h runner 文件"少重复、口径一致。repo lab 版 `pattern_forward_shadow.py` 同步加 `--interval`。
3. **launchd 节奏**：4h record 每 4h（贴 4h 收盘 + 小偏移，不漏信号）、4h settle 每日（4h ~40h 成熟，一天内结算到）。日线 record/settle 不动（自动覆盖扩 universe）。
4. **re-validate 是 gate**：宽 universe 1d/4h 回测须 edge 仍过诚实门才算成功；若 4h edge 在宽 universe 翻负/不过门，如实记入 verify、4h 仅当探索不下结论（与日线分开裁定）。
5. **fetch 仅增量**：现有 30 币 1d+4h 已在库，只抓新增 ~70 币（去重幂等，重跑安全）。
6. **settle-when-determinable（brainstorming 关键细化，使 4h 真快）**：现 settle 硬等 10 日历日（`(now-detect).days<10 跳过`）→ 若仅参数化 interval，4h 信号即便 8h 触 SL/TP 也要等 10 日，B 的"~2 天"白费。改为 outcome-determinable：窗口内触 ATR SL/TP → 立即结算；无退出且整窗（`max_hold_days×bars_per_day` bar：1d→10/4h→60）已闭合 → expired；窗口未满 → 留未结算（不提前判 expired）。**净 R 值不变、无前视，仅 `settled:true` 提前**。日线随之早结算（同值），是严格改进。
7. **dedup 按 bar 身份**：现去重键 `(symbol,detect_date_utc)` 对 4h 同 UTC 日多 bar 塌缩 → 改 `(symbol,detect_bar_open_time,interval)`，record 带检测 bar `open_time`；日线一日一 bar 等价。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| 网络 fetch ~70 币 ×{1d,4h} 慢/限流 | 增量幂等抓取，失败 symbol 跳过+计数；分批；binance 限流退避 |
| 部分新币历史不足（新上币） | `fetch_historical_klines` 已有"短历史币标注"；回测 OOS 三分对短史币自然少计 |
| 4h edge 宽 universe 可能不如日线 | re-validate 分 interval 独立裁定；4h 只作领先信号、文档明示 margin 薄 |
| 冻结 universe 漏掉某天高量新币 | 接受（可比性优先于覆盖最新）；后续 change 可定期刷新快照 |
| 部署副本与 repo 源漂移 | runner 改动同步 repo 源 + `cp` 部署，README 记部署命令；逻辑单一来源 |
| binance symbol 不存在/下架 | fetch 与 record 都 try-except 跳过+计数，不中断 |

## 数据流

```
构建期一次性：binance ticker 24h vol → 排序+排除稳定/杠杆 → top~100 → 冻结成 DEFAULT_SYMBOLS/SYMBOLS(代码常量)
        │
        ▼
fetch_historical_klines --symbols <~100> --intervals 1d,4h → klines.db(增量去重)
        │
        ├── cf_pattern_edge_discovery main("1d") → 宽universe 日线 edge 报告(诚实门)
        ├── cf_pattern_edge_discovery main("4h") → 宽universe 4h edge 报告(诚实门)
        │        ↑ 二者须 edge 仍过门 = re-validate gate
        ▼
forward runner --interval 1d  (launchd 每日 09:17 record / 周一 settle)  → pattern_forward_shadow.jsonl
forward runner --interval 4h  (launchd 每4h record / 每日 settle)        → pattern_forward_shadow_4h.jsonl
        │
        ▼
各自 cf_honesty_gate(n<30 拒答) 滚动报告
```
```

## openspec/changes/pattern-shadow-broaden-universe-and-4h/tasks.md

- Source: openspec/changes/pattern-shadow-broaden-universe-and-4h/tasks.md
- Lines: 1-34
- SHA256: f5c02d8643d3a138c8a44625d56187f331804783799bd1047936ba57ff866b93

```md
# Tasks: pattern-shadow-broaden-universe-and-4h

## 1. 冻结扩展 universe

- [ ] 1.1 构建期跑一次性脚本：binance USDT-spot `fetch_tickers` 按 24h quoteVolume 排序，排除稳定币(USDC/USDT/FDUSD/TUSD/DAI/USDP/PYUSD…)+杠杆代币(*UP/*DOWN/*BULL/*BEAR)，取 top~100，打印 base symbol 列表。
- [ ] 1.2 把结果**固化成代码常量**：更新 `fetch_historical_klines.py:DEFAULT_SYMBOLS` 与 runner `SYMBOLS`（repo `scripts/fwdshadow_runner.py` + lab `pattern_forward_shadow.py` 若硬编码 universe）为同一份冻结列表；保留原 30 币子集。

## 2. re-fetch + re-validate（gate）

- [ ] 2.1 `python3 fetch_historical_klines.py --symbols <~100> --intervals 1d,4h`：增量抓新增 ~70 币 1d+4h 入 `data/klines.db`（去重幂等）；记录 n_ok/失败 symbol。
- [ ] 2.2 重跑回测：`cf_pattern_edge_discovery.main("1d")` 与 `main("4h")` 在宽 universe 上；捕获 `Bearish Engulfing|低位跌势` 是否仍过诚实门、净 R/胜率，记入 verify 报告。**gate**：若 1d edge 不过门则范围异常须暂停讨论；4h 不过门则 4h 仅当探索（文档标注、不下结论），不阻塞日线。

## 3. 前向 runner interval 参数化

- [ ] 3.1 `scripts/fwdshadow_runner.py`：加 `argparse --interval`（默认 1d，choices 1d/4h）；record/settle 读 `interval` bar、调 `set_interval_windows(interval)`、jsonl 路径按 interval 选（1d→`pattern_forward_shadow.jsonl` / 4h→`pattern_forward_shadow_4h.jsonl`）；幂等键加 interval。逻辑(检测/退出/诚实门)不动。
- [ ] 3.2 `pattern_forward_shadow.py`（repo lab 版）同步加 `--interval`，与 runner 同口径（读 `interval=` 参数化，写分离 jsonl）。
- [ ] 3.3 部署：`cp scripts/fwdshadow_runner.py "~/Library/Application Support/cryptoarb-fwdshadow/"`。

## 4. 4h launchd jobs

- [ ] 4.1 新建 `~/Library/LaunchAgents/com.cryptoarb.pattern-forward-shadow.record4h.plist`：`--record --interval 4h`，每 4h 触发（StartCalendarInterval 多条 Hour: 1/5/9/13/17/21，Minute 偏移~7），日志 `~/Library/Logs/pattern_forward_shadow.log`，WorkingDirectory/EnvironmentVariables 同现有自包含 runner。
- [ ] 4.2 新建 `.settle4h.plist`：`--settle --interval 4h`，每日 1 次（如 10:07）。
- [ ] 4.3 `launchctl bootstrap` 两 job + `kickstart` 即时验证 record4h 能读 repo-free 自包含 + 产 4h 记录（或诚实"今日无 4h 信号"）。

## 5. 测试 + 红线 + 文档

- [ ] 5.1 单测：runner `--interval` 路由（1d/4h 写对 jsonl、幂等键含 interval、4h 不污染 1d）；scope/universe 常量非空且排除稳定/杠杆代币。
- [ ] 5.2 红线守卫 `tests/test_cf_red_line_guard.py::test_decision_paths_do_not_read_pattern_research` 仍绿（forbidden 集已含 `pattern_forward_shadow`）；全量 `python3 -m pytest -q` 绿（基线 1430 + 新测试）；compileall 通过。
- [ ] 5.3 README §日线形态前向影子记录器：更新为「扩展 universe(~100 冻结快照)+ 1d/4h 双轨」，加 4h launchd 命令、冻结口径与可比性说明、4h margin 薄/低置信注脚。

## 6. 真跑与收尾

- [ ] 6.1 日线 record 手动跑一次确认扩 universe 生效（信号数 vs 旧 30 币对比）；4h record 跑一次出首批 4h 信号。
- [ ] 6.2 结论入 verify 报告：宽 universe 1d/4h 回测 edge 裁定 + 前向首跑信号数 + 预计 n≥30 提速幅度。**不改 config、不上 live、诚实门未过前不下前向结论。**
```

## openspec/changes/pattern-shadow-broaden-universe-and-4h/specs/pattern-forward-shadow/spec.md

- Source: openspec/changes/pattern-shadow-broaden-universe-and-4h/specs/pattern-forward-shadow/spec.md
- Lines: 1-60
- SHA256: 636fa1cab2392bb1e8da369d6bbff0715e9b2af7dc66018fa438809a691c5aaa

```md
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
```

