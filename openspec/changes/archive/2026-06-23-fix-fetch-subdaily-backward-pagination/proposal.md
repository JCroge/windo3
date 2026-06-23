## Why

`fetch_historical_klines.py::fetch_symbol` 的分页方向错误:首批 `since=None` 取交易所**最近** 1000 根,随后 `since = o[-1][0]+1` 向**未来**翻页——而最近批之后已无数据,循环立即停止。结果子日线周期只能拿到最近 1000 根:日线 1000 根=2.75 年(够用,bug 未暴露),但 **4h 仅 ~5.5 个月**,导致 `daily-pattern-edge-lab` 的 4h 确认集深度不足、无法对 2 个日线候选做有效确认。

## What Changes

- 修 `fetch_historical_klines.py::fetch_symbol` 分页为**向历史纵深**抓取:从 `now - max_bars × interval_ms` 起点用 `since` 正向翻页追到最新,确保 4h 能取到 ~2 年(~4380 根)。`INSERT OR IGNORE` 幂等不变。
- `cf_pattern_edge_discovery.py::main` 加 `--interval` 参数(默认 1d),使其可在 4h 上跑确认(observability-only,不改判定逻辑)。

## Capabilities

### New Capabilities
<!-- 无 -->

### Modified Capabilities
<!-- 无 spec 级行为变更:本修复让 pattern-edge-discovery 的"历史 OHLC 幂等抓取"Requirement(已声明支持 1d 与 4h、分页拉取全部可得历史)的 4h 部分真正成立,属实现修复非 spec 变更。 -->

## Impact

- 改 `fetch_historical_klines.py`(1 函数)+ `cf_pattern_edge_discovery.py`(main 加 CLI 参数,不改判定)。
- 数据:`data/klines.db` 4h 行回填至 ~2 年(幂等)。
- observability-only,不碰 live 决策。
