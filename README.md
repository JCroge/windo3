# Crypto Trading System

**重要变更（2026-05-06）**：原套利策略经全面验证不可行，已转向趋势交易+合约策略。

## 快速开始

### 1. 安装依赖
```bash
cd crypto-arbitrage
pip install -r requirements.txt
```

### 2. 配置API密钥（可选）
复制 `.env.example` 为 `.env` 并填入你的API密钥：
```bash
cp .env.example .env
```

编辑 `.env` 文件，填入真实的API密钥（K线采集无需API密钥）。

### 3. 运行K线采集
```bash
python3 test_kline.py
```

## 当前功能（2026-05-06）

✅ K线数据实时采集（WebSocket）
✅ SQLite数据存储
✅ 多币种、多周期支持

## MVP开发计划（1-2周）

- [x] K线数据采集
- [ ] 技术指标计算（MA, MACD, RSI, 布林带）
- [ ] 信号生成器（趋势识别、买卖信号）
- [ ] 回测引擎
- [ ] 合约执行 + 风控系统

## 配置说明

风控参数（硬限制）：
- `MAX_TRADE_AMOUNT`: 单次最大交易额（默认10 USDT）
- `MAX_DRAWDOWN`: 最大回撤（默认20%）

## 文档

- [项目交接文档](docs/handoff.md) - 项目状态和决策记录
- [系统架构](docs/architecture.md) - 技术架构和模块设计
- [运维手册](docs/runbook.md) - 部署和故障排查
- [集成指南](docs/integration-guide.md) - API和扩展开发
- [AI协作指南](CLAUDE.md) - AI开发协作规范

## 套利系统归档

原套利系统代码保留作为参考，但已验证不可行（2026-05-06全面测试，0次机会）。
