# Crypto Trading System

加密货币趋势交易系统，基于技术分析、合约执行、风控闭环和多 Agent 协作决策。

**系统状态（2026-05-25）**：OKX posMode 执行兼容代码已落地（基线 `531 passed / 4 deselected / 1 warning`，含 38 个 posMode 单测；mock 验收 10 case PASS）。系统可继续 paper/mock 和既有小额 live 灰度观察；OKX 真实 testnet 端到端验收（T0-T9）未执行，阻断 live 扩容。

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

**多 Agent 交易系统（主入口）**：
```bash
python3 run_agents.py
```

`live_trading.py` 已标记为 deprecated，只保留作单策略调试参考。生产、paper、testnet、实盘验收都应走 `run_agents.py`。

## 系统能力

✅ 多 Agent 两层架构（研判层 + 交易层）  
✅ 研判层定时扫描全市场选币，并支持空闲提前研判  
✅ 交易层 9 维度数据采集 + 规则/LLM 综合研判 + 精确交易计划  
✅ 动态杠杆 1-20x + R:R / EV 门 + RSI 极端值保护  
✅ PositionAnalyst 持仓管理：7因子评分 + 行为偏差检测 + 裁决引擎  
✅ PaperExecutor 影子账户，与实盘信号并行但不下真单  
✅ Telegram远程命令：/status /positions /stop /restart /halt /resume /log  
✅ 风控：Daily Hard Stop + 组合级RiskGuard + Telegram实时告警  
✅ LLM不可用时自动降级为规则引擎  

## 常用验证

默认回归：
```bash
python3 -m pytest -q
```

核心链路：
```bash
python3 test_full_pipeline.py
python3 test_executor_upgrade.py
python3 test_p1m_order_caps.py
python3 test_llm_schema.py
python3 test_paper_executor.py
python3 test_risk_budget.py
```

收益验证：
```bash
python3 test_event_backtest.py
python3 test_event_backtest_real_data.py
python3 test_p2p3_grid_search.py
```

真实环境冒烟：
```bash
python3 test_full_verification.py
```

说明：默认 pytest 排除 `network` 标记的外部依赖测试；真实 OKX/Telegram 冒烟依赖本机网络和凭证。

## 风控参数（当前实盘配置）

- 单笔最大保证金：30 USDT（`MAX_TRADE_AMOUNT=30`）
- 逻辑账户拆分：300 USDT（`EFFECTIVE_BALANCE_CAP=300`）
- 最大回撤：20%
- 每日最大亏损：300 USDT（`daily_pnl_hard_stop=-300`）

## 文档

- [系统开发文档](docs/development.md) - 后续修改规范、链路契约、验证矩阵
- [项目交接文档](docs/handoff.md) - 项目状态和决策记录
- [系统架构](docs/architecture.md) - 技术架构和模块设计
- [运维手册](docs/runbook.md) - 部署和故障排查
- [集成指南](docs/integration-guide.md) - API和扩展开发
- [系统性审计报告](docs/generated_reports/系统性审计报告_20260524.md) - 2026-05-24 全链路审计结论
- [OKX posMode 执行兼容 PRD](docs/okx_posmode_execution_prd.md) / [验收文档](docs/okx_posmode_execution_acceptance.md) - 51169/51205 修复方案与 testnet 验收矩阵
- [To-Do List](docs/to-do-list.md) - 当前阻断项、后续优化和已关闭事项
- [AI协作指南](CLAUDE.md) - AI开发协作规范

## 开发约束

- 跨 Agent 消息里的 symbol 使用内部格式 `BASE-USDT`；交易所 API 调用现场转换。
- `trade_decision.plan.size_usdt` 表示保证金，名义价值为 `size_usdt * leverage`。
- 所有下单路径必须经过订单能力预检、幂等防护和风控检查。
- LLM 只作为辅助信号，不能绕过规则、EV、余额、熔断和下单预检。
- 修改 Judge / 策略公式必须同步事件回测，不能只看 mock 单测。
- 关键状态 JSON 使用原子写；不要删除或覆盖用户已有 `data/` 和 `logs/`。

## 套利系统归档

原套利系统代码保留作为参考，但已验证不可行（2026-05-06全面测试，0次机会）。
