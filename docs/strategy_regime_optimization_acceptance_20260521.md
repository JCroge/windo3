# 策略 Regime 优化验收标准

日期：2026-05-21  
关联需求：`docs/strategy_regime_optimization_prd_20260521.md`

## 1. 验收目的

验证策略 Regime 优化是否在控制复杂度的前提下解决以下问题：

- bullish 市场中 short 信号过度亏损。
- long 信号因为全局 EV / R:R 门槛被误杀。
- regime 边界抖动导致同一信号忽放忽拒。
- 低 R:R 信号收益小但占用主 slot。
- CounterfactualLedger 被误用为全市场机会诊断。
- 分批止盈复杂度被提前引入执行层。

验收重点是交易行为是否可控、可解释、可回滚，而不是承诺策略收益。

## 2. 验收范围

模块范围：

- `utils/market_regime.py` 或等价 RegimeManager。
- `agents/trading/judge.py`。
- `utils/candidate_ranker.py`。
- `agents/trading/position_analyst.py`。
- `agents/trading/paper_executor.py` 或新增 shadow tracker。
- `utils/counterfactual_ledger.py`。
- `utils/config_loader.py`。
- `event_backtest.py`。

不验收：

- 价格触发分批止盈。
- 扩大实盘额度。
- 新交易所接入。
- 研判层选币全面重构。

## 3. 验收环境

建议配置：

```dotenv
MAX_TRADE_AMOUNT=30
MAX_CONCURRENT_POSITIONS=3
EFFECTIVE_BALANCE_CAP=300

REGIME_HYSTERESIS_ENABLED=true
SHORT_REGIME_GUARD_ENABLED=true
PROBE_SHORT_ENABLED=true
LOW_RR_SLOT_ENABLED=true
COUNTERFACTUAL_LEDGER_ENABLED=true

RR_FLOOR_LONG_BULLISH=1.30
```

验收数据：

- 构造单测 fixtures。
- 2026-05-20 至 2026-05-21 历史日志回放。
- paper/testnet 连续运行数据。

证据文件：

- `logs/agent_judge_*.log`
- `logs/agent_position_analyst_*.log`
- `data/rejected_signal_events.jsonl`
- `data/rejected_signal_lifecycle.json`
- `data/paper_trades.jsonl`
- pytest 输出
- 回测报告

## 4. 功能验收项

### AC-REG-01：Regime Hysteresis 防抖

测试步骤：

1. 构造 raw regime 序列：`bullish, mixed, bullish, mixed, bullish`。
2. 当前 effective regime 初始为 bullish。
3. 连续调用 RegimeManager 更新。

通过标准：

- effective regime 保持 bullish。
- candidate_regime 和 candidate_count 正确记录。
- 日志显示 raw regime 抖动但未切换 effective regime。

失败标准：

- 单次 mixed 直接把 effective regime 改成 mixed。
- 同一时间窗口内 bullish/mixed 来回切换。

### AC-REG-02：Regime 降级需要确认

测试步骤：

1. 当前 effective regime 为 bullish。
2. 输入 `mixed, mixed`，confidence 均 >= 65。

通过标准：

- 第一次 mixed 后 effective 仍为 bullish。
- 第二次 mixed 后 effective 切换为 mixed。
- `last_changed_at` 更新。

失败标准：

- 第一次 mixed 就降级。
- 第二次 mixed 后仍不切换，且无 min_hold 或 confidence 解释。

### AC-REG-03：最小停留期

测试步骤：

1. effective regime 刚切换到 bullish。
2. 在 30 分钟内输入 mixed。

通过标准：

- 非 critical 情况下不切换。
- attribution 或日志包含 `min_hold_remaining_sec`。

失败标准：

- 停留期内普通 mixed 直接切换。

### AC-REG-04：Plan 固化 Entry Regime

测试步骤：

1. 在 effective_regime=bullish 时生成 low R:R long plan。
2. 生成后 raw regime 抖到 mixed。
3. PositionAnalyst 在 60 分钟内评估该持仓。

通过标准：

- `plan.attribution.entry_regime=bullish`。
- PositionAnalyst 不因短时 mixed 立即减仓。
- review 日志同时包含 `entry_regime` 和 `current_regime`。

失败标准：

- 持仓刚建立即因 regime 抖动被减仓。
- review 中没有 regime attribution。

### AC-PARAM-01：Phase 1 只改变两个 Live 策略参数

测试步骤：

1. 检查配置加载和启动 banner。
2. 检查会改变 live 交易结果的新参数。

通过标准：

- Phase 1 live 只允许 `RR_FLOOR_LONG_BULLISH` 和 `SHORT_REGIME_GUARD_ENABLED` 改变交易结果。
- 其他开关只控制记录、保护或功能启停，不做网格寻优。
- 启动 banner 展示相关参数。

失败标准：

- 一次上线 10 个以上会改变交易结果的可调参数。
- 参数未进 `.env.example` 或 banner。

### AC-PARAM-02：配置可回滚

测试步骤：

1. 设置所有新增开关为 false。
2. 构造同一 trade signal。

通过标准：

- Judge 行为回到旧逻辑。
- 不生成 new regime policy、probe_short、low_rr_extra_slot 相关动作。

失败标准：

- 关闭开关后仍触发新策略分支。

### AC-SHORT-01：Bullish Regime 普通 Short 被 Guard

测试步骤：

1. effective_regime=bullish。
2. 构造普通 open_short：score=-45、15m confirm short、R:R=1.4。

通过标准：

- 不发布 live open_short。
- 发布 hold 或 rejected_signal。
- CounterfactualLedger 记录 rejected plan。
- attribution 包含 `blocked_by=short_regime_guard`。

失败标准：

- bullish 中普通 short 直接 live 开仓。
- 拒绝后没有可复盘记录。

### AC-SHORT-02：Bullish Regime 强 Short 仍可放行

测试步骤：

1. effective_regime=bullish。
2. 构造 open_short：score<=-70、htf_bearish_votes>=2、15m confirm short、R:R>=1.8、EV>=0。

通过标准：

- 可进入 Ranking。
- attribution 标记 `rr_policy=short_bullish_strong`。
- 杠杆和仓位不超过配置上限。

失败标准：

- 所有 short 被无条件禁止。
- 强 short 放行但缺 attribution。

### AC-SHORT-03：Probe Short 早期反转通道

测试步骤：

1. effective_regime=bullish。
2. 构造市场级早期反转信号：BTC 4h RSI 从 >=75 下穿 70，且放量阴线。
3. 构造标的级 short：score=-55、15m confirm short、R:R=1.35、liquidity_score>0。

通过标准：

- 生成 `probe_short`，而不是普通 short。
- 保证金 <= `MAX_TRADE_AMOUNT * 0.30`。
- 杠杆 <= 3x。
- 同时只能存在 1 个 probe_short。
- attribution 包含 `is_probe=true` 和触发原因。

失败标准：

- probe short 使用普通仓位或普通杠杆。
- probe short 可加仓。
- 多个 probe short 同时开。

### AC-SHORT-04：Probe Short 冷却

测试步骤：

1. 连续构造 2 笔 probe_short shadow/live SL。
2. 再构造第三个 probe_short 触发条件。

通过标准：

- 第三个 probe_short 被冷却 24 小时。
- 日志包含 `probe_short_cooldown`。

失败标准：

- 连续 probe 亏损后仍持续试空。

### AC-RR-01：Bullish Low R:R Long 放行

测试步骤：

1. effective_regime=bullish。
2. 构造 open_long：R:R=1.35、15m confirm long、htf_votes>=2、score>=50、EV>=0。

通过标准：

- 不因默认 R:R 1.5 被直接拒绝。
- attribution 包含 `rr_policy=long_bullish_low_rr`。
- `is_low_rr=true`。
- 保证金 <= `MAX_TRADE_AMOUNT * 0.5`。
- 杠杆 <= 首版上限，建议 5x。

失败标准：

- 仍按默认 1.5 硬拒。
- 低 R:R long 用满仓满杠杆。

### AC-RR-02：低 R:R Long 不挤占高 R:R 主槽位

测试步骤：

1. 当前主槽位剩余 1 个。
2. 同一 ranking 窗口进入两个候选：
   - A：high R:R=1.7，score=50。
   - B：low R:R=1.32，score=60。

通过标准：

- A 优先获得主槽位。
- B 只能进入 low_rr_extra_slot 或 rejected/shadow。
- Ranking 日志展示 `low_rr_penalty`。

失败标准：

- B 因 score 更高挤掉 A。

### AC-RR-03：低 R:R 附加槽限制

测试步骤：

1. 主槽位满。
2. low_rr_extra_slot 已有 1 个 low R:R long。
3. 再进入一个 low R:R long。

通过标准：

- 第二个 low R:R long 不进入 live。
- 进入 CounterfactualLedger。

失败标准：

- 附加槽无限扩张。

### AC-LEDGER-01：Rejected Plan 被记录

测试步骤：

1. 构造一个已有 plan 但因 confidence<60 被拒的 long。
2. 构造一个因 short_regime_guard 被拒的 short。

通过标准：

- `data/rejected_signal_events.jsonl` 写入 `rejected_plan_created`。
- 字段包含 symbol、side、regime、score、confidence、R:R、reject_reason、entry、SL、TP。
- 每行是合法 JSON。

失败标准：

- 被拒 plan 没有记录。
- 记录缺少 reject_reason 或 plan 价格。

### AC-LEDGER-02：Shadow TP/SL 归因

测试步骤：

1. 构造 rejected plan。
2. 输入后续 price_tick，先触发 TP。
3. 构造另一笔先触发 SL。

通过标准：

- 第一笔 lifecycle 状态变为 `shadow_tp`。
- 第二笔 lifecycle 状态变为 `shadow_sl`。
- 不下真实订单。
- 不影响真实 positions。

失败标准：

- shadow 触发真实下单。
- TP/SL 归因错误。

### AC-LEDGER-03：Ledger 范围声明

测试步骤：

1. 生成 shadow 报告。
2. 检查报告说明。

通过标准：

- 报告明确写明只统计 rejected planned signals。
- 报告不声称覆盖所有 hold 标的或全市场机会。

失败标准：

- 报告把 shadow 胜率表述为真实策略胜率。

### AC-EXEC-01：Phase 1 不新增价格触发分批止盈

测试步骤：

1. 搜索实现中是否新增 TP1 price monitor、自动取消重设 SL 条件单路径。
2. 运行低 R:R long 开仓流程。

通过标准：

- Phase 1 没有新增价格触发 TP1 执行循环。
- 低 R:R 风险只通过仓位和杠杆限制控制。
- 现有 PositionAnalyst reduce_position 路径不被伪装成 TP1 自动止盈。

失败标准：

- Phase 1 新增复杂条件单重设逻辑。
- TP1 成交后 SL 修改失败无兜底。

### AC-OBS-01：Attribution 可解释

测试步骤：

1. 构造以下四种信号：
   - 普通 high R:R long。
   - low R:R bullish long。
   - short 被 guard。
   - probe_short。

通过标准：

每个 `trade_decision` 或 `rejected_signal` 都包含：

- `entry_regime`
- `raw_regime`
- `regime_confidence`
- `rr_policy`
- `slot_type`
- `blocked_by` 或空值
- `is_probe`
- `is_low_rr`

失败标准：

- 需要读源码才能解释为何放行或拒绝。

### AC-RISK-01：硬风控优先级不变

测试步骤：

1. 设置 `data_quality.degraded=true`。
2. 设置全局 halted。
3. 设置 reconciliation_pending。
4. 构造 low R:R bullish long 和 probe_short。

通过标准：

- 所有新增策略分支都被硬风控拦截。
- 不发布 live open。
- rejection reason 指向硬风控，而不是 low R:R / regime。

失败标准：

- 新策略分支绕过 data_quality、halt、reconciliation。

## 5. 回测验收

### AC-BT-01：事件回测支持方向和 Regime

通过标准：

- `event_backtest.py` 或等价回测支持 `regime` 列。
- 支持 long/short 不同 R:R 下限。
- 支持 bullish short guard。
- 支持 low R:R 仓位缩放。
- 输出 long/short 分项指标。

失败标准：

- live 新逻辑无法在事件回测中复现。

### AC-BT-02：不接受小样本过拟合

通过标准：

- 任一参数组合必须至少在 validation window 有 30 笔以上样本，或标记为样本不足。
- 不允许用 18 小时 21 个样本直接确定最终参数。
- 报告必须展示 train / validation 分离。

失败标准：

- 只报告单窗口最优收益。
- 没有样本数和分项 PF。

### AC-BT-03：多目标指标

回测报告必须包含：

- total PF
- long PF
- short PF
- bullish long PF
- bullish short PF
- max drawdown
- avg win / avg loss
- trade count
- low R:R trade count
- probe_short trade count

通过标准：

- 若 bullish short PF < 0.8 且样本 >= 10，short guard 不得放宽。
- 若 low R:R long PF < 1.2 且样本 >= 20，`RR_FLOOR_LONG_BULLISH` 不得低于 1.5。

失败标准：

- 只看总 PnL，不看分项。

## 6. Paper/Testnet 验收

### AC-PT-01：阶段运行

通过标准：

- Phase 1A、1B、1C 分阶段启用。
- 每阶段至少运行 48 小时 paper/testnet。
- 每阶段只改变允许的 live 参数。

失败标准：

- 三个阶段一次性全部打开。

### AC-PT-02：7 天观察指标

进入 live 小幅放行前，paper/testnet 需满足：

- low R:R bullish long 样本 >= 20，PF >= 1.2。
- bullish short 被 guard 后，short 总亏损下降。
- probe_short 若样本 < 10，不得扩大仓位。
- 无 Daily Hard Stop。
- 无因 regime 抖动导致的开仓后 60 分钟内非风险减仓。

失败标准：

- 低 R:R long 样本不足仍放量。
- probe_short 亏损后继续扩大。

## 7. 回归测试建议

新增或扩展测试文件：

```bash
python3 -m pytest -q test_regime_hysteresis.py
python3 -m pytest -q test_short_regime_guard.py
python3 -m pytest -q test_low_rr_slots.py
python3 -m pytest -q test_counterfactual_ledger.py
python3 -m pytest -q test_event_backtest_side_regime.py
```

既有回归：

```bash
python3 -m pytest -q test_risk_budget.py test_ev_gate.py test_ranking_slots.py
python3 -m pytest -q test_paper_executor.py test_live_ledger.py
python3 -m pytest -q test_event_backtest.py test_event_backtest_real_data.py
python3 -m pytest -q test_full_pipeline.py
```

全量回归：

```bash
python3 -m pytest -q
```

## 8. 最终验收结论模板

```text
验收日期：
代码版本：
配置：

AC-REG: 通过/失败
AC-PARAM: 通过/失败
AC-SHORT: 通过/失败
AC-RR: 通过/失败
AC-LEDGER: 通过/失败
AC-EXEC: 通过/失败
AC-RISK: 通过/失败
AC-BT: 通过/失败
AC-PT: 通过/失败

是否允许进入下一阶段：
阻塞问题：
残余风险：
```

