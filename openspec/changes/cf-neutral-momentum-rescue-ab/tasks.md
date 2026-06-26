# Tasks

## 1. 谓词与数据接入

- [ ] 1.1 在 `cf_neutral_momentum_rescue_ab.py` 实现救援候选谓词:`effective_regime ∈ {choppy, mixed}` AND `direction=='neutral'` AND `(daily_bias=='bullish' OR higher_tf_bias=='bullish')` AND `pre_12h_return_pct >= pre12h_min` AND `position_in_24h_range <= range_pos_max`(阈值参数化,基准对齐 0.03 / 0.92);谓词 MUST NOT 引用 `trend.strength`
- [ ] 1.2 从 `decision_replay_tape.jsonl` 读 `replayable` 记录,提取 tech 快照字段(direction/daily_bias/higher_tf_bias/pre_12h_return_pct/position_in_24h_range)+ regime;缺字段 fail-safe 跳过并计数

## 2. CF 结算与桶统计

- [ ] 2.1 救援候选用 `resolve_counterfactual` + `klines_1s.db` TP1 保守口径(SL-first)结算前向净 R;无覆盖跳过并计数
- [ ] 2.2 按 (symbol, side, 时间窗 >1h) 簇去重,与现有 cf 驱动一致
- [ ] 2.3 设参照桶(comet-design 定:同体制已放行 aligned 多单,或"谓词近邻但 pre12h 不达标"桶),两桶同口径比净 R/簇
- [ ] 2.4 报阈值敏感性(pre12h_min × range_pos_max 多组取值),非单点

## 3. 诚实门与结论

- [ ] 3.1 经 `cf_honesty_gate.summarize_bucket`(min_sample=30 不下调)裁定;n<30 输出 INSUFFICIENT_SAMPLE,仅 suggestive
- [ ] 3.2 报告输出:谓词命中数、各桶簇数/净 R、诚实门裁定、阈值敏感表;显式声明"救援有 edge → 起后续改门 change / 为负或 INSUFFICIENT → 结案"

## 4. 红线守卫与测试

- [ ] 4.1 `tests/test_cf_red_line_guard.py` 加断言:judge/executor/portfolio_risk_guard/reviewer/position_analyst 禁 import `cf_neutral_momentum_rescue_ab`
- [ ] 4.2 驱动单元测试:谓词命中/不命中(bearish 不救、趋势体制不进桶)、无覆盖跳过、诚实门薄样本拒答、CF 结算契约传 `entry_price`/`created_at`/`side`/`stop_loss`/`take_profit`(非 `entry_ref`)
- [ ] 4.3 全量 `pytest -q` 绿(基线 1460 + 新增)

## 5. 运行与归档

- [ ] 5.1 真跑驱动产出报告,记录结论(净 R、诚实门、是否值得改门)
- [ ] 5.2 验证:`python3 -m compileall` + 驱动测试通过;确认零 live/Judge/config 改动
