# Proposal: pseudo-resonance-downweight（伪共振降权 · 病根1a）

## Why

策略诊断（agent memory `strategy-no-directional-edge-diagnosis`）**病根1：伪共振**。现行 `_compute_score`（judge.py:3403）里多个打分分量**同源于一条 MA 趋势**，在 1h/4h/1d 重复计权：

- `rule_signal` entry ±35 / `ma_aligned` ±20（MA crossover/alignment）
- `trend` direction×strength → 0~±20（MA 方向×强度）
- `higher_tf_bias` ±10（高周期 MA）

四者共线非独立 → "htf_votes 确认"是幻觉，错时一起错。真正独立的信号（OI 背离 ±12、鲸鱼 ±15、taker ±8、散户 ±8、RSI 背离）权重反而小。

**真实磁带量化（189 笔 accept）**：MA簇贡献占 |score| **中位 67% / 均值 74%**；**47/189(25%) 完全靠 MA簇**（独立信号净零或反向也照开）；39% 的 accept MA簇占比≥80%。坐实"一条 MA 趋势投 3-4 票"。

## What Changes

把 `rule_signal / ma_aligned / trend / higher_tf_bias` 视为**单一「MA 趋势块」**，其合计贡献**封顶**（diminishing returns：多个同源确认不再线性叠加），使**独立信号必须说话**才能把强信号推过入场门。封顶值与各分量权重**走 config 四段式可调可回退**（现状全硬编码）。

## Scope

**In**：
- `_compute_score` 重构 MA 趋势块为封顶合计（如合计 cap ±45，vs 当前可达 ±65）。
- MA 块各分量权重 + cap 走 config（four-segment），默认值由真实磁带验证定。
- 归因记录 MA块贡献 / 独立信号贡献 / 是否触发 cap（observability，切分用）。
- CF 回放验证（红线，见下）+ 单元测试。

**Out（非目标）**：
- 不引入新的独立信号源（→ 病根1b 另起 change）。
- 不碰 RSI 背离 ≤15 压制、不碰 RSI 极端 cap / 4h RSI 折扣（保护层不动）。
- 不碰 veto（病根3 已做）、出场、体制分类、空单硬门。

## Rollback

config 把 MA 块权重/cap 调回原值（或总开关）即回退。生效需重启 live。

## Impact / Red Line

- **策略改动红线**：`event_backtest.py` 走 RobustStrategy MA 信号、**不调 `_compute_score`**，触达不到本改动；**改用 CF 确定性回放**（`utils/decision_replay.py` 跑真实 `_make_decision`→`_compute_score`，喂真实 tech 磁带）做 pre/post 验证——这是能验证 scoring 改动的保真 harness。
- **通过标准**（design 定稿）：被 cap 影响的决策子集，验证降权后 accept/reject 翻转方向与 PnL 分布；触发率合理；无新回归。
- 上线 default 与缓进据 CF 回放结果定（默认可能保守起步或先影子）。
- 生效需重启 live。
