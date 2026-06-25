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
