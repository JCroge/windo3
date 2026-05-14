# 集成指南

## 概述

本文档面向需要集成或扩展交易系统的开发者。

**系统状态（2026-05-14）**：两层多Agent系统完成，研判层每4h自动选币，交易层持续运行。Judge含统一风险预算框架（杠杆由风险约束推导）+ LLM-Rule方向冲突保护。

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

### 实时交易系统 ✅

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

**交易层消息格式（2026-05-07）**：

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

`trade_decision:{symbol}` — Judge发布，精确交易计划：
- action, confidence, reasoning, key_factors[], risk_warnings[]
- plan: {entry_zone, stop_loss, take_profit[], leverage(1-20x), size_usdt(=margin), order_type, risk_reward_ratio, effective_risk_reward_ratio, funding_cost, est_hold_hours}

`execution_result:{symbol}` — Executor发布，交易执行结果：
- status (executed/force_closed/rejected/risk_reduced)
- action, symbol, result (entry_price/pnl/leverage/amount_usdt), confidence

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
