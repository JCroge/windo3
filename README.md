# Crypto Trading System

加密货币趋势交易系统，基于技术分析、合约执行、风控闭环和多 Agent 协作决策。

## 系统状态

- 最新基线（2026-05-27）：`618 passed / 4 deselected / 1 warning`，含 partial TP lifecycle 32 个、Long Entry Position Guard 23 个、R:R Floor Policy 20 个、OKX posMode 38 个 case。
- live 状态：OKX 实盘 paper+live 双轨在跑，逻辑账户拆分 300 USDT。
- OKX 真实 testnet T0-T9 语义验收 2026-05-27 完成（7 PASS / 3 SKIP），live 扩容前置阻断已解除；最终报告 [OKX执行语义testnet验收报告_20260527_150518.md](docs/generated_reports/OKX执行语义testnet验收报告_20260527_150518.md)。下一步进入小额 24h 灰度观察 segmented metrics。

具体阈值与开关以启动 banner 为准（启动后看 `logs/launcher_*.log` 第一段），不要从 README 硬抄数字。

## 快速开始

```bash
cd crypto-arbitrage
pip install -r requirements.txt
cp .env.example .env          # 按注释填 OKX / Anthropic 凭证与风控参数
python3 run_agents.py         # 主入口（生产/paper/testnet/实盘验收都走这个）
```

`live_trading.py` 与 `main.py` 已 deprecated，仅保留作单策略调试参考。

## 系统能力

- 多 Agent 两层架构（研判层 6 + 交易层 10）
- 研判层：定时全市场扫描 + 情绪 / 新闻 / 言官 / 标的路由
- 交易层：9 维度数据采集 → 规则+LLM 综合研判 → 精确开仓计划 → CCXT 合约执行
- 风控闭环：动态杠杆 + 动态 R:R floor + EV 门 + RSI 极端值保护 + Daily Hard Stop + 组合级 RiskGuard
- 持仓管理：PositionAnalyst 7 因子 + BehavioralCritic 偏差检测 + 裁决引擎
- PaperExecutor 影子账户与 live 信号并行（不下真单，独立 topic 隔离）
- Telegram 远程命令：`/status` `/positions` `/stop` `/restart` `/halt` `/resume` `/log`
- LLM 不可用时自动降级为规则引擎；事件 journal + LLM audit 可观测

## 配置入口

所有可调参数都在 [`.env.example`](.env.example) 注释里描述。常用：

| 类别 | 关键变量 | 说明 |
|---|---|---|
| 凭证 | `OKX_API_KEY` / `OKX_SECRET` / `OKX_PASSWORD` / `ANTHROPIC_API_KEY` | live 模式下缺失会 fail-closed |
| 模式 | `USE_TESTNET` | `false=live`，`true=testnet` |
| 仓位 | `MAX_TRADE_AMOUNT` / `LEVERAGE` / `EFFECTIVE_BALANCE_CAP` | 单笔保证金 / 默认杠杆 / 逻辑账户拆分 |
| 风控 | `MAX_DRAWDOWN_PCT` / `MAX_DAILY_LOSS` / `DAILY_PNL_HARD_STOP` | 回撤上限 / 每日硬熔断 |
| R:R | `RR_FLOOR_DEFAULT` / `RR_FLOOR_LONG_BULLISH` / `RR_FLOOR_LONG_ALIGNED_CHOPPY` / `RR_FLOOR_SHORT_BULLISH` / `PROBE_RR_FLOOR` | R:R floor 五分支阈值 |
| 多头位置保护 | `LONG_LIVE_POSITION_GUARD_ENABLED` / `LONG_LIVE_MAX_RANGE_POS` / `LONG_LIVE_MAX_PRE_MOVE` / `LONG_LIVE_MAX_DAILY_GAIN` | 山顶接货防护，命中走 `deferred_pullback_overheat`（2026-05-26） |
| EV 分桶 | `EV_BUCKET_MIN_TRADES` / `EV_BUCKET_SPARSE_ALLOW_UPLIFT` | 稀疏 bucket 不抬 p_win（2026-05-26） |

完整列表与默认值见 `utils/config_loader.py` 的 `DEFAULTS` 与 `HARD_LIMITS`。

## 常用验证

```bash
python3 -m pytest -q                       # 默认回归（基线 618 passed）
python3 -m pytest -q -m network            # 真实 OKX/Telegram 冒烟
python3 verify_okx_testnet_semantics.py    # OKX mock 验收 10 case
python3 verify_okx_testnet_real.py         # OKX 真实 testnet T0-T9 验收（需 .env.testnet）
```

更细的核心链路 / 收益验证 / 真实环境冒烟命令见 [docs/runbook.md](docs/runbook.md)。

## 文档

| 文档 | 用途 |
|---|---|
| [docs/development.md](docs/development.md) | 修改规范、链路契约、验证矩阵 |
| [docs/architecture.md](docs/architecture.md) | 技术架构与模块设计 |
| [docs/runbook.md](docs/runbook.md) | 部署、环境变量、故障排查 |
| [docs/integration-guide.md](docs/integration-guide.md) | 消息契约与下游接入 |
| [docs/handoff.md](docs/handoff.md) | 项目交接与决策记录 |
| [docs/to-do-list.md](docs/to-do-list.md) | 当前阻断项、后续优化、已关闭事项 |
| [docs/generated_reports/](docs/generated_reports/) | 系统性审计报告归档（最新 2026-05-24）+ OKX testnet 验收（2026-05-27 PASS） |
| [docs/okx_posmode_execution_*.md](docs/) | OKX posMode 执行兼容 PRD + 验收 |
| [docs/rr_floor_policy_*.md](docs/) | R:R Floor Policy 修复 PRD + 验收（2026-05-26） |
| [docs/long_entry_position_guard_*.md](docs/) | Long Entry Position Guard PRD + 验收（2026-05-26） |
| [docs/drawdown_baseline_*.md](docs/) | 回撤基准 PRD + 验收 |
| [docs/live_readiness_*.md](docs/) | live 准备度 PRD + 验收 |
| [docs/audit_remediation_*.md](docs/) | 审计整改 PRD + 验收 |
| [CLAUDE.md](CLAUDE.md) | AI 协作指南、当前事实、目录职责、消息契约红线 |

## 开发约束

- 跨 Agent 消息里的 symbol 使用内部格式 `BASE-USDT`；交易所 API 调用现场转换。
- `trade_decision.plan.size_usdt` 表示保证金，名义价值为 `size_usdt * leverage`。
- open 主链路必须走 `trade_decision.v2`（带 `request_id` / `attribution` / `dispatch_path`）；Executor 终态发 `execution_result.v2`。
- LLM 只作为辅助信号，不能绕过规则、R:R、EV、余额、熔断和下单预检。
- 修改 Judge / 策略公式必须同步 `event_backtest.py` 同构验证，不能只看 mock 单测。
- 配置或代码变更后可直接使用 Telegram `/restart`；launcher 会在优雅停机后 `os.execv(...)` 置换解释器镜像并重新 import 模块。若升级 Python/venv/系统级依赖，仍建议外部 supervisor 或 OS 层重启。
- 关键状态 JSON 使用原子写；不要删除或覆盖用户已有 `data/` 和 `logs/`。

## 套利系统归档

原 CEX 套利代码保留作参考，但已验证不可行（2026-05-06 全面测试 0 次机会）；当前系统是趋势交易，不是套利。
