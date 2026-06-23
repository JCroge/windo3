# Tasks: fix-fetch-subdaily-backward-pagination

- [ ] 1. 修 `fetch_historical_klines.py::fetch_symbol`:加 `_interval_ms()` 解析 + 从历史起点 `now-max_bars×interval_ms` 正向分页 + 内存 open_time 去重;日线行为不变
- [ ] 2. 重抓 4h:`python3 fetch_historical_klines.py --intervals 4h`,验证每币根数 ≫1000、起点回到 ~2024、入库汇总 4h 显著增长
- [ ] 3. `cf_pattern_edge_discovery.py::main` 加 `--interval` 参数(默认 1d),不改判定逻辑
- [ ] 4. 跑 `python3 cf_pattern_edge_discovery.py --interval 4h`,核对 2 候选(Bearish Engulfing|低位跌势、Evening Star|中位涨势)的 4h 同号桶 → D6 确认/可疑
- [ ] 5. 回归:本 change 自测 + 编译;诚实汇报 4h 确认结论 + 更新记忆 daily-pattern-lab-candidates
