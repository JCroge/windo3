# Crypto Arbitrage System

## 快速开始

### 1. 安装依赖
```bash
cd crypto-arbitrage
pip install -r requirements.txt
```

### 2. 配置API密钥
复制 `.env.example` 为 `.env` 并填入你的API密钥：
```bash
cp .env.example .env
```

编辑 `.env` 文件，填入真实的API密钥。

### 3. 运行系统
```bash
python main.py
```

## 当前功能（Day 1-2）

✅ 实时获取Binance和OKX的ETH/USDT行情
✅ 自动检测跨交易所套利机会
✅ 数据存储到本地SQLite数据库
✅ 日志记录

## 下一步（Day 3-4）

- [ ] 币种研判Agent
- [ ] 执行模块
- [ ] 风控系统

## 配置说明

编辑 `config.yaml` 调整参数：
- `min_profit_rate`: 最小利润率（默认0.3%）
- `max_trade_amount`: 单次最大交易额（默认10 USDT）
- `max_drawdown`: 最大回撤（默认20%）
