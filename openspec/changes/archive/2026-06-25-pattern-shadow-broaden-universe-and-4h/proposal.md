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
