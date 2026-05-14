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

2. 编辑`.env`填入API密钥

### 启动系统

**单策略实盘交易**：
```bash
python3 live_trading.py
```

**多Agent交易系统**：
```bash
python3 run_agents.py
```

**后台运行**：
```bash
nohup python3 live_trading.py &
nohup python3 run_agents.py &
```

**查看实时日志**：
```bash
tail -f logs/live_trading_$(date +%Y%m%d).log
tail -f logs/orchestrator_$(date +%Y%m%d).log
```

**停止系统（优雅停机）**：
```bash
# 发送SIGINT/SIGTERM，系统会自动保存状态后退出
kill -SIGINT $(pgrep -f run_agents.py)
# 或直接 Ctrl+C（前台运行时）
# 或通过Telegram发送 /stop 命令
```

**远程重启**：
```bash
# 通过Telegram发送 /restart 命令
# 系统写入 data/.restart_flag 后优雅退出，run_agents.py 检测标记后自动重启
```

**Telegram远程命令**（需配置TELEGRAM_BOT_TOKEN和TELEGRAM_CHAT_ID）：
| 命令 | 功能 |
|------|------|
| `/status` | 运行时长、持仓数、熔断状态、今日PnL |
| `/positions` | 每个持仓的方向/杠杆/入场价/SL/TP |
| `/stop` | 优雅退出 |
| `/restart` | 优雅退出后自动重启 |
| `/halt` | 手动熔断（停止新交易，保留持仓） |
| `/resume` | 解除熔断 |
| `/log` | 最近10条关键日志 |

**强制停止**：
```bash
pkill -f live_trading.py
pkill -f run_agents.py
```

**系统验证**：
```bash
python3 verify_system.py          # 基础验证（9项）
python3 verify_trading_flow.py    # 交易Flow验证（7项）
python3 verify_okx_real.py        # OKX真实账户验证（5项）
python3 test_agents_integration.py  # 多Agent集成测试
python3 test_phase_c.py           # 研判层→交易层流水线测试
python3 test_data_sources.py      # 研判层数据源验证（实时API）
python3 test_p0_features.py       # P0功能测试（Reviewer/Hard Stop/Graceful Shutdown）
```

## 环境变量

| 变量 | 说明 | 默认值 | 必需 |
|------|------|--------|------|
| EXCHANGE | 交易所（binance/okx） | binance | 是 |
| OKX_API_KEY | OKX API密钥 | - | 是（OKX） |
| OKX_SECRET | OKX Secret | - | 是（OKX） |
| OKX_PASSWORD | OKX Passphrase | - | 是（OKX） |
| BINANCE_API_KEY | Binance API密钥 | - | 是（Binance） |
| BINANCE_SECRET | Binance Secret | - | 是（Binance） |
| USE_TESTNET | 是否测试网 | false | 否 |
| LEVERAGE | 杠杆倍数 | 1 | 否 |
| MAX_TRADE_AMOUNT | 单次最大交易额(USDT) | 10 | 否 |
| MAX_DRAWDOWN | 最大回撤比例 | 0.20 | 否 |
| ANTHROPIC_API_KEY | Claude API密钥 | - | 否（多Agent系统） |
| ANTHROPIC_BASE_URL | Claude API地址（中转） | https://api.anthropic.com | 否 |
| ANTHROPIC_MODEL | Claude模型名 | claude-sonnet-4-6 | 否 |
| RESEARCH_INTERVAL | 研判层运行周期（秒） | 14400 (4h) | 否 |
| TELEGRAM_BOT_TOKEN | Telegram Bot Token | - | 否（通知） |
| TELEGRAM_CHAT_ID | Telegram Chat ID | - | 否（通知） |

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
# 实时查看实盘交易日志
tail -f logs/live_trading_$(date +%Y%m%d).log

# 实时查看K线采集日志
tail -f logs/kline_collector_$(date +%Y%m%d).log
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

### 问题：correlation_risk误报导致持续减仓

**症状**：日志频繁出现 `[风控] 同多/空方向敞口XX > 20`，持仓被反复减半

**根因**：旧版本用名义价值（amount_usdt）计算敞口，4 USDT×20x=80 USDT远超20 USDT阈值

**状态**：2026-05-09已修复，现用保证金（amount_usdt/leverage）计算，无需人工干预

---

### 问题：强平后立即重开同方向

**症状**：日志显示force_close后几秒内又出现open_long/open_short同标的

**根因**：旧版本Judge无force_close记忆

**状态**：2026-05-09已修复，force_close后300s冷却期，无需人工干预

---

### 问题：OKX下单错误

**错误51008：余额不足**
- 原因：账户USDT余额不足以开仓
- 处理：系统自动调整仓位大小或放弃本次交易，无需人工干预

**错误51020：订单金额低于最小值**
- 原因：计算出的下单数量低于OKX合约最小数量限制
- 处理：系统自动放弃本次交易，无需人工干预

**错误11045：设置杠杆失败**
- 原因：偶发性API错误，通常不影响后续交易
- 处理：忽略，系统继续运行；如持续出现，检查账户是否有未平仓持仓

**OKX下单数量计算公式**：
```
market = exchange.market(symbol)
contract_size = market.get('contractSize', 1)
amount = (size_usdt * leverage) / (price * contract_size)
amount = exchange.amount_to_precision(symbol, amount)
```
OKX允许的杠杆值：[1, 2, 3, 5, 10, 20]

### 问题：Claude API "Your request was blocked"

**症状**：多Agent系统日志显示 `LLM调用失败: Your request was blocked`

**原因**：Cloudflare Bot防护拦截了SDK默认的User-Agent

**解决**：
- `llm_client.py` 已内置修复（设置 `User-Agent: curl/8.0`）
- 如果仍然失败，检查中转站账户分组是否有对应模型的通道
- 系统会自动降级为规则引擎，不影响交易

**验证**：
```bash
curl -s https://www.dorocli.cc/v1/chat/completions \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY" \
  -H "content-type: application/json" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":20,"messages":[{"role":"user","content":"hi"}]}'
```

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
