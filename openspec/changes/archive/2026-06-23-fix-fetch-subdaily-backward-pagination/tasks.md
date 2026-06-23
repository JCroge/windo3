# Tasks: fix-fetch-subdaily-backward-pagination

- [x] 1. 修 `fetch_historical_klines.py::fetch_symbol`:加 `_INTERVAL_MS` + 从历史起点 `now-max_bars×interval_ms` 正向分页 + open_time 去重;日线行为不变(验证:日线复现原 2 候选)
- [x] 2. 重抓 4h:每币 4000 根起 2024-08-25(~1.85 年),入库 4h 30000→116061 根
- [x] 3. `cf_pattern_edge_discovery.py` 加 `--interval` 参数 + 窗口 interval 感知(天→bar 换算,4h 时窗与日线对齐)
- [x] 4. 跑 4h 确认:**Bearish Engulfing|低位跌势 确认**(4h时间对齐 +0.208R 同号 actionable n293);Evening Star|中位涨势 翻负否决。方法学:原生4h窗误判翻号→时间对齐才正确
- [x] 5. 编译通过 + 日线复现 2 候选(零回归);记忆 daily-pattern-lab-candidates 已更新确认结论
