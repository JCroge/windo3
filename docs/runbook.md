# 运维手册

## 快速启动

### 环境要求
- Python 3.9+
- pip3

### 安装步骤

```bash
cd crypto-arbitrage
pip3 install -r requirements.txt
```

### 配置

1. 复制环境变量模板：
```bash
cp .env.example .env
```

2. 编辑`.env`填入API密钥（可选，只读行情无需密钥）

### 启动系统

**当前可用（2026-05-06）**：
```bash
# K线数据采集
python3 test_kline.py
```

**原套利系统（已归档）**：
```bash
# 方式1：直接运行
python3 main.py

# 方式2：使用启动脚本
./start.sh
```

**注意**：趋势交易系统正在开发中，当前只有K线采集功能可用。

## 环境变量

| 变量 | 说明 | 默认值 | 必需 |
|------|------|--------|------|
| BINANCE_API_KEY | Binance API密钥 | - | 否 |
| BINANCE_SECRET | Binance Secret | - | 否 |
| OKX_API_KEY | OKX API密钥 | - | 否 |
| OKX_SECRET | OKX Secret | - | 否 |
| OKX_PASSWORD | OKX密码 | - | 否 |
| MAX_TRADE_AMOUNT | 单次最大交易额(USDT) | 10 | 否 |
| MAX_DRAWDOWN | 最大回撤比例 | 0.20 | 否 |

## 配置文件

### config.yaml

```yaml
# 交易所
exchanges:
  - binance
  - okx

# 交易对
symbols:
  - ETH/USDT

# 套利参数
arbitrage:
  min_profit_rate: 0.003  # 最小利润率0.3%
  check_interval: 1       # 检查间隔(秒)

# 风控
risk:
  max_trade_amount: 10    # 单次最大交易额
  max_drawdown: 0.20      # 最大回撤20%
  max_daily_loss: 50      # 每日最大亏损

# 手续费
fees:
  binance: 0.001
  okx: 0.001
```

## 监控

### 查看日志

```bash
# 实时查看K线采集日志
tail -f logs/kline_collector_$(date +%Y%m%d).log

# 查看原套利系统日志（已归档）
tail -f logs/main_$(date +%Y%m%d).log
tail -f logs/aggregator_$(date +%Y%m%d).log
tail -f logs/detector_$(date +%Y%m%d).log
```

### 检查数据库

**K线数据库（当前使用）**：
```bash
sqlite3 data/klines.db

# 查看最新K线数据
SELECT symbol, datetime(open_time/1000, 'unixepoch') as time, open, high, low, close, volume 
FROM klines ORDER BY open_time DESC LIMIT 10;

# 统计数据量
SELECT symbol, interval, COUNT(*) as count FROM klines GROUP BY symbol, interval;

# 退出
.quit
```

**原套利数据库（已归档）**：
```bash
sqlite3 data/market.db

# 查看最新行情
SELECT * FROM tickers ORDER BY timestamp DESC LIMIT 10;

# 查看交易记录
SELECT * FROM trades ORDER BY timestamp DESC;

# 退出
.quit
```

## 故障排查

### 问题：K线数据未采集

**症状**：数据库中无K线数据

**排查**：
```bash
# 检查K线采集进程
ps aux | grep test_kline.py

# 查看日志
tail -f logs/kline_collector_$(date +%Y%m%d).log
```

**可能原因**：
- WebSocket连接失败
- 网络问题
- Binance API限流

**解决**：
```bash
# 重启K线采集
python3 test_kline.py
```

### 问题：无法连接交易所

**症状**：日志显示连接错误

**排查**：
```bash
# 测试连接
python3 test_connection.py
```

**可能原因**：
- 网络问题
- 交易所API限流
- ccxt版本过旧

**解决**：
```bash
# 升级ccxt
pip3 install --upgrade ccxt
```

### 问题：套利系统未发现机会（已归档）

**说明**：2026-05-06全面验证后，套利策略已放弃。所有测试显示0次机会，市场效率极高，成本>收益。

**历史排查方法**（仅供参考）：
1. 检查当前价差
2. 调整最小利润率
3. 切换到波动更大的币种

**结论**：套利策略不可行，已转向趋势交易+合约策略。

### 问题：数据库锁定

**症状**：`database is locked`错误

**解决**：
```bash
# 停止所有运行实例
pkill -f main.py

# 重启
python3 main.py
```

## 维护

### 清理日志

```bash
# 删除7天前的日志
find logs/ -name "*.log" -mtime +7 -delete
```

### 备份数据

```bash
# 备份K线数据库
cp data/klines.db data/klines_$(date +%Y%m%d).db.bak

# 备份原套利数据库（已归档）
cp data/market.db data/market_$(date +%Y%m%d).db.bak
```

### 更新依赖

```bash
pip3 install --upgrade -r requirements.txt
```

## 性能优化

### 降低延迟
- 减少`check_interval`（最小0.5秒）
- 使用更快的网络

### 减少API调用
- 增加`check_interval`
- 减少监控的交易对数量

## 安全建议

1. **API密钥权限**：只开启读取和交易权限，禁用提现
2. **IP白名单**：在交易所设置IP白名单
3. **密钥轮换**：定期更换API密钥
4. **监控异常**：设置告警，监控异常交易
