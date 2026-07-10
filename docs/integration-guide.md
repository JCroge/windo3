# 集成指南

## 概述

本文档面向需要集成或扩展交易系统的开发者。

**系统状态（2026-07-10）**：两层多 Agent 系统主入口为 `run_agents.py`。Open 主链路使用 `trade_decision.v2`，Executor 终态使用 `execution_result.v2`。R:R floor、Entry Position Guard、Entry Drift、Short Structural Gate 和 Tactical Exit Track 均有单点收口函数。**下游集成红线**：消费 close 类 payload 必须用 `pnl_is_final=True` 守门；消费 `risk_reduced` 必须同时检查 `result.reduce_ok` 与 `result.protection_failed`；消费 open/reject/close 结果时必须保留 `track` / `exit_profile` / `slot_type` / `tactical_close_reason`，不能只按 `regime + side` 反推出口语义。生产入口不再接旧 `live_trading.py`。

## 核心模块接口

### 多Agent交易系统（两层架构） ✅

```python
from agents.orchestrator import Orchestrator

# 使用默认配置启动（读取.env）
orchestrator = Orchestrator()
orchestrator.start()

# 或自定义配置
orchestrator = Orchestrator(config={
    "exchange": "okx",
    "interval": "1h",
    "leverage": 3,
    "max_trade_amount": 10,
    "use_testnet": False,
    "research_interval": 14400,   # 研判周期4h
    "max_active_symbols": 5,      # 最多同时交易5个标的
})
orchestrator.start()
```

**架构说明**：
- 研判层（Tier 1）：每4h运行，扫描全市场选出最优标的（最多5个）
- 交易层（Tier 2）：持续运行，对活跃标的并行分析+交易
- 标的动态轮换：研判层选出新标的时，旧标的自动平仓

**Agent消息总线（支持symbol-scoped路由）**：
```python
from agents.message_bus import MessageBus

bus = MessageBus.get_instance()

# 订阅特定标的的数据
bus.register("my_agent", ["market_data:SOL-USDT"])

# 订阅所有标的的数据（通配符）
bus.register("my_agent", ["market_data:*"])

# 发布带symbol的消息
await bus.publish("my_agent", "market_data", {"klines": [...]}, "broadcast", symbol="SOL-USDT")

msg = await bus.receive("my_agent", timeout=1.0)
```

**LLM客户端**：
```python
from agents.llm_client import LLMClient

llm = LLMClient()  # 读取ANTHROPIC_*环境变量
result = await llm.chat("system prompt", "user message")
json_result = await llm.chat_json("system prompt", "user message")
```

### 旧单策略实时交易系统（归档）

```python
from live_trading import LiveTradingSystem

system = LiveTradingSystem(
    symbol='BTC-USDT',
    interval='1h',
    exchange='okx',
    api_key='...',
    secret='...',
    password='...',
    testnet=False,
    leverage=3
)
system.run(check_interval=60)
```

`live_trading.py` 仅保留给旧单策略调试参考，生产、paper、testnet 和实盘验收都必须走 `run_agents.py`。新集成优先接入 `trade_decision.v2` / `execution_result.v2` 消息契约。

### 合约执行器 ✅

```python
from executor import ContractExecutor

executor = ContractExecutor(
    exchange_id='okx',
    api_key='...', secret='...', password='...',
    testnet=False, leverage=3
)

executor.open_long('BTC-USDT', amount_usdt=10.0)
executor.open_short('BTC-USDT', amount_usdt=10.0)
executor.close_position('BTC-USDT')
executor.get_position('BTC-USDT')  # 返回持仓或None
```

**OKX posMode 注意事项（2026-05-25）**：

- `ContractExecutor.__init__` 在 `exchange_id='okx'` 时会自动调用 `private_get_account_config()` 探测账户 `posMode`（`net_mode` / `long_short_mode`），结果缓存在 `executor._okx_pos_mode`。
- live (`testnet=False`) 探测失败 → fail-closed：`can_open_new_okx()` 返回 `False`，禁止开新仓直至人工介入。testnet 失败时降级为 `net_mode`（带 warning），可用 `OKX_POS_MODE_OVERRIDE` 环境变量覆盖。
- 业务路径**禁止手写** `params={'reduceOnly': True}` 或 `posSide`；统一调用 `_build_okx_open_params` / `_build_okx_close_params` / `_build_okx_algo_params`。
- close / reduce 前会自动 `fetch_positions()` 取交易所真实仓位，按 `availPos` 钳制 amount；51169/51205/51112/51333 拒单走 `_handle_okx_close_reject` 状态复核（`already_flat` / `external_closed` / `still_open` / `direction_conflict`），不再无限重试。

### 风控管理器 ✅

```python
from risk_manager import RiskManager

rm = RiskManager(
    max_trade_amount=10,
    max_drawdown=0.20,
    max_daily_loss=50
)

can_trade, reason = rm.check_can_trade(balance=19.33)
sl, tp = rm.calculate_stop_loss_take_profit(entry_price=81000, side='long')
size = rm.calculate_position_size(balance=19.33, amount_usdt=10.0)
```

### 策略系统 ✅

```python
from optimize_1h import RobustStrategy

strategy = RobustStrategy(ma_fast=7, ma_slow=25, rsi_period=14, rsi_threshold=75, volume_factor=1.0)
df_analyzed = strategy.analyze(df)  # 返回含 entry_long/entry_short/exit_long/exit_short 列的DataFrame
```

### 技术指标 ✅

```python
from indicators import TechnicalIndicators

ma = TechnicalIndicators.calculate_ma(df['close'], period=7)
rsi = TechnicalIndicators.calculate_rsi(df['close'], period=14)
macd, signal, hist = TechnicalIndicators.calculate_macd(df['close'])
upper, mid, lower = TechnicalIndicators.calculate_bollinger(df['close'])
```

### 币种筛选 Agent ✅

```python
from agents.coin_selector_v2 import CoinSelectorV2

selector = CoinSelectorV2()
result = selector.analyze()  # 返回优质币种列表及评分
```

## 数据持久化

| 文件 | 内容 | 说明 |
|------|------|------|
| `data/klines.db` | K线数据（SQLite） | WebSocket实时采集 |
| `data/positions.json` | 当前持仓 | Executor重启后恢复 |
| `data/risk_state.json` | 峰值余额 | 回撤计算基准 |
| `data/trade_history.json` | 交易历史 | Reviewer追踪盈亏/策略衰减 |
| `data/riskguard_state.json` | RiskGuard状态 | 持仓追踪/价格/熔断状态重启恢复 |
| `data/halt_state.json` | 全局熔断状态 | 加载损坏 fail-closed |
| `data/live_order_events.jsonl` | 订单事件流 | LiveLedger append-only |
| `data/live_position_lifecycle.json` | 持仓生命周期 | LiveLedger 原子写入 |

**状态文件命名空间（FR-008，2026-05-28）**：路径由 `utils/state_paths.py` 单一真相源派生。命名空间优先级 `STATE_NAMESPACE=live|testnet|paper` > `USE_TESTNET=true` 推断 testnet > 默认 live。live 默认完全兼容历史路径；testnet/paper 自动加 `testnet_` / `paper_` 前缀（如 `data/testnet_positions.json`）。下游若复用同一台机器跑多个 namespace，必须设置 `STATE_NAMESPACE` 隔离 6 个状态文件（`positions` / `risk_state` / `riskguard_state` / `halt_state` / `live_order_events` / `live_position_lifecycle`）。启动 banner 会打印当前 namespace 与全部 6 个路径。

## 扩展开发

### 添加新策略

继承 `StrategyBase`：

```python
from strategy_base import StrategyBase

class MyStrategy(StrategyBase):
    def populate_indicators(self, df): ...
    def populate_entry_signals(self, df): ...
    def populate_exit_signals(self, df): ...
```

### 添加新交易层Agent

继承 `BaseAgent`，订阅消息总线topic：

```python
from agents.base import BaseAgent

class MyAgent(BaseAgent):
    name = "my_agent"
    subscriptions = ["tech_analysis:*"]  # 订阅所有标的的技术分析

    async def setup(self):
        self.init_llm()  # 如需LLM

    async def on_message(self, msg: dict):
        if msg['type'] == 'tech_analysis':
            symbol = msg.get('symbol')
            data = msg['payload']
            # data包含: trend, levels, momentum, money_flow,
            #           microstructure, crowd, risk, rule_signal, llm_analysis

    async def tick(self):
        await asyncio.sleep(5)
```

**交易层消息格式（2026-05-24）**：

`market_data:{symbol}` — DataCollector发布，9维度：
- klines, klines_4h, funding_rate, funding_history, latest_price
- orderbook (asks/bids/spread/depth), oi_data (current/delta_1h/delta_4h)
- liquidations (long_vol/short_vol/direction), taker_ratio (buy_sell_ratio)
- big_trades (big_buy_vol/big_sell_vol/whale_direction), long_short_account
- data_quality (dimensions_ok/dimensions_total)

`tech_analysis:{symbol}` — TechAnalyst发布，9维度信号：
- trend (direction/strength/ma_alignment/higher_tf_bias)
- levels (support[]/resistance[]/orderbook_wall_above/below)
- momentum (rsi/rsi_divergence/volume_anomaly/volume_ratio)
- money_flow (funding_rate/trend/extreme/oi_delta/oi_divergence/taker_pressure)
- microstructure (spread/bid_ask_imbalance/whale_direction/liquidation_pressure)
- crowd (long_ratio/sentiment/contrarian_signal)
- risk (leverage_risk/volatility_regime/liquidity_score)
- rule_signal, indicators, llm_analysis

`trade_decision:{symbol}` — Judge发布，open 主链路为 `trade_decision.v2`：
- schema_version, request_id, action, confidence, reasoning, key_factors[], risk_warnings[]
- dispatch_path, signal_score, execution_confidence, position_scale, attribution
- plan: {side, entry_zone, stop_loss, take_profit[], leverage(1-20x), size_usdt(=margin), order_type, risk_reward_ratio, effective_risk_reward_ratio, funding_cost, est_hold_hours, expected_value, p_win_used, p_win_source, track, exit_profile, slot_type}

**`attribution` 字段表（2026-05-26 起）**：

| 字段 | 类型 | 含义 |
|---|---|---|
| `dispatch_path` | str | 触发路径：`main_direct` / `main_ranking` / `deferred_15m` / `deferred_pullback` / `deferred_chase` / `probe_short` / `probe_long` |
| `signal_score` | int | 规则信号原始分数（含正负方向） |
| `execution_confidence` | int | LLM/规则综合置信度（0-100） |
| `position_scale` | float | 仓位缩放因子（0.0-1.0） |
| `slot_type` | str | 槽位归属：`main` / `low_rr_extra` / `probe_short` / `probe_long` / `tactical` |
| `track` | str | 出口轨道：`main` / `tactical` / `shadow_only`；Main 默认 `main` |
| `exit_profile` | str | 出口配置：Main 为 `trend_runner`，Tactical 为 `tactical_v1`，shadow/reject 为 `none` |
| `regime` | str | 市场 regime：`bullish` / `bearish` / `mixed` / `choppy` |
| `rr_policy` | str | R:R floor 策略标签：`probe` / `long_bullish_low_rr` / `long_aligned_low_rr` / `long_aligned_path_evidence` / `short_bullish_strong` / `default` |
| `rr_floor_used` | float | 本次开仓实际套用的 R:R floor 值 |
| `rr_floor_reason` | str | floor 选择原因，机器可读（如 `long_aligned:choppy`、`probe:bullish`、`default:mixed`） |
| `symbol_trend` | str | TechAnalyst 给出的标的自身趋势方向（`bullish`/`bearish`/`neutral`） |
| `symbol_higher_tf_bias` | str | 4h HTF bias |
| `symbol_daily_bias` | str | 日线 bias |
| `entry_position_status` | str | Long Entry Position Guard 输出：`normal` / `overheated` / `oversold` |
| `entry_position_block_reason` | str | overheat 原因，机器可读：`long_overheat_range_pos` / `long_overheat_pre_move` / `long_overheat_daily_gain` / `long_overheat_no_valid_pullback_target` / `range_position_too_low` / `pre_move_too_deep` |
| `entry_range_pos_24h` | float | 入场时 24h 区间位置（0-1） |
| `entry_pre_12h_return_pct` | float | 入场前 12h 涨跌幅（decimal ratio） |
| `entry_prev_daily_return_pct` | float | 上一根已完成日线涨跌幅（decimal ratio） |
| `entry_position_policy` | str | Entry Position Guard 策略版本，当前为 `long_overheat_v1` |
| `deferred_target_price` | float | 进入 `deferred_pullback_overheat` 时等待回调的目标价 |
| `deferred_reason` | str | deferred 创建原因 |
| `ev_bucket_key` | str | 分桶 EV 命中的 bucket key，例如 `long_bullish_ma_aligned_low_rr_extra`（`plan.entry_type` 必须在 EV gate 之前写入，避免 `unknown`） |
| `ev_bucket_trade_count` | int | bucket 样本数 |
| `ev_bucket_min_trades` | int | bucket 提高 p_win 所需最小样本数（默认 10） |
| `ev_bucket_sparse` | bool | 是否稀疏 bucket（trade_count < min_trades）；为 true 时不允许把 p_win 抬高于 bayesian/global |
| `tactical_source` | str | Tactical 分类来源或 shadow/reject 原因，如 `main_quality_failed`、`direction_not_aligned` |
| `main_diagnostic_effective_rr` | float | Tactical 改写前的 Main effective R:R 诊断值 |
| `tactical_effective_rr` | float | Tactical 独立 stop/TP1/成本口径的 effective R:R |
| `tactical_expected_value` | float | Tactical 独立 EV；不复用 Main EV |
| `tactical_cost_gate` | str | `pass` / `fail`；`fail` 时应为 `shadow_only` 或拒绝，不应真开 Tactical |
| `tactical_cost_coverage` | float | Tactical gross profit 对手续费+滑点估算成本的覆盖倍数 |
| `tactical_stop_quality` | str | `normal` / `very_near`，决定 Tactical 是否可用更高仓位比例 |
| `rejection_reason` | str | 仅被拒决策出现，配合 `data/journal/events_*.jsonl` 复盘 |

下游消费这些字段做策略复盘 / 分桶胜率 / 反事实账本时，必须按 `attribution.track`、`exit_profile`、`slot_type` 和 `rr_policy` 区分出口轨道、槽位与 floor 来源；不能仅凭 `regime + side` 反推。

`execution_result:{symbol}` — Executor发布，统一为 `execution_result.v2`：
- schema_version, status, action, symbol, source, request_id, correlation_id, reason, result, timestamp
- status: executed / force_closed / rejected / risk_reduced / closed_externally / error
- 可选字段：confidence, used_plan, is_add, reduce_pct, attribution, track, exit_profile, tactical_close_reason
- close 类 payload（`action='close'` 或 status ∈ {force_closed, closed_externally}）必带 close cause 字段（2026-05-28 P0 FR-004）：
  - `exit_reason` ∈ {`local_stop_loss`, `local_take_profit`, `price_fetch_failed`, `partial_tp`, `risk_emergency`, `risk_flash_move`, `risk_position_danger`, `risk_high_leverage_danger`, `risk_trailing_stop`, `system_close_all`, `exchange_sl`, `external_unknown`, `manual_close`}
  - `close_cause`：保留原始 reason 语义，用于细粒度归因
  - `is_strategy_stop`：bool，仅 `local_stop_loss` / `exchange_sl` 为 true；下游（Judge）只在该字段为 true 时记 SL hit
  - `is_risk_forced`：bool，risk_alert / close_all / price_fetch_failed 为 true
  - `result.exit_reason` / `result.close_cause` / `result.is_strategy_stop` / `result.is_risk_forced` 镜像顶层
  - `result.protective_cleanup_state` ∈ {`cleaned`, `none`, `failed`, `unknown`}：root `_cleanup_protective_orders_on_close()` 的 SL cancel + orphan algo sweep 结果
  - 历史无新字段的 payload 必须 fail-safe 兼容（默认不计 SL）
- Tactical close / reduce payload 会透传 `track=tactical`、`exit_profile=tactical_v1`，并在相关路径带 `tactical_close_reason`：
  - `tactical_tp1`：本地 TP1 触发，按 partial TP 路径减仓 50%
  - `tactical_max_hold`：达到 Tactical 最大持仓时间后全平
  - `tactical_invalidated`：15m 反向 block 等 thesis invalidation 后全平
  - `tactical_weakened_no_progress`：thesis weakened 且超过无进展窗口后全平
- 下游不得假设 `result` 一定含 entry_price；拒绝、异常和外部平仓都必须按 `status/source/reason` 解释。

`paper_execution_result:{symbol}` — PaperExecutor发布，影子账户执行结果：
- status, action, symbol, request_id, result, paper_equity, locked_margin, free_equity
- 该 topic 与 live `execution_result` 隔离，默认不进入 live Reviewer/EV 闭环。

`daily_hard_stop_triggered` — Reviewer发布，熔断信号（broadcast）：
- reason: "daily_loss_limit" | "consecutive_losses"
- daily_pnl / count, limit

`risk_alert:{symbol}` — RiskGuard发布，风控警报：
- type (position_danger/max_drawdown/flash_move/high_leverage_danger/trailing_stop/correlation_risk/stale_position/emergency_close)
- symbol, action (close_position/close_all/reduce_exposure/warn_only)

### 添加新交易所

1. 确认 ccxt 支持
2. 在 `executor.py` 的 `__init__` 中添加对应的 `config` 分支
3. 在 `.env` 中配置对应 API 密钥

## 日志格式

实盘交易每轮输出：
```
[扫描] 价格=81524.20 RSI=57.3 MA(7/25)=82026.87/81549.03 多头信号=0 空头信号=0 持仓=无持仓
风控状态: 今日盈亏=0.00, 回撤=100.00%
```
