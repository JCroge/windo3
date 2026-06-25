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
