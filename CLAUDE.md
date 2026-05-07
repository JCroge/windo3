# Crypto Arbitrage System - AI协作指南

# Crypto Trading System - AI协作指南

## 项目概述

**目标**：加密货币趋势交易系统，基于技术分析和合约交易
**策略**：趋势跟踪 + 技术指标信号 + 反欺骗机制
**开发周期**：2026-05-06开始，MVP核心已完成

**重要变更（2026-05-06）**：
- 原方向：跨交易所套利
- 验证结果：所有测试0次机会，成本>收益
- 新方向：趋势交易 + 合约（更适合当前市场）
- MVP核心完成：技术指标、策略系统、回测引擎、样本外验证

**当前最佳策略参数（2026-05-06验证）**：
- 时间周期：1小时（1分钟/15分钟不盈利）
- MA快线：7，MA慢线：25
- RSI阈值：75
- 成交量因子：1.0
- 胜率：83.3%（训练集），100%（测试集）
- 盈亏比：7.68

## 项目结构

```
crypto-arbitrage/
├── core/                  # 套利系统（已归档）
│   ├── aggregator.py      # 行情聚合器
│   └── detector.py        # 套利发现引擎
├── utils/
│   ├── database.py        # SQLite数据库
│   └── logger.py          # 日志系统
├── indicators.py          # ✅ 技术指标计算
├── strategy_base.py       # ✅ 策略基类（Freqtrade架构）
├── strategy_trend.py      # ✅ 基础趋势策略
├── optimize_1h.py         # ✅ 稳健策略+参数优化
├── backtest.py            # ✅ 回测引擎
├── compare_timeframes.py  # ✅ 多时间周期对比
├── validate_out_of_sample.py  # ✅ 样本外验证
├── risk_manager.py        # ✅ 风控管理器
├── executor.py            # ✅ 合约执行器
├── live_trading.py        # ✅ 实时交易系统
├── test_backtest.py       # ✅ 回测测试
├── verify_system.py       # ✅ 系统完整性验证
├── verify_trading_flow.py # ✅ 交易Flow验证
├── verify_okx_real.py     # ✅ OKX真实账户验证
├── kline_collector.py     # ✅ K线数据采集器
├── data/
│   ├── market.db          # 套利数据（已归档）
│   ├── klines.db          # ✅ K线数据
│   ├── risk_state.json    # ✅ 风控状态持久化
│   └── positions.json     # ✅ 持仓记录持久化
├── docs/                  # 文档
│   ├── architecture.md
│   ├── handoff.md
│   ├── integration-guide.md
│   └── runbook.md
├── logs/                  # 日志文件
├── ISSUES.md              # ✅ 问题清单（11/12已修复）
├── config.yaml            # 系统配置
├── .env                   # API密钥（不提交）
└── main.py                # 主程序入口
```

## 核心约束

### 风控参数（不可突破）
- 单次最大交易额：10 USDT
- 最大回撤：20%
- 每日最大亏损：50 USDT

### 交易所
- 主要：Binance + OKX
- 初始交易对：ETH/USDT
- 手续费：0.1%（Maker/Taker）

### 套利阈值
- 最小利润率：0.3%（扣除手续费后）
- 检查间隔：1秒

## 环境变量

| 变量名 | 说明 | 必需 |
|--------|------|------|
| EXCHANGE | 交易所选择（binance/okx） | 是 |
| BINANCE_API_KEY | Binance API密钥 | 否（使用Binance时必需） |
| BINANCE_SECRET | Binance Secret | 否（使用Binance时必需） |
| OKX_API_KEY | OKX API密钥 | 否（使用OKX时必需） |
| OKX_SECRET | OKX Secret | 否（使用OKX时必需） |
| OKX_PASSWORD | OKX密码（Passphrase） | 否（使用OKX时必需） |
| USE_TESTNET | 是否使用测试网（true/false） | 否（默认false） |
| LEVERAGE | 杠杆倍数 | 否（默认1） |
| MAX_TRADE_AMOUNT | 单次最大交易额 | 否（默认10） |
| MAX_DRAWDOWN | 最大回撤 | 否（默认0.20） |

## 开发阶段

### ✅ Phase 1: 数据基础（2026-05-06完成）
- 行情聚合器（Binance + OKX）
- 套利检测引擎
- SQLite数据存储
- 日志系统
- K线数据采集器（WebSocket）

### ✅ Phase 2: MVP核心系统（2026-05-06完成）
- 技术指标计算（MA、MACD、RSI、布林带）
- 策略基类设计（参考Freqtrade）
- 稳健策略实现（4重入场确认）
- 回测引擎（防前视偏差）
- 多时间周期测试
- 参数优化
- 样本外验证

**关键成果**：
- 发现1小时周期最优（1分钟/15分钟不盈利）
- 反欺骗机制使胜率从46.67%提升至83.3%
- 样本外验证通过，策略稳健
- 最佳参数：MA 7/25，RSI阈值75，成交量因子1.0

### ✅ Phase 3: 实盘交易系统（2026-05-06完成）
- 风控管理器（余额/回撤/每日亏损限制，峰值余额持久化）
- 合约执行器（CCXT统一接口，支持Binance/OKX，持仓持久化）
- 实时交易系统（整合策略+执行+风控）
- 止损止盈自动触发
- 多空双向交易支持
- 实时K线获取（含数据库降级）
- 系统完整性验证（15/16测试通过）
- OKX真实账户连接验证

**关键修复**（参考Freqtrade/CCXT最佳实践）：
- 合约交易实现：杠杆设置、reduceOnly参数、盈亏计算含杠杆
- 使用已闭合K线（iloc[-2]）防止前视偏差
- 风控逻辑：只限制亏损不限制盈利
- 持久化：峰值余额和持仓记录重启不丢失

### 🔄 Phase 4: 实盘运行与优化（下一阶段）
- 实盘交易监控
- 性能数据收集
- 策略参数动态优化
- 交易复盘分析

## 技术栈

- **数据获取**：ccxt 4.3+
- **数据处理**：pandas 2.0+
- **数据库**：SQLite3
- **异步IO**：asyncio
- **配置管理**：pyyaml, python-dotenv

## 运行命令

```bash
# 测试连接
python3 test_connection.py

# 启动系统
python3 main.py

# 或使用启动脚本
./start.sh
```

## 已知问题

1. **ETH/USDT价差过小**：Binance和OKX之间价差<0.01%，需要寻找波动更大的币种
2. **币种选择**：需要开发自动筛选机制，目标是24h交易量1000万-1亿美元、波动率>2%的币种

## 开发注意事项

- 所有时间使用UTC
- 日志文件按日期分割
- 数据库自动创建表结构
- 配置文件修改后需重启
- API密钥为空时仅获取公开行情数据
