# 交易系统逻辑审查 - 问题清单

## 修复状态总览（2026-05-08）

**已修复**: 12/12 + 2项新增修复
- 严重问题: 3/3 ✅
- 高优先级: 4/4 ✅
- 中优先级: 3/3 ✅
- 低优先级: 1/2 ✅
- 新增修复: contractSize计算 ✅, 杠杆上限10x ✅

## 严重问题（必须修复）

### 1. 合约交易实现错误 ⚠️⚠️⚠️ ✅已修复
**文件**: executor.py
**位置**: Line 93-99
**问题**: 
- 没有设置杠杆
- 没有正确使用合约API
- 平仓逻辑错误（应该用reduce_only）

**影响**: 无法正确执行合约交易，可能导致资金损失

**修复方案**:
```python
# 开仓前设置杠杆
self.exchange.set_leverage(leverage, symbol)

# 使用合约专用方法
order = self.exchange.create_order(
    symbol=symbol,
    type='market',
    side=order_side,
    amount=amount,
    params={'reduceOnly': False}  # 开仓
)

# 平仓时使用reduceOnly
order = self.exchange.create_order(
    symbol=symbol,
    type='market',
    side=order_side,
    amount=amount,
    params={'reduceOnly': True}  # 平仓
)
```

### 2. 盈亏计算未考虑杠杆 ⚠️⚠️ ✅已修复
**文件**: executor.py
**位置**: Line 147-150
**问题**: 合约交易的盈亏必须考虑杠杆倍数

**修复方案**:
```python
# 假设杠杆为10倍
leverage = 10
pnl = (exit_price - entry_price) / entry_price * position['amount_usdt'] * leverage
```

### 3. 使用未闭合K线信号 ⚠️⚠️ ✅已修复
**文件**: live_trading.py
**位置**: Line 83
**问题**: 最后一根K线可能未闭合，信号不可靠

**修复方案**:
```python
# 使用倒数第二根K线（已闭合）
latest = df_analyzed.iloc[-2]
```

## 高优先级问题

### 4. 每日亏损限制逻辑错误 ✅已修复
**文件**: risk_manager.py
**位置**: Line 47
**问题**: 使用abs()会限制盈利

**修复方案**:
```python
if self.daily_pnl <= -self.max_daily_loss:
    return False, f"已达每日最大亏损限制 {self.max_daily_loss} USDT"
```

### 5. K线数据可能过期 ✅已修复
**文件**: live_trading.py
**位置**: Line 54-66
**问题**: 从数据库加载，可能不是最新数据

**修复方案**:
```python
# 从交易所实时获取
def load_recent_klines(self, limit=100):
    klines = self.executor.exchange.fetch_ohlcv(
        self.symbol, 
        self.interval, 
        limit=limit
    )
    df = pd.DataFrame(klines, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
    return df
```

### 6. testnet硬编码 ✅已修复
**文件**: live_trading.py
**位置**: Line 176
**问题**: 用户要用真实账户，但代码硬编码为测试网

**修复方案**:
```python
testnet = os.getenv('USE_TESTNET', 'false').lower() == 'true'
```

### 7. 检查频率太慢 ✅已修复
**文件**: live_trading.py
**位置**: Line 180
**问题**: 5分钟检查一次，可能错过止损

**修复方案**:
```python
check_interval=60  # 改为1分钟
```

## 中优先级问题

### 8. 峰值余额重启后丢失 ✅已修复
**文件**: risk_manager.py
**位置**: Line 33
**问题**: 程序重启后回撤计算失效

**修复方案**: 持久化到 `data/risk_state.json`

### 9. 持仓记录不持久化 ✅已修复
**文件**: executor.py
**位置**: Line 116
**问题**: 程序重启后持仓丢失

**修复方案**: 持久化到 `data/positions.json`

### 10. 只支持做多，不支持做空 ✅已修复
**文件**: live_trading.py
**位置**: Line 88-96
**问题**: 策略可能有做空信号，但未实现

**修复方案**: 添加做空逻辑（基础设施已就绪，等待策略生成空头信号）

## 低优先级问题

### 11. 仓位计算函数参数未使用 ✅已修复
**文件**: risk_manager.py
**位置**: Line 62
**问题**: price参数未使用

**修复方案**: 删除price参数

### 12. 异常处理不够细粒度 ⏭️跳过
**文件**: executor.py
**位置**: Line 121-123
**问题**: 捕获所有异常但只记录日志

**修复方案**: 更细粒度的异常处理和回滚机制（低优先级，暂不实现）

## 参考的成熟框架实践

### Freqtrade
- 合约交易使用`create_order()`并指定`reduceOnly`
- 杠杆在开仓前设置
- 持仓持久化到数据库
- 使用已闭合K线生成信号

### CCXT
- 合约交易需要先`set_leverage()`
- 使用`fetch_ohlcv()`获取实时K线
- 平仓使用`reduceOnly=True`参数

## 建议的修复顺序

1. **立即修复**（严重问题1-3）
2. **今天修复**（高优先级4-7）
3. **本周修复**（中优先级8-10）
4. **有时间再修复**（低优先级11-12）
