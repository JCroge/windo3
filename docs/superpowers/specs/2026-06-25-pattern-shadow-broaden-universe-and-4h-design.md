---
comet_change: pattern-shadow-broaden-universe-and-4h
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-25-pattern-shadow-broaden-universe-and-4h
status: final
---

# Design: pattern-shadow-broaden-universe-and-4h

> Canonical 需求源 = OpenSpec delta spec `openspec/changes/pattern-shadow-broaden-universe-and-4h/specs/pattern-forward-shadow/spec.md`。本文档只记技术实现/风险/测试。

## 1. 背景与已就绪机件

形态前向影子（`pattern-forward-shadow`）当前 30 币 × 仅日线 × 严格低位跌势过滤 → 信号稀（3 天 5 条）、诚实门 n≥30 要数周/月。两条合法加速：扩盘口 + 加 4h（都不碰单条信号真实成熟期、不引前视）。

06-23 跨周期工作已建好 interval 感知：`fetch_historical_klines.py`(`--intervals 1d,4h`)、`cf_pattern_edge_discovery.main(interval)`+`set_interval_windows(interval)`(`BARS_PER_DAY`)、`klines.db`(30 币 1d+4h 已在库)。**仅前向 runner 日线硬编码**：部署版 `~/Library/Application Support/cryptoarb-fwdshadow/fwdshadow_runner.py`(`ccxt.binance()`/`"1d"`)、repo lab 版 `pattern_forward_shadow.py`(`interval='1d'`)。

## 2. 关键技术决策

### 2.1 settle-when-determinable（核心——使 4h 真快）
**问题**：现 `settle()` 硬门 `(now-detect).days < max_hold_days(10) → skip`，且 `_settle_one` 取 `fut[:max_hold_days]`=10 *bar*。后果：(a) 4h 信号即便 8h 触 SL/TP 也等 10 日历日 → B 失效；(b) 4h 的 10 bar=40h ≠ 回测 10 日=60 bar，前向≠回测。

**改法**（per-interval）：
```
window_bars = max_hold_days × bars_per_day(interval)     # 1d→10, 4h→60
fut = [检测 bar 之后的已闭合 <interval> bar][:window_bars]
扫描 fut（SL-first 同根保守）:
   触 SL/TP            → settle now (sl/tp)               # 4h 早退出 = 快
   无退出 & len(fut)==window_bars → settle expired
   无退出 & len(fut)<window_bars  → return None（留未结算，防提前 expired）
```
- 净 R 值与"等满 10 日再算"**完全一致**（只用检测后已闭合 bar、无前视），仅 `settled:true` 时点提前。
- 日线随之早结算（早退出信号 day2 即结算，同值）——严格改进、不破冻结的退出/诚实门逻辑。
- 删除 `settle()` 的 `(now-detect).days<10` 日历门，改由 `_settle_one` 的"整窗满才 expired / 否则 None"控制成熟。

### 2.2 dedup 按 bar 身份
record 增 `detect_bar_open_time`(检测 bar 的 `open_time` ms)；`_existing_keys` 去重键 `(symbol, detect_bar_open_time, interval)`。日线一日一 bar 等价于按日；4h 同 UTC 日多 bar 各自独立。

### 2.3 interval 参数化 + jsonl 分离
runner 加 `argparse --interval`(默认 1d, choices {1d,4h})；`set_interval_windows(interval)`；load bar 用 `WHERE interval=?`；jsonl 路径 1d→`pattern_forward_shadow.jsonl` / 4h→`pattern_forward_shadow_4h.jsonl`。repo lab 版同步。检测/退出/诚实门逻辑不动。

### 2.4 冻结扩展 universe
构建期一次性脚本：`ccxt.binance().fetch_tickers()` 按 `quoteVolume` 排序、排除稳定币(USDC/USDT/FDUSD/TUSD/DAI/USDP/PYUSD…)+杠杆代币(后缀 UP/DOWN/BULL/BEAR)、取 top~100 base → **固化成代码常量**（`fetch_historical_klines.DEFAULT_SYMBOLS` + runner `SYMBOLS` 同一份）。不动态 re-query（漂移破可比性）。

### 2.5 4h launchd（每 4h record / 每日 settle）
binance 4h bar 收于 UTC 00/04/08/12/16/20 = 本地 CST(UTC+8) 08/12/16/20/00/04。record4h 在各收盘 +~7min 触发（6 条 StartCalendarInterval，Minute=7）；settle4h 每日 1 次（10:07）。runner 只记已闭合 bar（防前视），故触发时点宽容、幂等。日线 record(09:17)/settle(周一 09:47) 不动，自动覆盖扩 universe。

## 3. 数据流
```
[构建期一次] binance 24h vol → 排稳定/杠杆 → top~100 → 冻结成代码常量
   → fetch_historical_klines --symbols<~100> --intervals 1d,4h → klines.db(增量去重)
   → cf_pattern_edge_discovery main("1d") + main("4h") → 宽universe edge(诚实门) = re-validate gate
[常驻] runner --interval 1d  (launchd 日 09:17 record / 周一 settle)  → pattern_forward_shadow.jsonl
       runner --interval 4h  (launchd 每4h record / 每日 settle)      → pattern_forward_shadow_4h.jsonl
   → 各自 settle-when-determinable + cf_honesty_gate(n<30 拒答) 独立滚动报告
```

## 4. 风险 / 取舍
| 风险 | 缓解 |
|---|---|
| 改 settle 影响日线既有行为 | 仅时点提前、净 R 值不变、无前视；视为严格改进；单测锁"早退出 day2 结算"与"窗口未满不 expired" |
| 网络 fetch ~70 币×{1d,4h} 慢/限流 | 增量幂等、失败跳过计数、binance 退避 |
| 新币历史不足 | fetch 已有短史标注；OOS 三分自然少计 |
| 4h edge 宽 universe 可能弱于日线 | re-validate 分 interval 独立裁定；4h 仅领先信号、文档明示 margin 薄(+0.208R vs +0.326R) |
| 部署副本与 repo 源漂移 | 改 repo 源 + `cp` 部署；README 记命令 |

## 5. 测试策略
- **settle-when-determinable 单测**（最关键）：early SL→day2/早 bar 结算；early TP→结算 tp；无退出+整窗满→expired；无退出+窗未满→留 None（防提前 expired）；4h 窗口=60 bar/1d=10 bar。
- **dedup 单测**：2 条 4h 同 UTC 日不同 bar 都记录；重跑幂等（按 bar_open_time）。
- **interval 路由单测**：`--interval 4h` 只读 4h、只写 4h jsonl、不污染 1d。
- **universe 单测**：常量非空、排除稳定币/杠杆代币样例。
- **红线**：`test_cf_red_line_guard::test_decision_paths_do_not_read_pattern_research` 仍绿（forbidden 含 `pattern_forward_shadow`）。
- **全量**：`pytest -q` 绿（基线 1430 + 新测试）+ compileall。
- **re-validate（真跑）**：宽 universe `cf_pattern_edge_discovery` 1d+4h edge 裁定入 verify；前向首跑信号数 vs 旧 30 币对比。
