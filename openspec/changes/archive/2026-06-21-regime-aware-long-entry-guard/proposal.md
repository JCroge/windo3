## Why

多单"过热"位置门 `_check_entry_position_policy`（judge.py:2825）当前用体制无关的固定阈值 `max_range=0.82`。数据诊断（trade_history 现策略 39 笔 + K 线远期收益）证实：在 `choppy` 体制下，多单在区间 0.55–0.66 位置"追突破"系统性亏损——choppy 多单 PF 0.72、方向胜率 27%、入场后 +1h 远期收益仅 25% 胜率、信号分与方向相关性 −0.04。典型案例 HYPE 多单买在 `range_pos=0.66`（低于 0.82 阈值被放行），入场后单调下跌（+30m −0.41% / +4h −1.49%）。根因是驱动方向的 `_compute_score` 完全体制盲——在震荡盘里"追突破"本就是错误策略。

## What Changes

- 让多单位置门的 `max_range` / `daily_gain_range_pos` 阈值**体制感知**：`choppy`/`mixed` 体制收紧（`max_range` 0.82 → 约 0.55，可配置），`bullish`(trending) 体制保持现有 0.82。
- 阈值触发后**复用已存在的** `deferred_pullback_overheat` 路径——把 choppy 里的"追突破"自动转成"等回调再进"（震荡盘正确的均值回归入场）。不新增出场/入场机制。
- 新增 `config.yaml` `risk` 段配置键（four-segment 模式），缺省**向后兼容**：体制不可得时回退现有 0.82，行为不变。
- 扩展入场 attribution：记录实际使用的 regime 与阈值（`entry_position_policy` 版本标记 + 体制/阈值字段），供后续用 PF/远期收益脚本验证。

非目标（明确不做）：不改 `_compute_score` 打分逻辑（方案 B，后续再议）；不改 regime 分类本身；不碰出场/SL/紧急清仓；不碰 short-side guard。

## Capabilities

### New Capabilities
- `regime-aware-long-entry-guard`: 多单入场位置门按市场体制选择 `range_pos` 阈值，choppy/mixed 收紧并转 deferred pullback，trending 维持现状；阈值可配置且体制不可得时回退兼容默认。

### Modified Capabilities
<!-- 无：long overheat guard 此前无独立 spec；short-main-path-risk-guard 仅覆盖空单，不受影响。 -->

## Impact

- **代码**：`agents/trading/judge.py`（`_check_entry_position_policy` threading regime + 阈值选择；attribution 扩展）；`config.yaml`（risk 段新增体制阈值键）；`utils/config_loader.py`（按 four-segment 模式接入新键）。
- **行为**：仅影响 `choppy`/`mixed` 体制下的主动多单 open——更多被判 overheated 并转 `deferred_pullback_overheat`；`bullish` 体制与所有空单行为不变。
- **配置/兼容**：新键有缺省值，未配置或体制不可得时完全等价于当前 0.82 行为，无破坏性变更。
- **验证**：复用现有 dissection / 远期收益脚本对比 choppy 多单入场位置分布与 PF。
