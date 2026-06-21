# Comet Design Handoff

- Change: regime-aware-long-entry-guard
- Phase: design
- Mode: compact
- Context hash: d709a31156488736ffdd54463ac5bcc486e5017d51f4b1619f4b1369ef11fb70

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/regime-aware-long-entry-guard/proposal.md

- Source: openspec/changes/regime-aware-long-entry-guard/proposal.md
- Lines: 1-27
- SHA256: 5451be84699c9326d5cba55dc8d9bced6ced5a2f570fc853b7d28f47cfd8514f

```md
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
```

## openspec/changes/regime-aware-long-entry-guard/design.md

- Source: openspec/changes/regime-aware-long-entry-guard/design.md
- Lines: 1-35
- SHA256: f28228c33c4c2870015a58409ca3fb64e392f3bcab1c1e3ed93132ca9a8c6d98

```md
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
```

## openspec/changes/regime-aware-long-entry-guard/tasks.md

- Source: openspec/changes/regime-aware-long-entry-guard/tasks.md
- Lines: 1-29
- SHA256: ea0ba81908059d3133580a7fb4565466ec1ae602efbd29def666024353f93ea7

```md
## 1. 配置接入

- [ ] 1.1 `config.yaml` `risk` 段新增 `long_live_max_range_pos_choppy`（默认 0.55）及对应 daily_gain 体制键，附注释说明体制语义
- [ ] 1.2 `utils/config_loader.py` 按 four-segment 模式接入新键，缺省回退现有 0.82/0.75
- [ ] 1.3 judge `__init__` 读取并保存 `self._long_live_max_range_pos_choppy` 等字段

## 2. 体制感知阈值核心

- [ ] 2.1 新增 helper `_resolve_long_range_thresholds(regime) -> (max_range, daily_gain_range_pos)`：bullish→默认，choppy/mixed→收紧，None/未知→回退默认
- [ ] 2.2 `_check_entry_position_policy` 多单分支改用该 helper 取阈值；新增 `regime` 入参
- [ ] 2.3 在所有调用 `_check_entry_position_policy` 处（judge.py:802/931/1052/1587）传入 `eff_regime`，体制不可得传 None
- [ ] 2.4 确认主路径与 deferred 路径共用同一阈值判定，无漂移

## 3. 归因

- [ ] 3.1 attribution 增补 `entry_regime_used` 与 `entry_range_pos_threshold` 字段
- [ ] 3.2 `entry_position_policy` 标记升级为 `long_overheat_v2_regime`

## 4. 测试

- [ ] 4.1 单测：choppy + range_pos=0.66 → overheated + should_defer（对照 bullish 同值放行）
- [ ] 4.2 单测：体制 None/未知 → 回退 0.82 放行（向后兼容）
- [ ] 4.3 单测：配置覆盖 `long_live_max_range_pos_choppy` 生效
- [ ] 4.4 单测：空单候选不受多单体制阈值影响
- [ ] 4.5 回归：`python3 -m pytest -q` 全绿（含既有 Long Entry Guard / position guard 用例）

## 5. 验证支撑

- [ ] 5.1 确认 attribution 新字段可被现有 dissection / 远期收益脚本读取，供部署后按体制切分核对 choppy 多单入场位置与 PF
```

## openspec/changes/regime-aware-long-entry-guard/specs/regime-aware-long-entry-guard/spec.md

- Source: openspec/changes/regime-aware-long-entry-guard/specs/regime-aware-long-entry-guard/spec.md
- Lines: 1-58
- SHA256: 0eb39efaca2988166db35380a14ca4432df6e195d812109ee97a809033fd8aea

```md
## ADDED Requirements

### Requirement: 体制感知的多单位置阈值

多单"过热"位置门 SHALL 根据当前有效市场体制（`self._regime_manager.snapshot()['effective_regime']`，与相邻 regime policy 同源）选择 `position_in_24h_range` 的过热阈值，而非使用单一固定值。`choppy`、`mixed`、`bearish` 体制 SHALL 使用收紧后的阈值（`long_live_max_range_pos_choppy`，默认 0.55）；仅 `bullish`（确认上涨）体制 SHALL 使用现有默认阈值（`long_live_max_range_pos`，默认 0.82）。同一判定 SHALL 在主路径与 deferred 路径共用，避免漂移。

#### Scenario: choppy 体制收紧阈值拦截中位追突破
- **WHEN** 一个 `open_long` 候选在 `choppy` 体制下，`position_in_24h_range=0.66`，且非 probe
- **THEN** 位置门 SHALL 判定为 `overheated`（0.66 ≥ choppy 阈值 0.55）
- **AND** SHALL 拒绝主动 open 并按现有逻辑转 `deferred_pullback_overheat`

#### Scenario: mixed 与 bearish 体制同样收紧
- **WHEN** 一个 `open_long` 候选在 `mixed` 或 `bearish` 体制下，`position_in_24h_range=0.66`，且非 probe
- **THEN** 位置门 SHALL 判定为 `overheated`（0.66 ≥ 收紧阈值 0.55）并转 `deferred_pullback_overheat`

#### Scenario: bullish 体制维持原阈值放行
- **WHEN** 一个 `open_long` 候选在 `bullish` 体制下，`position_in_24h_range=0.66`，且非 probe
- **THEN** 位置门 SHALL 维持默认阈值 0.82，判定为 `normal` 并放行（0.66 < 0.82）

### Requirement: 体制不可得时向后兼容回退

当有效体制不可得（缺失、未知或非白名单值）时，位置门 SHALL 回退到现有默认阈值 `long_live_max_range_pos`（0.82），使行为与本变更前完全一致。

#### Scenario: 体制缺失回退默认
- **WHEN** 一个 `open_long` 候选其有效体制为 `None` 或未知，`position_in_24h_range=0.70`
- **THEN** 位置门 SHALL 使用默认阈值 0.82，判定为 `normal` 并放行

### Requirement: 体制感知位置门总开关

体制感知逻辑 SHALL 受配置键 `long_live_regime_aware_range_enabled`（默认 `true`）控制。当其为 `false` 时，多单位置门 SHALL 对所有体制使用现有默认阈值（0.82/0.75），行为与本变更前完全一致，提供实盘即时回退能力。

#### Scenario: 总开关关闭回退旧行为
- **WHEN** `risk.long_live_regime_aware_range_enabled=false`，一个 `open_long` 候选在 `choppy` 体制下 `position_in_24h_range=0.66`，且非 probe
- **THEN** 位置门 SHALL 使用默认阈值 0.82，判定为 `normal` 并放行（与变更前一致）

### Requirement: 体制阈值可配置

收紧体制（choppy/mixed/bearish）与默认体制的多单位置阈值（含 `daily_gain_range_pos` 对应键）SHALL 经 `config.yaml` `risk` 段配置键提供，并按既有 four-segment 配置模式接入；未配置时使用规范默认值（收紧 0.55/0.50，默认 0.82/0.75）。

#### Scenario: 配置覆盖 choppy 阈值
- **WHEN** `config.yaml` 设 `risk.long_live_max_range_pos_choppy=0.50`
- **THEN** choppy 体制下位置门 SHALL 以 0.50 作为过热阈值

### Requirement: 入场归因记录所用体制与阈值

位置门 SHALL 在入场 attribution 中记录本次判定所用的有效体制与生效阈值，使后续可按体制切分核对入场位置分布与盈亏。

#### Scenario: 归因含体制与阈值
- **WHEN** 一个 `open_long` 候选经体制感知位置门判定（无论放行或转 defer）
- **THEN** attribution SHALL 包含所用有效体制及该体制下生效的 `range_pos` 阈值字段

### Requirement: 不影响空单与非位置门逻辑

本能力 SHALL 仅作用于多单（long）过热位置门；空单 short-side guard、`_compute_score` 打分、regime 分类本身、出场/SL 逻辑 SHALL 不受影响。

#### Scenario: 空单不受影响
- **WHEN** 一个 `open_short` 候选在任意体制下进入位置门
- **THEN** 其判定 SHALL 完全沿用既有 short-side guard 语义，不应用多单体制阈值
```

