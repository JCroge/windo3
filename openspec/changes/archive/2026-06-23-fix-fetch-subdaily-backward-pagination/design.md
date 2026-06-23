## 修复方案

**根因**:`fetch_symbol` 用 `since=None` 起步(交易所返回最近 N 根)+ `since=o[-1][0]+1` 向未来翻 → 最近批后无数据即停。

**修复**:改为从**历史起点**正向翻页。
- 计算起点 `start_ms = now_ms - max_bars × interval_ms`(interval_ms 由 timeframe 解析:1d=86400e3,4h=14400e3 等)。
- `since = start_ms`,循环 `fetch_ohlcv(since=since, limit=1000)`,每批后 `since = last_open_time + 1`,直到返回空或不足 1000 或 `len(all_rows) >= max_bars`。
- 去重:同一 open_time 可能跨批重复 → 落库 `INSERT OR IGNORE` 已幂等;内存层按 open_time 去重防 max_bars 计数虚高。
- 日线行为不变(2.75 年 < max_bars×1d 起点,正向翻一样到最新)。

**为什么不用 reverse-paginate(end→early)**:多数交易所 `fetch_ohlcv` 以 `since` 正向语义为主,正向起点法最通用、最少特例。

**cf_pattern_edge_discovery.py**:`main(interval)` 加 `argparse --interval`(默认 1d),`load(interval)` 已支持;判定逻辑零改动。4h 确认时跑 `--interval 4h`,人工核对 2 候选桶(D6:同号且≥0)。

## 风险

- [4h 数据量增大→回测变慢] → 可接受(一次性研究跑)。
- [交易所对超早 since 返回上市后首根] → 正常,短史币自然少,与日线一致处理。
