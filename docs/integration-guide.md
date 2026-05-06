# 集成指南

## 概述

本文档面向需要集成或扩展套利系统的开发者。

## 系统接口

### 1. 行情聚合器

**导入**：
```python
from core.aggregator import TickerAggregator
```

**使用示例**：
```python
import asyncio

# 初始化
aggregator = TickerAggregator(
    exchanges=['binance', 'okx'],
    symbols=['ETH/USDT', 'BTC/USDT']
)

# 获取行情
async def get_prices():
    tickers = await aggregator.fetch_all()
    for ticker in tickers:
        print(f"{ticker['exchange']} {ticker['symbol']}: {ticker['bid']}/{ticker['ask']}")

asyncio.run(get_prices())
```

**返回格式**：
```python
[
    {
        'exchange': 'binance',
        'symbol': 'ETH/USDT',
        'bid': 2371.46,
        'ask': 2371.47
    },
    ...
]
```

### 2. 套利检测引擎

**导入**：
```python
from core.detector import ArbitrageDetector
```

**使用示例**：
```python
detector = ArbitrageDetector('config.yaml')

# 检测套利机会
opportunities = detector.detect(tickers)

for opp in opportunities:
    print(f"套利: {opp['symbol']}")
    print(f"  买入: {opp['buy_exchange']} @ {opp['buy_price']}")
    print(f"  卖出: {opp['sell_exchange']} @ {opp['sell_price']}")
    print(f"  利润率: {opp['profit_rate']:.4f}")
```

**返回格式**：
```python
[
    {
        'symbol': 'ETH/USDT',
        'buy_exchange': 'binance',
        'sell_exchange': 'okx',
        'buy_price': 2371.47,
        'sell_price': 2372.50,
        'profit_rate': 0.0032  # 0.32%
    },
    ...
]
```

### 3. 数据库

**导入**：
```python
from utils.database import Database
```

**使用示例**：
```python
db = Database('data/market.db')

# 插入行情
db.insert_ticker('binance', 'ETH/USDT', 2371.46, 2371.47)

# 插入交易记录
db.insert_trade(
    symbol='ETH/USDT',
    buy_ex='binance',
    sell_ex='okx',
    buy_price=2371.47,
    sell_price=2372.50,
    amount=0.01,
    profit=0.0103
)
```

## 扩展开发

### 添加新交易所

1. 确认ccxt支持该交易所
2. 修改`config.yaml`：
```yaml
exchanges:
  - binance
  - okx
  - huobi  # 新增
```

3. 添加手续费配置：
```yaml
fees:
  binance: 0.001
  okx: 0.001
  huobi: 0.002  # 新增
```

### 添加新交易对

修改`config.yaml`：
```yaml
symbols:
  - ETH/USDT
  - BTC/USDT  # 新增
```

### 开发币种研判Agent

**位置**：`agents/coin_selector.py`

**接口规范**：
```python
class CoinSelector:
    def analyze(self, days=7):
        """分析历史数据，返回优质币种列表"""
        pass
    
    def get_recommendations(self, top_n=20):
        """返回Top N推荐币种"""
        return ['ETH/USDT', 'BTC/USDT', ...]
```

**集成方式**：
```python
from agents.coin_selector import CoinSelector

selector = CoinSelector()
symbols = selector.get_recommendations(top_n=20)

# 更新配置
config['symbols'] = symbols
```

## API参考

### TickerAggregator

**方法**：
- `fetch_ticker(exchange, symbol)` - 获取单个交易对行情
- `fetch_all()` - 并发获取所有行情
- `get_latest(exchange, symbol)` - 获取缓存的最新行情

### ArbitrageDetector

**方法**：
- `detect(tickers)` - 检测套利机会
- `_calculate_opportunity(symbol, buy_ex, sell_ex, buy_price, sell_price)` - 计算单个机会

### Database

**方法**：
- `insert_ticker(exchange, symbol, bid, ask)` - 插入行情
- `insert_trade(symbol, buy_ex, sell_ex, buy_price, sell_price, amount, profit)` - 插入交易

## 错误处理

### 交易所连接失败

```python
try:
    ticker = exchange.fetch_ticker(symbol)
except Exception as e:
    logger.error(f"获取行情失败: {e}")
    # 继续处理其他交易所
```

### 数据库错误

```python
try:
    db.insert_ticker(...)
except sqlite3.Error as e:
    logger.error(f"数据库错误: {e}")
```

## 测试

### 单元测试示例

```python
import unittest
from core.detector import ArbitrageDetector

class TestDetector(unittest.TestCase):
    def test_detect_opportunity(self):
        detector = ArbitrageDetector()
        tickers = [
            {'exchange': 'binance', 'symbol': 'ETH/USDT', 'bid': 2370, 'ask': 2371},
            {'exchange': 'okx', 'symbol': 'ETH/USDT', 'bid': 2380, 'ask': 2381}
        ]
        opps = detector.detect(tickers)
        self.assertGreater(len(opps), 0)
```

## 性能建议

- 使用asyncio并发获取行情
- 缓存最新行情数据
- 批量写入数据库
- 合理设置检查间隔
