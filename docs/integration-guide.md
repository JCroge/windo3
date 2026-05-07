# 集成指南

## 概述

本文档面向需要集成或扩展交易系统的开发者。

**系统状态（2026-05-06）**：实盘交易系统已完成，OKX BTC-USDT 1h 3x杠杆运行中。

## 核心模块接口

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
| `data/positions.json` | 当前持仓 | 重启后恢复 |
| `data/risk_state.json` | 峰值余额 | 回撤计算基准 |

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
