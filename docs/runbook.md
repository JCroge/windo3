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

**多 Agent 交易系统（主入口）**：
```bash
python3 run_agents.py
```

`live_trading.py` 已废弃，只保留作单策略调试参考；生产、paper、testnet、实盘验收都走 `run_agents.py`。

**后台运行**：
```bash
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
| `/resume` | 对账通过后解除熔断 |
| `/force_resume` | 跳过对账强制解除熔断 |
| `/reconcile` | 执行持仓对账 |
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
python3 test_4h_rsi_decay.py      # 4h RSI 二级保护衰减
python3 test_logical_account_split.py  # effective_balance_cap 逻辑账户拆分
python3 test_paper_executor.py    # PaperExecutor 影子账户（open/close/SL/TP/halt/persist/PnL）
python3 -m pytest test_drawdown_baseline.py  # 回撤基准修正验收（14 tests）

# 完整 CI 回归（默认排除 network 标记的外部数据测试）
python3 -m pytest -q              # 469 passed / 4 deselected / 1 warning（2026-05-23）
python3 -m pytest -q -m network   # 仅跑 network 测试（需 data/klines.db 和实时网络）
```

> conftest.py 通过 `monkeypatch.chdir(tmp_path)` 把 `data/` 和 `logs/` 隔离到临时目录，每个测试独立。
> pytest.ini 默认排除 `network` 标记的测试；网络冒烟需要显式 `-m network`。

## 数据持久化文件

| 文件 | 写入者 | 用途 | 备注 |
|------|--------|------|------|
| `data/positions.json` | ContractExecutor | 实盘持仓快照 | 重启恢复 |
| `data/risk_state.json` | RiskManager | 回撤基准（v2 schema：session_peak_equity/baseline_mode/legacy_peak_balance） | 重启不丢，启动时按 baseline_mode 决定是否重置 |
| `data/trade_history.json` | ReviewerAgent | 已平仓历史+策略衰减 | 缺失时空起 |
| `data/riskguard_state.json` | PortfolioRiskGuard | 持仓追踪/价格缓存/熔断状态 | 缺失时空起 |
| `data/judge_state.json` | MultiJudge | deferred_entry/sl_timestamps/cooldown | 缺失时空起，启动时清理过期条目 |
| `data/live_order_events.jsonl` | LiveLedger | 订单事件流（open/reduce/close） | append-only |
| `data/live_position_lifecycle.json` | LiveLedger | 持仓生命周期聚合 | 原子写入 |
| `data/paper_positions.json` | PaperExecutor | 影子持仓快照 | 缺失=从初始 equity 起 |
| `data/paper_equity.json` | PaperExecutor | 影子账户余额 | 首次启动=EFFECTIVE_BALANCE_CAP 或 1000 |
| `data/paper_trades.jsonl` | PaperExecutor | 影子已平仓 append-only 流水 | 与实盘 trade_history 互不影响 |
| `data/.restart_flag` | TelegramNotifier | 远程 /restart 标记 | run_agents.py 检测后重启 |

## 环境变量

| 变量 | 说明 | 默认值 | 必需 |
|------|------|--------|------|
| EXCHANGE | 交易所（binance/okx） | okx | 是 |
| OKX_API_KEY | OKX API密钥 | - | 是（OKX） |
| OKX_SECRET | OKX Secret | - | 是（OKX） |
| OKX_PASSWORD | OKX Passphrase | - | 是（OKX） |
| BINANCE_API_KEY | Binance API密钥 | - | 是（Binance） |
| BINANCE_SECRET | Binance Secret | - | 是（Binance） |
| USE_TESTNET | 是否测试网 | false | 否 |
| LEVERAGE | 杠杆倍数 | 3 | 否 |
| MAX_TRADE_AMOUNT | 单笔最大保证金（USDT） | 10 | 否 |
| MAX_DRAWDOWN_PCT | 最大回撤百分比 | 20.0 | 否 |
| MAX_DAILY_LOSS | 每日最大亏损（USDT，正数） | 50 | 否 |
| EFFECTIVE_BALANCE_CAP | 逻辑账户拆分：风控按此上限计算余额（真实余额不变）。留空=用真实余额。范围 [10, 1_000_000] | （未启用） | 否 |
| DRAWDOWN_BASELINE_MODE | 回撤基准模式：`session_start`=启动时重置基准（默认）；`persisted_peak`=继承历史峰值（兼容旧行为） | session_start | 否 |
| RESET_RISK_BASELINE_ON_START | 启动时是否重置本轮回撤基准 | true | 否 |
| ANTHROPIC_API_KEY | Claude API密钥 | - | 否（多Agent系统） |
| ANTHROPIC_BASE_URL | Claude API地址（中转） | https://api.anthropic.com | 否 |
| ANTHROPIC_MODEL | Claude模型名 | claude-opus-4-7 | 否 |
| RESEARCH_INTERVAL | 研判层运行周期（秒） | 14400 (4h) | 否 |
| RANKING_ENABLED | 是否启用候选 Top-N Ranking 裁决 | true | 否 |
| RANK_FLUSH_DELAY | Ranking flush 窗口秒数，等待同批候选到齐后统一排序。范围 [1, 30] | 5.0 | 否 |
| MAX_CONCURRENT_POSITIONS | 最大并发持仓数（同时开仓数量）。范围 [1, 20] | 3 | 否 |
| SHORT_LIVE_MIN_RSI | 空单入场最低RSI（防超卖追空） | 40 | 否 |
| SHORT_LIVE_MIN_RANGE_POS | 空单入场最低24h区间位置（防底部追空） | 0.45 | 否 |
| SHORT_LIVE_REQUIRE_DAILY_BEARISH | 空单是否要求日线偏空 | true | 否 |
| SHORT_LIVE_MAX_PRE_MOVE | 空单入场前12h最大跌幅（防追空） | -0.01 | 否 |
| PHASE2_SIGNAL_CONFIDENCE_SPLIT_ENABLED | Confidence Split：signal_score/execution_confidence/position_scale 三层拆分 | true | 否 |
| PHASE2_MOMENTUM_PROBE_LONG_ENABLED | Momentum Probe Long：RSI 70-85 强趋势追踪小仓位 | true | 否 |
| PHASE2_TREND_SATURATION_ENABLED | Trend Saturation：strength>90 cap + 4h RSI 动态衰减 | true | 否 |
| PHASE2_BUCKETED_EV_ENABLED | Bucketed EV：per side×regime×entry_type 分桶胜率 | true | 否 |
| TELEGRAM_BOT_TOKEN | Telegram Bot Token | - | 否（通知） |
| TELEGRAM_CHAT_ID | Telegram Chat ID | - | 否（通知） |

## 配置文件

### config.yaml

`load_config()` 优先级为 `.env` > `config.yaml` > 内置默认值。生产风险参数以 `.env` 为准，`config.yaml` 主要用于历史兼容和离线脚本。

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
  max_trade_amount: 500   # 单次最大保证金（会被 .env 的 MAX_TRADE_AMOUNT 覆盖）
  max_drawdown: 0.20      # 最大回撤20%
  max_daily_loss: 300     # 每日最大亏损

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

### 问题：Telegram重复通知

**症状**：同一平仓事件收到2-3条通知，或sync发现的持仓推送"做多 置信度0%"

**状态**：2026-05-15已修复，三层去重：
- sync发现的持仓（source=sync）不推送开仓通知
- 同symbol平仓通知60s内去重
- Executor close冷却60s防止sync重新发现已平仓位

---

### 问题：PA误平持仓（高杠杆）

**症状**：持仓浮亏未到SL但被PA强制平仓

**根因**：旧版本PA Rule 1用固定15%阈值，10x杠杆下原价差1.5%被计算为15%（含杠杆）

**状态**：2026-05-15已修复，Rule 1阈值=SL含杠杆距离（动态），只在交易所SL+Executor轮询都失败时触发

---

### 问题：同标的重复开仓

**症状**：同一标的在短时间内（几秒~几分钟）被连续开仓2-3次

**根因**：symbol格式不一致（`ZEC-USDT` vs `ZEC-USDT-SWAP`）导致Judge冷却设在错误key上

**状态**：2026-05-15已修复。Judge/PA/RiskGuard的execution_result handler入口统一strip `-SWAP`后缀，deferred_entry触发后立即设冷却。如仍复现，检查是否有新的消息路径绕过了strip逻辑。

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
  -d '{"model":"claude-opus-4-7","max_tokens":20,"messages":[{"role":"user","content":"hi"}]}'
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
