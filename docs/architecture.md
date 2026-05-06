# 系统架构文档

## 概述

加密货币套利系统，通过实时监控多个交易所的价格差异，自动发现并执行套利交易。

## 架构图

```
┌──────────────────────────────────────────────┐
│         Claude Code 协调层                    │
│  - 币种研判                                   │
│  - 策略优化                                   │
│  - 异常处理                                   │
└────────────┬─────────────────────────────────┘
             │
    ┌────────┼────────┐
    │        │        │
┌───▼───┐ ┌─▼──┐ ┌──▼───┐
│行情源 │ │发现│ │执行器│
│(实时) │ │引擎│ │(下单)│
└───┬───┘ └─┬──┘ └──┬───┘
    │       │       │
    └───────┼───────┘
            │
        ┌───▼───┐
        │数据库 │
        └───────┘
```

## 核心模块

### 1. 行情聚合器 (core/aggregator.py)

**职责**：从多个交易所实时获取价格数据

**实现**：
- 使用ccxt统一接口
- asyncio并发获取
- 自动存储到数据库

**数据流**：
```python
fetch_ticker(exchange, symbol) 
  → ticker{bid, ask, timestamp}
  → database.insert_ticker()
  → latest_tickers[key]
```

### 2. 套利检测引擎 (core/detector.py)

**职责**：计算价差并判断是否存在套利机会

**算法**：
```
净利润率 = (卖出价/买入价 - 1) - 买入手续费 - 卖出手续费

if 净利润率 >= 最小利润率阈值:
    触发套利信号
```

**配置参数**：
- `min_profit_rate`: 0.003 (0.3%)
- `fees`: {binance: 0.001, okx: 0.001}

### 3. 数据库 (utils/database.py)

**技术**：SQLite3

**表结构**：

```sql
-- 行情表
CREATE TABLE tickers (
    id INTEGER PRIMARY KEY,
    exchange TEXT,
    symbol TEXT,
    bid REAL,
    ask REAL,
    timestamp INTEGER
);

-- 交易记录表
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    symbol TEXT,
    buy_exchange TEXT,
    sell_exchange TEXT,
    buy_price REAL,
    sell_price REAL,
    amount REAL,
    profit REAL,
    timestamp INTEGER
);
```

## 数据流

### 实时监控流程

```
1. main.py 启动事件循环
2. aggregator.fetch_all() 并发获取所有交易所行情
3. detector.detect(tickers) 计算套利机会
4. 发现机会 → 记录日志
5. 等待check_interval秒
6. 重复步骤2-5
```

### 套利执行流程（待实现）

```
1. 检测到套利机会
2. 风控检查（余额、价格滑点、每日亏损）
3. 同时下单：买入交易所 + 卖出交易所
4. 监控订单状态
5. 记录交易结果
6. 更新账户状态
```

## 配置系统

**config.yaml**：
- 交易所列表
- 交易对列表
- 套利参数
- 风控参数
- 手续费配置

**.env**：
- API密钥
- 敏感配置

## 日志系统

**位置**：`logs/`
**格式**：`{module}_{YYYYMMDD}.log`
**级别**：INFO

**关键日志**：
- 行情获取成功/失败
- 套利机会发现
- 交易执行结果
- 错误和异常

## 扩展点

### Phase 2: 币种研判Agent
- 位置：`agents/coin_selector.py`
- 输入：历史行情数据
- 输出：优质币种列表
- 触发：每日定时

### Phase 3: 执行模块
- 位置：`core/executor.py`
- 功能：订单管理、风控、状态监控

## 性能考虑

- **并发获取**：asyncio同时查询多个交易所
- **数据库**：SQLite适合单机，后期可升级PostgreSQL
- **延迟**：1秒检查间隔，适合短线套利

## 安全考虑

- API密钥存储在.env，不提交代码库
- 风控硬限制：单次10 USDT，回撤20%
- 只读模式：无API密钥时仅获取公开数据
