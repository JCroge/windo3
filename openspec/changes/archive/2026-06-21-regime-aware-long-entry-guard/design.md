## Context

`_check_entry_position_policy`（judge.py:2825）是多单/空单位置门的统一入口。多单"过热"分支当前用固定阈值：`_long_live_max_range_pos=0.82`、`_long_live_daily_gain_range_pos=0.75`（judge.py:2869-2872），与市场体制无关。超阈时已会转 `deferred_pullback_overheat`（追突破→等回调，judge.py:2895-2909）——即"回调入场"机制**已存在**。

驱动方向的 `_compute_score`（judge.py:3283）完全不引用 regime；regime 仅在下游 `_apply_regime_policy` / `rr_floor_long_aligned_choppy` 调整 R:R 地板与 slot，不影响方向与位置门。诊断证实 choppy 体制下多单在 0.55–0.66 位置追突破系统性亏损（PF 0.72、方向胜率 27%、+1h 远期 25%）。

有效体制在 judge 内已可获取（如 `_apply_regime_policy` 中的 `eff_regime` 来自 `self._regime_manager._effective_regime`）。

## Goals / Non-Goals

**Goals:**
- 多单过热阈值按体制选择：choppy/mixed 收紧（默认 0.55），bullish 维持 0.82。
- 复用现有 `deferred_pullback_overheat` 路径，零新增出场/入场机制。
- 体制不可得时回退默认阈值，向后兼容、无破坏性。
- 归因记录所用体制与阈值，支撑事后 PF/远期收益验证。

**Non-Goals:**
- 不改 `_compute_score`（方案 B，后续）。
- 不改 regime 分类逻辑本身。
- 不碰出场/SL/紧急清仓、不碰 short-side guard。

## Decisions

1. **阈值选择点**：在 `_check_entry_position_policy` 多单分支内，将 `max_range`/`daily_gain_range_pos` 由固定读取改为按有效体制查表。新增私有 helper `_resolve_long_range_thresholds(regime) -> (max_range, daily_gain_range_pos)`，单一收口主/deferred 路径。
2. **体制分组**：`bullish` → 默认阈值（0.82/0.75）；`choppy`、`mixed` → 收紧阈值（默认 0.55 / 收紧后的 daily_gain）；其它/None/未知 → 回退默认（兼容）。仅 `pre_move`/`daily_gain` 联合判定中的 `daily_gain_range_pos` 同步体制化，`max_pre`/`max_daily` 本次不动。
3. **体制来源**：在调用 `_check_entry_position_policy` 处把 `eff_regime` 作为入参传入（与现有 `_apply_regime_policy` 同源），避免函数内部再次耦合 regime_manager；体制不可得传 `None`。
4. **配置接入**：`config.yaml` `risk` 段新增 `long_live_max_range_pos_choppy`（默认 0.55）及对应 daily_gain 键，按既有 four-segment 模式在 `config_loader` 接入；judge `__init__` 读取并保存为 `self._long_live_max_range_pos_choppy` 等，缺省回退现值。
5. **归因**：在 `result['metrics']` 或 attribution 增补 `entry_regime_used` 与 `entry_range_pos_threshold` 字段；`entry_position_policy` 标记升级为 `long_overheat_v2_regime`，便于脚本按版本切分。

## Risks / Trade-offs

- **回调入场在 choppy 仍可能被套**：但 deferred 目标价有 `stop_loss*1.005` 地板（judge.py:2897），风险有界；且本就是用更优入场替代追顶。
- **阈值过紧 → choppy 多单大量转 defer 难成交**：跳过坏入场本身即目标；可经配置回调阈值微调。需在 verify 阶段观察 defer 转化率与成交率。
- **mixed 归类**：把 mixed 一并收紧可能误伤部分趋势延续；缺省可先只收紧 choppy、mixed 暂用默认，留配置开关（design 倾向 choppy+mixed 同收紧，验证后再分化）。
- **体制误判传导**：本变更不改 regime 分类，若 regime 本身判错，阈值会跟着错；属已知上游依赖，不在本次范围。
