# Crypto Arbitrage System - AI协作指南

## 项目概述

**目标**：CEX跨交易所套利系统，自动发现并执行价差交易
**策略**：短线套利，捕捉Binance和OKX之间的价格差异
**开发周期**：2026-05-06开始，计划一周完成MVP

## 项目结构

```
crypto-arbitrage/
├── core/
│   ├── aggregator.py      # 行情聚合器
│   └── detector.py        # 套利发现引擎
├── utils/
│   ├── database.py        # SQLite数据库
│   └── logger.py          # 日志系统
├── agents/                # AI Agent模块（待开发）
│   └── prompts/          # Agent提示词
├── data/                  # 数据存储
│   └── market.db         # SQLite数据库文件
├── logs/                  # 日志文件
├── config.yaml           # 系统配置
├── .env                  # API密钥（不提交）
└── main.py              # 主程序入口
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
| BINANCE_API_KEY | Binance API密钥 | 否（只读行情可选） |
| BINANCE_SECRET | Binance Secret | 否 |
| OKX_API_KEY | OKX API密钥 | 否 |
| OKX_SECRET | OKX Secret | 否 |
| OKX_PASSWORD | OKX密码 | 否 |
| MAX_TRADE_AMOUNT | 单次最大交易额 | 否（默认10） |
| MAX_DRAWDOWN | 最大回撤 | 否（默认0.20） |

## 开发阶段

### ✅ Phase 1: 数据基础（2026-05-06完成）
- 行情聚合器（Binance + OKX）
- 套利检测引擎
- SQLite数据存储
- 日志系统

### 🔄 Phase 2: 智能研判（2026-05-07-08计划）
- 币种研判Agent
- 多币种监控
- 历史数据分析

### ⏳ Phase 3: 执行系统（2026-05-09-10计划）
- 订单执行模块
- 风控引擎
- 实盘测试

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
