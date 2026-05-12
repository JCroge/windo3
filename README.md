# Crypto Trading System

加密货币趋势交易系统，基于技术分析 + 合约交易 + 15个AI Agent协作决策。

**系统状态（2026-05-12）**：Phase 6i 完成，持仓管理三角决策上线。

## 快速开始

### 1. 安装依赖
```bash
cd crypto-arbitrage
pip install -r requirements.txt
```

### 2. 配置API密钥
```bash
cp .env.example .env
# 编辑 .env，填入 OKX_API_KEY / OKX_SECRET / OKX_PASSWORD
# 多Agent系统还需填入 ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL
```

### 3. 启动系统

**多Agent交易系统（推荐）**：
```bash
python3 run_agents.py
```

**单策略实盘交易**：
```bash
python3 live_trading.py
```

## 系统能力

✅ 15个AI Agent两层架构（研判层6个 + 交易层9个）  
✅ 研判层每4h自动扫描全市场选币（OKX 333合约）  
✅ 交易层9维度数据采集 + Claude综合研判 + 精确交易计划  
✅ 动态杠杆1-20x + R:R门槛≥1.5 + RSI极端值保护  
✅ 持仓管理三角决策：6因子评分 + 行为偏差检测 + 裁决引擎（每1h）  
✅ 反欺骗机制：胜率83.3%（4重入场确认）  
✅ 风控：Daily Hard Stop + 组合级RiskGuard + Telegram实时告警  
✅ LLM不可用时自动降级为规则引擎  

## 风控参数（硬限制）

- 单次最大交易额：10 USDT
- 最大回撤：20%
- 每日最大亏损：50 USDT

## 文档

- [项目交接文档](docs/handoff.md) - 项目状态和决策记录
- [系统架构](docs/architecture.md) - 技术架构和模块设计
- [运维手册](docs/runbook.md) - 部署和故障排查
- [集成指南](docs/integration-guide.md) - API和扩展开发
- [AI协作指南](CLAUDE.md) - AI开发协作规范

## 套利系统归档

原套利系统代码保留作为参考，但已验证不可行（2026-05-06全面测试，0次机会）。
