# Crypto Trading System

加密货币趋势交易系统，基于技术分析、合约执行、风控闭环和多 Agent 协作决策。

## 系统状态

- **当前线上执行路径（2026-08-06）**：Tactical V2 已在云服 `LIVE`，固定 `100U x 3`；当前快照为 `0 active / 0 pending / 3 free`、无 integrity halt、protection/reconciliation `verified`。Sidecar 保留 resident monitor，但 `admission_enabled=false`，不再新开仓。
- V2 固定约束：最多 5x、`0.10R` 追价上限、900 秒 frozen-entry 限价、全仓 TP1/SL、90 分钟 max-hold、滚动 24h `-15U` 新开暂停、3 连亏 60 分钟暂停。代码默认仍为 `TACTICAL_V2_MODE=off`；云服实际模式以 `.env` 与启动 banner 为准。
- 2026-08-05/06 已完成 V2 入口精确回查、保护单 halt 自愈、旧保护 halt 迁移和重启后 durable final-PnL replay；当前线上修复后的状态不依赖手工清空账本或重启绕过熔断。
- 主入口仍是 `python3 run_agents.py`。云服当前由常驻进程运行，**没有已部署的应用级 cron/systemd/pm2 supervisor**；需要代码生效时使用 `/restart` 或按 [docs/runbook.md](docs/runbook.md) 的受控重启流程。
- **Tactical Exit Track** 与旧 Shadow Tactical sidecar 的详细历史、验收和回滚证据分别见 [docs/handoff.md](docs/handoff.md) 与 `docs/superpowers/reports/`；不要把历史灰度快照当作当前线上状态。
- 保护单 halt 仍保持 fail-closed：只允许在风险已消失且证据完整时自动清除 `okx_sl_algo_unresolved:<symbol>` / `migrate_missing_sl`；manual/daily/reconcile/未知原因必须人工处理。
- OKX 真实 testnet 语义验收 2026-05-28 完成：long_short_mode 13 PASS、net_mode 3 PASS；owner-tag 补验 T0/T1/T6 PASS。

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
- 出口轨道：Main Trend Runner 与 Tactical Exit Track 分离；Tactical 支持 shadow-only 复盘和 live 灰度，shadow-only 复盘看 CounterfactualLedger 的 `rejected_signal_*`
- Tactical V2：Main 进程内持久化 intent/episode/entry/protection/exit/PnL 状态，shadow/live 共用状态机；V2 持仓使用 `strategy_owner=tactical_v2`，不接受 Main partial TP、trailing、thesis invalidation 或 Position Analyst 改仓
- PaperExecutor 影子账户与 live 信号并行（不下真单，独立 topic 隔离）
- Telegram 远程命令：`/status` `/positions` `/halt` `/resume` `/force_resume` `/reconcile` `/halts` `/resume_symbol` `/pnl` `/pnl_id` `/stop` `/restart` `/log` `/paper_gap` `/health`
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
| 多头位置保护·体制感知 | `LONG_LIVE_REGIME_AWARE_RANGE_ENABLED` / `LONG_LIVE_MAX_RANGE_POS_CHOPPY` / `LONG_LIVE_DAILY_GAIN_RANGE_POS_CHOPPY` | choppy/mixed/bearish 收紧 range_pos 阈值转回调入场，bullish 保 0.82；总开关可回退（2026-06-21，生产起步 0.70/目标 0.55） |
| EV 分桶 | `EV_BUCKET_MIN_TRADES` / `EV_BUCKET_SPARSE_ALLOW_UPLIFT` | 稀疏 bucket 不抬 p_win（2026-05-26） |
| Tactical 出口轨道 | `TACTICAL_TRACK_ENABLED` / `TACTICAL_SHADOW_ONLY` / `TACTICAL_MIN_RR_FOR_TRACK` / `TACTICAL_MIN_EV_FOR_TRACK` / `TACTICAL_TP1_R` / `TACTICAL_MAX_HOLD_MINUTES` | 代码默认 disabled + shadow-only；live 灰度需 track=true 且 shadow_only=false，先过 cost gate，再按 Tactical R:R≥0.75 且 EV>-0.04 筛“会真开”样本，TP1 默认 1.00R |
| Tactical V2 执行 | `TACTICAL_V2_MODE` / `TACTICAL_V2_MARGIN_USDT` / `TACTICAL_V2_MAX_CONCURRENT` / `TACTICAL_V2_ROLLING_LOSS_LIMIT_USDT` | 代码默认 `off`；当前云服为 `live`，固定 `100U x 3`、滚动 24h `-15U`，并要求 sidecar drain proof |

完整列表与默认值见 `utils/config_loader.py` 的 `DEFAULTS` 与 `HARD_LIMITS`。

## 常用验证

```bash
python3 -m pytest -q                       # 默认回归（pytest.ini 默认排除 network）
python3 -m pytest -q test_tactical_*.py tests/test_tactical_wld_replay.py  # Tactical 专项
python3 -m pytest -q tests/test_tactical_v2_*.py                         # Tactical V2 专项
python3 scripts/replay_tactical_v2.py --fixture tests/fixtures/tactical_v2_reproduced_window.json
python3 -m pytest -q -m network            # 真实 OKX/Telegram 冒烟
python3 verify_okx_testnet_semantics.py    # OKX mock 验收 10 case
python3 verify_okx_testnet_real.py         # OKX 真实 testnet T0-T15 验收（需 .env.testnet）
```

更细的核心链路 / 收益验证 / 真实环境冒烟命令见 [docs/runbook.md](docs/runbook.md)。

## 文档

| 文档 | 用途 |
|---|---|
| [docs/project-stage-summary.md](docs/project-stage-summary.md) | 当前阶段、功能域总览、使用场景和 sidecar 状态 |
| [docs/development.md](docs/development.md) | 修改规范、链路契约、验证矩阵 |
| [docs/architecture.md](docs/architecture.md) | 技术架构与模块设计 |
| [docs/runbook.md](docs/runbook.md) | 部署、环境变量、故障排查 |
| [docs/integration-guide.md](docs/integration-guide.md) | 消息契约与下游接入 |
| [docs/handoff.md](docs/handoff.md) | 项目交接与决策记录 |
| [docs/to-do-list.md](docs/to-do-list.md) | 当前阻断项、后续优化、已关闭事项 |
| [docs/generated_reports/](docs/generated_reports/) | 系统性审计报告归档 + OKX testnet 验收（2026-05-28 T0-T15 13 PASS / 3 SKIP；2026-05-29 owner-tag T0/T1/T6 PASS） |
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

## 日线形态前向影子记录器（observability-only）

对信号 `Bearish Engulfing|低位跌势`（见 `docs/superpowers/specs/2026-06-23-pattern-forward-shadow-recorder-design.md`）做 record-only 前向验证，**绝不接入 live 决策**。

> **⚠️ 2026-06-25 重要更新（change `pattern-shadow-broaden-universe-and-4h`）**：把 universe 从 30 扩到 **~100 binance 流动币冻结快照**后重跑回测，**日线与 4h 双双干净证伪**——`过三关=0`、所有 pattern×context 均 R 全负、`Bearish Engulfing|低位跌势` 在宽 universe 根本不进排名。**原 30 币的 +0.326R 是小样本/选择偏差，不泛化。** 故：runner 已升级（interval 参数化 1d/4h + settle-when-determinable + 冻结~100 universe），**但 4h 加速 cron 刻意不部署**（不加速收集已证伪的非-edge）；日线 cron 继续作 null-monitor。详见 `docs/superpowers/reports/2026-06-25-pattern-shadow-broaden-universe-and-4h-verify.md` + memory `alpha-source-hunt-verdict`。

**调度（macOS）= 自包含 runner + launchd**。背景:本仓库在 `~/Desktop` 下(macOS TCC 保护目录),**cron/launchd 派生的命令行 python 拿不到 Full Disk Access**(实测无论授权 `/usr/bin/python3` 还是框架 `python3.9` 均 `Operation not permitted`)→ 故部署一个**零 Desktop 依赖**的自包含 runner 到非保护目录运行:
- 仓库内可跟踪源:`scripts/fwdshadow_runner.py`(只用 ccxt + stdlib,与 `cf_pattern_edge_discovery` 同口径;2026-06-25 起 **interval 参数化** `--interval {1d,4h}`、窗口×bpd、**settle-when-determinable**=早退出立即结算/整窗满才 expired/窗未满留未结算[净 R 值不变、无前视]、dedup 按 `(symbol,detect_bar_open_time,interval)`、universe=冻结~100;4h 写独立 `pattern_forward_shadow_4h.jsonl`)
- 部署副本:`~/Library/Application Support/cryptoarb-fwdshadow/`(klines.db + jsonl 也在此,完全不碰 Desktop)
- LaunchAgent:`~/Library/LaunchAgents/com.cryptoarb.pattern-forward-shadow.{record,settle}.plist`(每日 09:17 record / 周一 09:47 settle,本地 CST;launchd 唤醒后补跑错过点),日志 `~/Library/Logs/pattern_forward_shadow.log`。

```bash
# 部署/更新 runner(改了 scripts/fwdshadow_runner.py 后重新部署)
cp scripts/fwdshadow_runner.py ~/Library/Application\ Support/cryptoarb-fwdshadow/
# 手动验证(可访问环境下直接跑)
cd ~/Library/Application\ Support/cryptoarb-fwdshadow && python3 fwdshadow_runner.py --record   # 拉日线+检测
python3 fwdshadow_runner.py --settle                                                            # 结算+滚动报告
# launchd 即时触发 / 看日志 / 卸载
launchctl kickstart -k gui/$(id -u)/com.cryptoarb.pattern-forward-shadow.record
tail -f ~/Library/Logs/pattern_forward_shadow.log
launchctl bootout gui/$(id -u)/com.cryptoarb.pattern-forward-shadow.{record,settle}
```

(仓库内 `pattern_forward_shadow.py` 是 lab 集成版,供可访问环境/对照;launchd 跑的是上面的自包含 runner。)
**4h 能力已在 runner 里(`--interval 4h`)但未配 launchd**——因 2026-06-25 宽 universe 回测已证伪 edge,不部署 4h 加速 cron;需要时手动 `python3 fwdshadow_runner.py --record --interval 4h` 可跑。前向验证须数周累积；结算经诚实门(n<30 拒答)。**确认稳健前不上实盘、不改 config——当前结论是 edge 已证伪,日线 cron 仅作 null-monitor。**

## 日更 cron：choppy+neutral TP1 地板反事实监控（observability-only）

`cf_choppy_neutral_tp1_floor_ab.py`（change `cf-choppy-neutral-tp1-floor-ab`）量化「choppy+neutral 多单卡 TP1 口径地板」的反事实 PnL delta。现诚实门 `INSUFFICIENT_SAMPLE`（n=13<30），须等磁带 choppy+neutral 忠实样本累积后重跑。

**调度 = 用户 crontab（不能自包含——驱动须读 repo 内 live 决策磁带 `data/decision_replay_tape.jsonl`）**：
- crontab 条目：每日 10:13 重跑驱动，输出追加到 `~/Library/Logs/cf_choppy_tp1_floor_ab.log`（每次带时间戳头）。日更只加快发现样本变化，不制造新样本。
- **✅ FDA 已授权并验证（2026-06-24 cron 11:19 实跑通过）**。因 repo 在 `~/Desktop`（TCC 保护目录），`/usr/sbin/cron` 必须有 Full Disk Access，否则读 repo 文件报 `Operation not permitted`（TCC 拦的是文件读：cron 能执行 crontab、能 `cd` 进目录，但读不到文件内容）。**若换机 / FDA 失效需重授**：系统设置>隐私与安全性>完全磁盘访问 > `+` > 文件选择器按 ⌘⇧G 输入 `/usr/sbin/cron` > 打开；然后 `sudo killall cron`（launchd 自动重起，**新进程才带上 FDA**）。
- 看结果：`tail ~/Library/Logs/cf_choppy_tp1_floor_ab.log`，关注主桶 `tp1_floor_rejected` 桶的「诚实门裁定」和 `EARLY_WARNING`。`EARLY_WARNING` 只表示薄样本已强负向，不自动改 live config；一旦诚实门不再 `INSUFFICIENT_SAMPLE`，再据此判断是否对 choppy+neutral 上 TP1 地板（另起 change，须 event_backtest）。

```bash
crontab -l                                   # 查看日更条目
rg "EARLY_WARNING|诚实门裁定|tp1_floor_rejected|日更" ~/Library/Logs/cf_choppy_tp1_floor_ab.log
python3 cf_choppy_neutral_tp1_floor_ab.py    # 可访问环境下手动重跑
```

**确认稳健（诚实门跨过 INSUFFICIENT_SAMPLE）前不上实盘、不改 config；`EARLY_WARNING` 也不得自动触发配置变更。**
