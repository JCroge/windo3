# Long Entry Position Guard 验收文档

## 验收范围

验证 Entry Position Guard 上线后，系统能够阻止 bullish regime 下低 R:R 多头在价格短期过热位置即时追高，同时保持正常趋势多头、short side guard、R:R floor、EV gate 和接口契约不被破坏。

## 必须通过的验收项

### AC-LONGPOS-01 NEAR 复现场景不得即时开多

条件：

- `symbol=NEAR-USDT`
- `action=open_long`
- `effective_regime=bullish`
- `effective_rr=1.36`
- `rr_floor_used=1.30`
- `slot_type=low_rr_extra`
- `score=31.5`
- `position_in_24h_range=0.838`
- `prev_daily_return_pct=0.1566`
- `pre_12h_return_pct=0.0033`
- `tf_15m_confirm_long=true`
- bucket EV 给出 `p_win_used=0.667`

期望：

- 不发布即时 `open_long`。
- 发布 `hold` 或创建 `deferred_entry`。
- attribution 包含：
  - `entry_position_status=overheated`
  - `entry_position_block_reason=long_overheat_daily_gain` 或等价机器可读原因
  - `entry_range_pos_24h=0.838`
  - `entry_prev_daily_return_pct=0.1566`
- 若创建 deferred：
  - `entry_type=deferred_pullback_overheat`
  - `chase_eligible=false`
  - `deferred_target_price < signal_price`

### AC-LONGPOS-02 正常位置的 bullish low R:R long 保持可放行

条件：

- `action=open_long`
- `effective_regime=bullish`
- `effective_rr=1.36`
- `position_in_24h_range<=0.70`
- `prev_daily_return_pct<0.08`
- `pre_12h_return_pct<0.03`
- R:R floor、15m、EV、quality gate 均通过

期望：

- 不被 Entry Position Guard 拦截。
- 仍可进入 `low_rr_extra`。
- `rr_policy=long_bullish_low_rr`。
- attribution 中 `entry_position_status=normal`。

### AC-LONGPOS-03 单独 range_pos 过高触发 overheat

条件：

- `action=open_long`
- `position_in_24h_range >= LONG_LIVE_MAX_RANGE_POS`
- `prev_daily_return_pct` 和 `pre_12h_return_pct` 未超过阈值

期望：

- 不允许即时 open。
- `entry_position_block_reason=long_overheat_range_pos`。

### AC-LONGPOS-04 12h 前置涨幅过大触发 overheat

条件：

- `action=open_long`
- `pre_12h_return_pct >= LONG_LIVE_MAX_PRE_MOVE`
- `position_in_24h_range >= LONG_LIVE_DAILY_GAIN_RANGE_POS`

期望：

- 不允许即时 open。
- `entry_position_block_reason=long_overheat_pre_move`。

### AC-LONGPOS-05 日线涨幅过大触发 overheat

条件：

- `action=open_long`
- `prev_daily_return_pct >= LONG_LIVE_MAX_DAILY_GAIN`
- `position_in_24h_range >= LONG_LIVE_DAILY_GAIN_RANGE_POS`

期望：

- 不允许即时 open。
- `entry_position_block_reason=long_overheat_daily_gain`。

### AC-LONGPOS-06 无有效回调目标时直接拒绝

条件：

- long overheat 已触发
- `target_price <= stop_loss` 或 `target_price >= signal_price`
- 或 ATR/SL/price 数据不足，无法生成有效 target

期望：

- 不创建 `deferred_entry`。
- 发布 hold/reject。
- `blocked_by=long_overheat_no_valid_pullback_target`。

### AC-LONGPOS-07 overheat deferred 禁止 chase

条件：

- 已存在 `deferred_entry.entry_type=deferred_pullback_overheat`
- 价格相对 `signal_price` 继续上涨超过 chase 阈值

期望：

- 不触发 `deferred_chase`。
- `chase_eligible=false` 保持有效。
- 不发布 `open_long`。

### AC-LONGPOS-08 回调到位后必须全链路二次确认

条件：

- `deferred_pullback_overheat` 到达 `target_price`

期望：

- 必须重新执行：
  - HTF/趋势二次确认
  - 15m 二次确认
  - R:R floor
  - EV gate
  - Entry Position Guard
  - slot gate/ranking
- 任一环节失败时发布 hold/reject，并清理或继续等待 deferred，行为需有日志。
- 全部通过时才允许发布 `open_long`。

### AC-LONGPOS-09 主路径和 deferred 路径策略一致

同一组 `symbol/action/plan/tech/score` 输入，在以下路径必须得到一致的 Entry Position Guard 结果：

- 主开仓路径
- `deferred_15m_confirmation`
- `deferred_pullback`
- `deferred_chase`

期望一致字段：

- `allowed`
- `should_defer`
- `entry_position_status`
- `entry_position_block_reason`
- `deferred_target_price`

### AC-LONGPOS-10 short side guard 在主路径也生效

条件：

- `action=open_short`
- `daily_bias=bearish`
- `position_in_24h_range < SHORT_LIVE_MIN_RANGE_POS`
- 或 `pre_12h_return_pct <= SHORT_LIVE_MAX_PRE_MOVE`

期望：

- 主开仓路径与 deferred 路径均会拦截。
- 不允许只在 `_apply_regime_policy()` 单测中生效。
- rejection reason 仍兼容既有：
  - `range_position_too_low`
  - `pre_move_too_deep`

### AC-LONGPOS-11 EV bucket 不得使用 unknown entry_type

条件：

- ma aligned 多头信号进入 EV gate

期望：

- `ev_bucket_key` 应包含真实 entry type，例如 `long_bullish_ma_aligned_low_rr_extra`。
- 不得出现 `long_bullish_unknown_low_rr_extra`。
- `plan.entry_type` 在 EV gate 前已写入。

### AC-LONGPOS-12 稀疏 bucket 不得抬高 p_win

条件：

- bucket `trade_count < EV_BUCKET_MIN_TRADES`
- bucket win rate 高于当前 bayesian/global p_win

期望：

- `p_win_used` 不得被稀疏 bucket 提高。
- `ev_bucket_sparse=true`。
- attribution 包含：
  - `ev_bucket_key`
  - `ev_bucket_trade_count`
  - `ev_bucket_min_trades`
- 稀疏 bucket 可用于降低 p_win 或缩仓，但不能把负 EV 抬成正 EV。

### AC-LONGPOS-13 trade_decision.v2 向后兼容

条件：

- 任意 open/hold/reject 决策

期望：

- 既有字段不删除：
  - `schema_version`
  - `request_id`
  - `action`
  - `confidence`
  - `dispatch_path`
  - `signal_score`
  - `execution_confidence`
  - `position_scale`
  - `plan`
  - `attribution`
- 新增字段仅作为 optional attribution 字段存在。
- 旧消费方在缺少新字段时不报错。

### AC-LONGPOS-14 execution_result.v2 透传 attribution

条件：

- Entry Position Guard 允许后的 open 成交

期望：

- `execution_result.result.attribution` 透传 open 决策 attribution。
- 顶层 `execution_result.attribution` 也保留同一份或等价字段。
- Reviewer、PaperExecutor、PositionAnalyst 对新 optional 字段缺失时保持兼容。

### AC-LONGPOS-15 event backtest 与 live 同构

条件：

- 回测输入构造 NEAR 类场景：
  - `position_in_24h_range=0.838`
  - `prev_daily_return_pct=0.1566`
  - `effective_regime=bullish`
  - `effective_rr=1.36`

期望：

- 回测不会产生即时 long。
- 结果标记为 deferred/rejected。
- live 与 backtest 使用同一组默认阈值。

### AC-LONGPOS-16 配置与启动日志可验证

条件：

- `load_config(strict_live_check=False)`

期望：

- 返回所有新增配置默认值。
- hard limits 覆盖新增数值配置。
- env override 可覆盖新增配置。
- 启动 banner 打印 Entry Position Guard 摘要，包括：
  - 是否开启
  - `range_pos`
  - `pre_12h`
  - `daily_gain`
  - pullback timeout

### AC-LONGPOS-17 线上日志与 journal 可审计

命中 overheat 后，日志或 `data/journal/events_YYYYMMDD.jsonl` 至少能看到：

- `request_id`
- `symbol`
- `entry_position_status`
- `entry_position_block_reason`
- `entry_range_pos_24h`
- `entry_pre_12h_return_pct`
- `entry_prev_daily_return_pct`
- `deferred_target_price`，如适用
- `ev_bucket_key`
- `ev_bucket_trade_count`

## 建议测试文件

建议新增或扩展：

- `test_long_entry_position_guard.py`
- `test_short_side_guard.py`
- `test_ev_gate.py`
- `test_rr_floor_policy.py`
- `test_judge_deferred_regime_policy.py`
- `test_event_backtest_regime.py`
- `test_metrics_contract.py`

## 回归命令

建议至少执行：

```bash
python3 -m pytest -q test_long_entry_position_guard.py
python3 -m pytest -q test_short_side_guard.py test_ev_gate.py test_rr_floor_policy.py test_judge_deferred_regime_policy.py
python3 -m pytest -q test_event_backtest_regime.py test_metrics_contract.py
python3 -m pytest -q
```

## 部署验收

1. 合并代码和文档。
2. 确认 `.env` 或部署配置中不再需要临时关闭 `LOW_RR_SLOT_ENABLED`。
3. 重启运行进程，确认启动 banner 打印 Entry Position Guard 配置。
4. 观察 24 小时 paper/live 小额灰度。
5. 抽查 journal：
   - 不应再出现 `ev_bucket_key` 含 `unknown`。
   - overheat 信号应为 hold/deferred，不应即时 open。
   - `deferred_pullback_overheat` 不应走 chase。
6. 若无异常，再恢复或保留 low R:R extra 槽位配置。
