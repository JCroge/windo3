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

```bash
# 方式1：直接运行
python3 main.py

# 方式2：使用启动脚本
./start.sh
```

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
# 实时查看主日志
tail -f logs/main_$(date +%Y%m%d).log

# 查看行情聚合日志
tail -f logs/aggregator_$(date +%Y%m%d).log

# 查看检测引擎日志
tail -f logs/detector_$(date +%Y%m%d).log
```

### 检查数据库

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

### 问题：未发现套利机会

**症状**：长时间运行无套利信号

**排查**：
1. 检查当前价差：
```bash
python3 -c "
import ccxt
b = ccxt.binance().fetch_ticker('ETH/USDT')
o = ccxt.okx().fetch_ticker('ETH/USDT')
print(f'Binance: {b[\"bid\"]}/{b[\"ask\"]}')
print(f'OKX: {o[\"bid\"]}/{o[\"ask\"]}')
print(f'价差: {abs(b[\"last\"]-o[\"last\"])/b[\"last\"]*100:.4f}%')
"
```

2. 调整最小利润率：
编辑`config.yaml`，降低`min_profit_rate`（如0.001）

**原因**：
- ETH/USDT流动性极好，价差小
- 需要切换到波动更大的币种

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
# 备份数据库
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
