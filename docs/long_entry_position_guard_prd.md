# Long Entry Position Guard PRD

## 背景

2026-05-26 14:47:47 CST，NEAR-USDT 开出一笔 `open_long`，`request_id=20260526-NEAR-5ead4ff9`。该单通过 `long_bullish_low_rr` 分支进入 `low_rr_extra` 槽位：

- `effective_regime=bullish`
- `effective_rr=1.36`
- `rr_floor_used=1.30`
- `slot_type=low_rr_extra`
- `p_win_used=0.667`
- `p_win_source=bucket:long_bullish_unknown_low_rr_extra`
- 实盘成交价 `2.778`

按 OKX 1h/1d K 线复算，决策使用的上一根 1h close 为 `2.744`，24h 区间 `high=2.819`、`low=2.356`，`position_in_24h_range=0.838`。2026-05-25 日线涨幅约 `+15.66%`，实盘成交价距离 2026-05-25 高点仅约 `1.45%`。这说明该单不是 RSI 极端意义上的追高，而是价格位置已经处在短期高位后的趋势追多。

## 问题定义

当前系统对“位置过高的多头入场”缺少独立风控语义：

1. `pending_pullback` 只在 RSI 极端后触发。多头要求 RSI >= 70，NEAR 当时 RSI 约 54，因此不会触发。
2. `deferred_entry` 当前主要由 15m 入场过滤失败触发。NEAR 当时 15m 为 confirmed，因此不会进入等待。
3. `long_bullish_low_rr` 在 bullish regime 下把 R:R floor 放宽到 1.30，并进入 low_rr_extra 槽位，但没有检查标的自身 24h 区间位置、前置涨幅、前一日涨幅。
4. short 侧位置保护存在于 `_apply_regime_policy()`，但主开仓路径并未统一调用该函数，而是复制了一段 short guard 和 R:R floor 逻辑，导致主路径与 deferred 路径存在风控漂移风险。
5. bucket EV 在主路径中发生于 `plan.entry_type` 写入之前，导致分桶 key 可能出现 `unknown`，例如 `long_bullish_unknown_low_rr_extra`。这类 bucket 若样本不足或样本偏移，会放大错误放行风险。

## 产品目标

在不取消趋势多头能力的前提下，防止 bullish regime 下低 R:R 多头在短期价格过热位置即时追高成交。

目标行为：

- 价格位置正常时，既有 `long_bullish_low_rr` 与 `long_aligned_low_rr` 能继续按原规则缩仓入场。
- 价格位置过高时，即便 R:R floor、15m、EV 通过，也不能即时 open。
- 位置过高但趋势仍有效时，优先进入 `deferred_pullback_overheat`，等待回调后二次确认。
- 位置过高且回调目标无效、数据不足或风险过高时，直接 hold/reject，而不是创建不可执行的 deferred。
- 主开仓路径、deferred 路径、回测路径必须使用同一套 entry position policy。
- `trade_decision.v2` 和 `execution_result.v2` 保持向后兼容，仅新增 optional attribution 字段。

## 推荐方案

新增一层 Entry Position Guard，独立于 R:R floor、15m timing、EV gate。

### 核心判定

对 `action=open_long` 启用 long overheat guard。默认建议阈值如下，配置名与运行态字段均使用 decimal ratio：

| 配置 | 默认值 | 含义 |
|---|---:|---|
| `LONG_LIVE_POSITION_GUARD_ENABLED` | `true` | 是否启用多头位置保护 |
| `LONG_LIVE_MAX_RANGE_POS` | `0.82` | 24h 区间位置超过该值，认为接近短期高位 |
| `LONG_LIVE_MAX_PRE_MOVE` | `0.05` | 12h 预涨幅超过 5%，认为已有较大前置涨幅 |
| `LONG_LIVE_MAX_DAILY_GAIN` | `0.10` | 前一日或最近已完成日线涨幅超过 10%，认为日线过热 |
| `LONG_LIVE_DAILY_GAIN_RANGE_POS` | `0.75` | 日线过热时的辅助 range_pos 阈值 |
| `LONG_LIVE_PULLBACK_MIN_PCT` | `0.025` | 位置过热后等待回调的最小幅度 |
| `LONG_LIVE_PULLBACK_TIMEOUT_HOURS` | `4` | overheat deferred 最大等待时间 |
| `LONG_LIVE_OVERHEAT_DISABLE_CHASE` | `true` | 位置过热 deferred 禁止 chase |

命中以下任一条件时，标记 `entry_position_status=overheated`：

- `position_in_24h_range >= LONG_LIVE_MAX_RANGE_POS`
- `pre_12h_return_pct >= LONG_LIVE_MAX_PRE_MOVE` 且 `position_in_24h_range >= LONG_LIVE_DAILY_GAIN_RANGE_POS`
- `prev_daily_return_pct >= LONG_LIVE_MAX_DAILY_GAIN` 且 `position_in_24h_range >= LONG_LIVE_DAILY_GAIN_RANGE_POS`

NEAR 这笔按复算值会命中第三条：`prev_daily_return_pct=0.1566` 且 `position_in_24h_range=0.838`。

### 处理策略

命中 long overheat 后：

1. 不允许即时发布 `open_long`。
2. 若趋势、流动性、R:R 基础数据有效，则创建 `deferred_entry`：
   - `entry_type=deferred_pullback_overheat`
   - `action=open_long`
   - `signal_price=当前信号价`
   - `target_price=max(stop_loss * 1.005, signal_price * (1 - max(LONG_LIVE_PULLBACK_MIN_PCT, atr_pct)))`
   - `chase_eligible=false`
   - `timeout_hours=LONG_LIVE_PULLBACK_TIMEOUT_HOURS`
3. 若 `target_price <= stop_loss`、`target_price >= signal_price`、ATR/SL 数据缺失或数据质量不足，则直接 hold/reject，`blocked_by=long_overheat_no_valid_pullback_target`。
4. deferred 触发后必须重新执行：
   - HTF/趋势二次确认
   - 15m 二次确认
   - R:R floor
   - EV gate
   - Entry Position Guard
   - slot gate/ranking

### 统一路径

新增统一函数，例如：

```python
_check_entry_position_policy(symbol, action, plan, tech, score, context) -> dict
```

返回结构建议：

```python
{
    "allowed": bool,
    "should_defer": bool,
    "reason": "long_overheat_daily_gain",
    "entry_position_status": "normal|overheated|oversold",
    "target_price": 0.0,
    "metrics": {
        "position_in_24h_range": 0.838,
        "pre_12h_return_pct": 0.0033,
        "prev_daily_return_pct": 0.1566
    }
}
```

主开仓路径、`deferred_15m_confirmation`、`deferred_pullback`、`deferred_chase` 必须都调用该函数。short 侧现有 `SHORT_LIVE_*` 位置保护也应收敛进这个函数，避免只在 deferred helper 中生效。

### EV Bucket 修正

`plan.entry_type` 必须在 `_check_expected_value()` 之前写入，避免 bucket key 落到 `unknown`。

新增或调整配置：

| 配置 | 默认值 | 含义 |
|---|---:|---|
| `EV_BUCKET_MIN_TRADES` | `10` | bucket 提高 p_win 所需最小样本数 |
| `EV_BUCKET_SPARSE_ALLOW_UPLIFT` | `false` | 稀疏 bucket 是否允许把 p_win 提高到高于 bayesian/global |

行为要求：

- bucket 样本数 `< EV_BUCKET_MIN_TRADES` 时，不允许提高 `p_win_used`。
- 稀疏 bucket 可以用于降低 p_win 或缩仓，但不能把负 EV 信号抬成正 EV。
- attribution 必须记录 bucket key 与样本数，便于复盘。

## 接口回参影响

`trade_decision.v2` 保持既有顶层字段和 `plan` 字段不变。

新增 optional attribution 字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `entry_position_status` | str | `normal` / `overheated` / `oversold` / `deferred_pullback` |
| `entry_position_block_reason` | str | 位置保护触发原因 |
| `entry_range_pos_24h` | float | 24h 区间位置，0 到 1 |
| `entry_pre_12h_return_pct` | float | 12h 前置涨跌幅，decimal ratio |
| `entry_prev_daily_return_pct` | float | 最近已完成日线涨跌幅，decimal ratio |
| `entry_position_policy` | str | 使用的策略版本，例如 `long_overheat_v1` |
| `deferred_target_price` | float | 等待回调目标价 |
| `deferred_reason` | str | deferred 创建原因 |
| `ev_bucket_key` | str | EV 分桶 key |
| `ev_bucket_trade_count` | int | bucket 样本数 |
| `ev_bucket_min_trades` | int | bucket 最小样本要求 |
| `ev_bucket_sparse` | bool | 是否稀疏 bucket |

`execution_result.v2` 不新增强制字段。Executor 继续透传 `trade_decision.attribution` 到 `execution_result.result.attribution` 与顶层 `attribution`。

Reviewer、Counterfactual Ledger、PaperExecutor 可按 optional 字段增量记录；缺失字段必须按默认值兼容旧交易。

## 实现范围

### 需要修改

- `agents/trading/tech_analyst.py`
  - 新增 `entry_context`，至少包含 `position_in_24h_range`、`pre_12h_return_pct`、`prev_daily_return_pct`。
  - 保留 `short_context`，避免破坏既有 short 测试和旧消费方。
- `agents/trading/judge.py`
  - 新增配置读取。
  - 新增 `_check_entry_position_policy()`。
  - 主路径与 deferred 路径统一调用 entry position policy。
  - `plan.entry_type` 前移到 EV gate 之前。
  - bucket EV 样本不足处理改为不允许提高 p_win。
  - attribution 与 rejection attribution 写入新字段。
- `utils/config_loader.py`
  - 新增默认配置、env override、hard limits、启动 banner。
- `event_backtest.py`
  - 同步 long overheat gate 和 bucket sparse 语义。
- `docs/integration-guide.md` / `docs/runbook.md`
  - 在代码实现后更新运行配置与消息契约说明。

### 不需要修改

- 不修改现有止盈止损算法。
- 不扩大 `low_rr_extra` 槽位。
- 不改变 `rr_floor_default`、`rr_floor_long_bullish` 的语义。
- 不让 LLM 拥有硬否决权。

## 运维与灰度

代码上线前建议临时止血：

- `LOW_RR_SLOT_ENABLED=false` 或 `LOW_RR_EXTRA_SLOT=0`
- 若无法关闭 low R:R 槽位，则临时关闭 `PHASE2_BUCKETED_EV_ENABLED=false`

代码上线后：

1. 先 paper/live 小额灰度 24 小时。
2. 观察 `entry_position_status=overheated` 的 hold/deferred 数量。
3. 检查是否出现 `ev_bucket_key` 含 `unknown`。
4. 检查 `deferred_pullback_overheat` 是否被错误 chase。
5. 如正常，再恢复 low R:R extra 槽位。

## 风险与取舍

- 阈值过严会漏掉强趋势延续行情，因此默认策略选择 deferred 而非一律 reject。
- 阈值过松会继续允许山顶接货，因此日线涨幅与 24h range_pos 必须联合作为硬条件。
- bucket EV 修正可能短期降低开仓数量，但能避免稀疏样本把低质量信号抬成正 EV。
- 主路径和 deferred 路径统一会触及较多测试，但这是必要修复；只补 long 分支会留下 short 主路径漂移风险。
