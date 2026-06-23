# Design (high-level): pseudo-resonance-downweight（病根1a）

> 高层架构决策。详细机制 + delta spec 在 comet-design 产出。

## 决策 1：MA 趋势块封顶（已定）

把四个同源分量合成一个 bloc 再封顶，而非各自线性叠加：

```
ma_bloc_raw =  rule_component(±35/±20)
             + trend_component(0~±20)
             + htf_component(±10)
ma_bloc = sign(ma_bloc_raw) * min(|ma_bloc_raw|, MA_BLOC_CAP)   # 同向封顶
score = ma_bloc + 独立信号(RSI背离 + OI + 鲸鱼 + 散户 + taker) + 保护层
```

- `MA_BLOC_CAP` 默认候选 ~45（vs 当前可达 65）：据磁带 spike（MA簇中位占 67%），cap 45 会让纯 MA簇最强情形从 65 降到 45，独立信号（最大 RSI背离35 + OI12 + 鲸鱼15…）必须参与才能把强信号推过门。**默认值 comet-design 用 CF 回放定**。
- 仅对**同向**叠加封顶（bloc 内部反向分量正常抵消后再 cap 绝对值）。

## 决策 2：config 四段式（已定，现状全硬编码）

- `pseudo_resonance_downweight_enabled`（总开关）
- `ma_bloc_cap`（默认待定）
- 可选：各分量权重键（rule/ma_aligned/trend/htf），保守起步可只放 cap，权重维持原值。

## 决策 3：保护层 / 独立信号不动（已定）

RSI 背离 ≤15 压制、RSI 极端 cap、4h RSI 折扣、OI/鲸鱼/taker/散户权重均不动——本 change 只重组 MA 共线簇。

## 归因（observability）

新增 attribution：`ma_bloc_contribution`、`independent_contribution`、`ma_bloc_capped`（bool），供 Reviewer/CF 切分降权效果。

## 验证（红线）—— CF 回放，非 event_backtest

- event_backtest 触达不到 `_compute_score`；用 `utils/decision_replay.py` 跑真实 `_make_decision` 重算 score。
- 两臂：开关 off（baseline）vs on（capped），喂真实 tech 磁带。
- **通过标准**（comet-design 定稿）：(1) 被 cap 影响子集的 accept→reject 翻转方向合理（砍掉无独立佐证的纯 MA 追势单）；(2) 该子集 PnL 分布不变差；(3) 全量无新回归（未触发 cap 的决策 score 不变）。
- 单元测试：bloc 封顶数学、同向/反向、开关 off 回退、独立信号不受影响、归因字段。

## 风险

- score 是全系统决策核心，爆炸半径大 → 默认保守 + CF 回放把关 + 开关回退。
- 单点收口：`_compute_score` 是单一函数，天然单点；不得在别处复制 bloc 逻辑。
