# Tasks: pattern-shadow-broaden-universe-and-4h

> **2026-06-25 范围调整**：Task 2.2 的 re-validate gate **失败**（宽 ~100 universe 干净证伪日线/4h 形态 edge）。用户裁定「改为 observability 记录证伪，不部署 4h cron」。故 §4（4h launchd jobs）**作废**；其余（冻结 universe + runner interval 参数化 + settle-when-determinable）保留并部署日线。详见 verify 报告。

## 1. 冻结扩展 universe

- [x] 1.1 一次性派生脚本 `scripts/derive_universe.py`（binance 24h vol top + 排稳定币/杠杆/非标准 base），取 top~100。
- [x] 1.2 固化成 `fetch_historical_klines.py:DEFAULT_SYMBOLS`（100 冻结）+ runner `SYMBOLS` 同一份。

## 2. re-fetch + re-validate（gate）

- [x] 2.1 fetch 102 币 ×{1d,4h} 入 `data/klines.db`（增量幂等）。
- [x] 2.2 重跑回测 `main("1d")`/`main("4h")`。**gate 结果：失败——`过三关=0`，1d/4h 双双干净证伪，所有 pattern×context 均 R 全负，`Bearish Engulfing|低位跌势` 在宽 universe 不进排名（原 30 币 +0.326R 是小样本/选择偏差）。** 记入 verify 报告，触发范围调整。

## 3. 前向 runner interval 参数化

- [x] 3.1 `scripts/fwdshadow_runner.py`：`--interval {1d,4h}` + 窗口×bpd + **settle-when-determinable** + dedup-by-bar-ts + jsonl 分离（commit 69e8b96，task review Spec✅/质量 Approved）。
- [x] 3.2 `pattern_forward_shadow.py` lab 版同步参数化（commit 63cc622）。
- [x] 3.3 部署更新后的 runner `cp` 到 `~/Library/Application Support/cryptoarb-fwdshadow/`（消除漂移；日线 cron 自动用新版=宽 universe + settle-when-determinable）。

## 4. 4h launchd jobs —— ❌ 作废（edge 已证伪，不部署 4h 加速 cron）

- [~] 4.1 ~~record4h plist~~ **不部署**（4h 能力在 runner `--interval 4h` 里，需要时手动跑；不加速收集已证伪非-edge）。
- [~] 4.2 ~~settle4h plist~~ **不部署**。
- [~] 4.3 ~~4h launchd 验证~~ **不适用**。

## 5. 测试 + 红线 + 文档

- [x] 5.1 单测 `tests/test_fwdshadow_runner.py` 7 例（settle-when-determinable 三态 + 窗口×bpd + dedup + interval 路由）。
- [x] 5.2 红线守卫绿 + 全量 `pytest -q` **1437 passed / 0 failed**（1430 + 7）+ compileall 通过。
- [x] 5.3 README §日线形态前向影子记录器：更新为扩 universe + runner interval 参数化 + **settle-when-determinable** + **2026-06-25 证伪结论 + 4h cron 不部署**说明。

## 6. 真跑与收尾

- [x] 6.1 部署版 runner 自检（SYMBOLS=100、resolve_signal 存在）；4h 能力经单测验证（不部署 cron）。
- [x] 6.2 结论入 verify 报告：**re-validate 干净证伪日线/4h edge；保留工具改进、部署日线、4h cron 不部署**。不改 config、不上 live。
