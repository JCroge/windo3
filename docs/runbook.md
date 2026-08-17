# 运维手册

## 快速启动

### 环境要求
- Python 3.10+
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
# 系统写入 data/.restart_flag 后优雅退出，run_agents.py 检测标记后通过 execv 重启解释器
```

> `/restart` 现在会在优雅停机后执行 `os.execv(sys.executable, [sys.executable] + sys.argv)`，重新加载磁盘上的源码。`execv` 置换的是当前进程镜像，所以 **PID 可能保持不变**，这是正常现象；关键是 Python 解释器和 `sys.modules` 会被重建。若变更的是 Python/venv/系统级依赖，仍建议外部重启进程：
> ```bash
> kill -TERM $(pgrep -f run_agents.py) && sleep 5
> nohup python3 run_agents.py > logs/launcher_$(date +%Y%m%d_%H%M%S).log 2>&1 &
> ```
> 验证新代码上线（OKX 路径）：日志出现 `[OKX posMode] 探测成功: net_mode/long_short_mode (testnet=...)`。

**Shadow Tactical live sidecar（独立进程，高风险实验入口）**：

Sidecar 只用于镜像 strict eligible Tactical shadow 记录，写 `data/shadow_tactical_live_*` 专属状态，不应修改 Main `.env` 或重启 Main。目标运维状态是 admission 关闭、只保留 resident monitor 管理历史 owner；**2026-08-17 read-only 复核发现云服 Sidecar 实际仍为 `admission_enabled=true`，进程命令为 `--size-usdt 100 --max-active 5`，这不是本 change 的授权状态。不得直接重启、扩容或把当前运行态当作已验收；恢复/重启前必须先处理 admission stop、drain、pending PnL 和代码同步。** OKX `net_mode` 下同标的堆叠会被阻断；ghost exposure 会 fail-closed 并要求人工处理。

```bash
# 受控运维动作：status 为只读；stop-admission / stop 会改状态，需操作员明确执行
python3 scripts/shadow_tactical_live_sidecar.py status
python3 scripts/shadow_tactical_live_sidecar.py stop-admission
python3 scripts/shadow_tactical_live_sidecar.py drain-report --namespace live
# 仅当 drain-report complete=true 时允许 archive
python3 scripts/shadow_tactical_live_sidecar.py drain-report --namespace live --archive
# 仅当 exchange-flat、无 owner exposure/pending 且无需 resident monitor 时允许 stop
python3 scripts/shadow_tactical_live_sidecar.py stop
# PROHIBITED while admission_enabled=false; do not run or enable admission:
# python3 scripts/shadow_tactical_live_sidecar.py run --poll-seconds 2 --size-usdt 100 --max-active 3
```

`--duration-hours` 已废弃，不会让 resident monitor 自动退出。`stop-admission` 与 runner 的单条候选处理共用 `<state>.lock`：命令会等待已进入交易所 I/O 的当前 admission attempt 完成，再持久化 `admission_enabled=false`；命令成功返回后，runner 每条 event 都会重读该状态，不得再开新仓或把 `false` 覆盖回 `true`。之后 monitor 仍继续管理并退出 owner-bound 旧仓。只有 `drain-report` 显示 `complete=true` 时才允许加 `--archive`；unknown exchange state、pending entry、open owner、保护单歧义或未说明的 pending PnL 都必须保持 incomplete。续跑前先确认 `status` 里 active 合理，并检查 `data/shadow_tactical_live_events.jsonl` 没有 `monitor_ghost_exposure` 或 `monitor_ambiguous_net_mode_stack`。

**Frozen Sidecar admission contract（change `sidecar-frozen-admission-risk-tiers`）**：

Judge 是 Sidecar admission 的唯一策略决策点。未来 Tactical Shadow row 必须在写入 `data/rejected_signal_events.jsonl` 前冻结并持久化这些字段：`sidecar_live_eligible`、`sidecar_policy_version`、`sidecar_risk_tier`、`sidecar_rejection_reason`、`sidecar_decided_at`、`sidecar_policy_evidence`，以及 canonical raw evidence：`tactical_track_gate`、`tactical_trend_exhaustion_warning`、`tactical_weak_volume_oi`、`tactical_weak_provenance`。

Sidecar 只验证冻结结果，不重算指标、LLM、provenance 或 Tactical economics。缺 stamp、unsupported version、字段类型错误、top-level evidence 与 `sidecar_policy_evidence` 不一致、冻结 outcome 与 policy v1 重算结果不一致，全部在任何 exchange / executor 调用前 fail-closed。`sidecar_decided_at` 超过 5 秒、缺失、非有限值或未来偏移超过 1 秒也 fail-closed；历史 unstamped row 只保留研究用途，不允许 backfill live admission。

Policy v1 tier 规则固定：`tactical_track_gate != pass` 拒绝，`tactical_trend_exhaustion_warning=true` 拒绝；clean eligible row 为 `full`，按 `--size-usdt 100` 请求 100U；`tactical_weak_volume_oi=true` 或 `tactical_weak_provenance=true` 为 `reduced`，请求 50U。Sidecar `--max-active` 只允许 1..3，生产上限是 3；`ContractExecutor` 的 Sidecar-only `max_trade_amount_override` 使用 100U ceiling，Main 的 `MAX_TRADE_AMOUNT` 和 Main 进程风险配置不变。

本地 sealed replay fixture `tests/fixtures/shadow_sidecar_policy_53_trade_window.json` 只证明 policy v1 在 53-row audited Sidecar cohort 上得到 9 条 eligible、all-100U counterfactual net `+4.47024185U`、100U/50U tiered counterfactual net `+9.086859325U`，并且 100-loop deterministic replay 稳定。这个 replay 不是 exchange-fill proof、不是未来 realized PnL，也不授权 live restart。恢复 admission 前必须重新收集当前 process、owner、position、protection、pending PnL 与 admission facts；有 active owner 或 unknown exchange/protection state 时不得重启。

**Telegram远程命令**（需配置TELEGRAM_BOT_TOKEN和TELEGRAM_CHAT_ID）：
| 命令 | 功能 |
|------|------|
| `/status` | 运行时长、持仓数、今日PnL、全局熔断、per-symbol halt、Tactical V2 mode/100U x 3/槽位/滚动PnL/circuit/protection/parity、agent/bus 健康总括 |
| `/positions` | 每个持仓的方向/杠杆/入场价/SL/TP |
| `/halt` | 手动熔断（停止新交易，保留持仓） |
| `/resume` | 对账通过后解除熔断 |
| `/force_resume` | 跳过对账强制解除熔断 |
| `/reconcile` | 执行持仓对账 |
| `/halts` | 查看 per-symbol halt 列表 |
| `/resume_symbol <SYMBOL>` | 清除指定 per-symbol halt；若全局 halt 仍 active，回显会提示仍需 `/resume` |
| `/pnl` | 查看 PnL 汇总 |
| `/pnl_id <ID>` | 按订单/仓位 ID 查询 PnL 解析 |
| `/paper_gap <SYMBOL>` | 查看 paper/live gap |
| `/health` | 查看 agent loop、queue、LLM、data 四维健康明细 |
| `/stop` | 优雅退出 |
| `/restart` | 优雅退出后自动重启 |
| `/log` | 最新10条关键日志 |

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
python3 -m pytest -q
python3 -m pytest -q test_tactical_*.py tests/test_tactical_wld_replay.py  # Tactical Exit Track 专项
python3 -m pytest -q -m network   # 仅跑 network 测试（需 data/klines.db 和实时网络，运行 5s 时间窗后退出；缺数据库时 fixture 干净 skip）

# OKX 真实 testnet 端到端语义验收（需 .env.testnet 隔离凭证）
python3 verify_okx_testnet_semantics.py   # mock 矩阵 10 case，CI 一定要先过这个
python3 verify_okx_testnet_real.py        # 真实 OKX testnet T0-T15，2026-05-28 long_short_mode 13 PASS / 3 SKIP（T0/T1/T4-T6/T8-T15 PASS；T2/T3 long_short_mode SKIP、T7 mock_only SKIP），切到 net_mode 子账户后单独跑 T0/T2/T3 全 PASS（脚本 T2/T3 已 self-contained 自建仓）；覆盖 EarlyReview move/SL cancel failure/close path/close cause
```

> conftest.py 通过 `monkeypatch.chdir(tmp_path)` 把 `data/` 和 `logs/` 隔离到临时目录，每个测试独立。
> pytest.ini 默认排除 `network` 标记的测试；网络冒烟需要显式 `-m network`。

## 数据持久化文件

文件路径由 `utils/state_paths.py` 单一真相源派生（FR-008，2026-05-28）。命名空间优先级：显式 `STATE_NAMESPACE=live|testnet|paper` > `USE_TESTNET=true` 推断 testnet > 默认 live。下表 default basename 为 live 命名空间；testnet/paper 自动加 `testnet_` / `paper_` 前缀（如 `data/testnet_positions.json`），启动 banner 会打印当前 namespace 与全部 6 个状态文件路径。

| 文件 | 写入者 | 用途 | 备注 |
|------|--------|------|------|
| `data/positions.json` | ContractExecutor | 实盘持仓快照 | 重启恢复 |
| `data/risk_state.json` | RiskManager | 回撤基准（v2 schema：session_peak_equity/baseline_mode/legacy_peak_balance） | 重启不丢，启动时按 baseline_mode 决定是否重置 |
| `data/trade_history.json` | ReviewerAgent | 已平仓历史+策略衰减 | 缺失时空起 |
| `data/riskguard_state.json` | PortfolioRiskGuard | 持仓追踪/价格缓存/熔断状态/Tactical circuit | 缺失时空起；`tactical_circuit` 保存 Tactical 日亏、连亏暂停和 pause_until |
| `data/halt_state.json` | HaltState | 全局熔断状态 | 缺失/损坏 fail-closed；`halted=false` 时残留 `reason` 只能当 stale metadata，不代表 active halt |
| `data/agent_health.json` | Orchestrator | agent/bus/per-symbol halt 快照 | `/status` / `/health` 读取；`halted_symbols` 最多有 30s 延迟 |
| `data/tactical_v2_events.jsonl` | TacticalV2Controller | intent/episode/entry/protection/exit/parity 的 append-only 权威事件 | live namespace 无前缀；testnet/paper 自动加 namespace 前缀 |
| `data/tactical_v2_state.json` | TacticalV2Controller | crash recovery 原子快照 | 必须与 event sequence 一致；未知或损坏状态 fail-closed |
| `data/tactical_v2_status.json` | TacticalV2Controller | TG 只读运维快照 | 默认 90 秒过期；缺失、畸形、NaN/Inf 都不得显示 healthy |
| `data/sidecar_retirement.json` | Sidecar drain/cutover | sidecar admission stop、owner/exchange/protection/PnL drain 证明 | `complete=true` 且 archived/hash/namespace/owner 验证通过才允许 V2 live |
| `data/judge_state.json` | MultiJudge | deferred_entry/sl_timestamps/cooldown | 缺失时空起，启动时清理过期条目 |
| `data/live_order_events.jsonl` | LiveLedger | 订单事件流（open/reduce/close） | append-only |
| `data/live_position_lifecycle.json` | LiveLedger | 持仓生命周期聚合 | 原子写入 |
| `data/paper_positions.json` | PaperExecutor | 影子持仓快照 | 缺失=从初始 equity 起 |
| `data/paper_equity.json` | PaperExecutor | 影子账户余额 | 首次启动=EFFECTIVE_BALANCE_CAP 或 1000 |
| `data/paper_trades.jsonl` | PaperExecutor | 影子已平仓 append-only 流水 | 与实盘 trade_history 互不影响 |
| `data/rejected_signal_events.jsonl` | CounterfactualLedger | 被拒/影子决策事件流 | append-only；Tactical shadow-only 复盘主输入 |
| `data/rejected_signal_lifecycle.json` | CounterfactualLedger | 被拒/影子决策生命周期 | 原子写入；记录 tracking 与结算状态 |
| `data/.restart_flag` | TelegramNotifier | 远程 /restart 标记 | run_agents.py 检测后重启 |

## 环境变量

| 变量 | 说明 | 默认值 | 必需 |
|------|------|--------|------|
| EXCHANGE | 交易所（binance/okx） | okx | 是 |
| OKX_API_KEY | OKX API密钥 | - | 是（OKX） |
| OKX_SECRET | OKX Secret | - | 是（OKX） |
| OKX_PASSWORD | OKX Passphrase | - | 是（OKX） |
| OKX_POS_MODE_OVERRIDE | OKX posMode 兜底覆盖（仅 testnet 生效，可选 `net_mode` / `long_short_mode`）。live 永远以 `private_get_account_config` 返回为准；testnet 拿不到时若设此值则使用，否则降级 `net_mode` 并 warning | （未启用） | 否 |
| BINANCE_API_KEY | Binance API密钥 | - | 是（Binance） |
| BINANCE_SECRET | Binance Secret | - | 是（Binance） |
| USE_TESTNET | 是否测试网；未显式设置 STATE_NAMESPACE 时，`true` 自动把状态文件切到 `data/testnet_*` | false | 否 |
| STATE_NAMESPACE | 状态文件命名空间（FR-008，2026-05-28），可选 `live` / `testnet` / `paper`，覆盖 USE_TESTNET 推断；非白名单值回退 `live`；live 默认完全兼容历史路径 | （随 USE_TESTNET 推断） | 否 |
| LEVERAGE | 杠杆倍数 | 3 | 否 |
| MAX_TRADE_AMOUNT | 单笔最大保证金（USDT） | 10 | 否 |
| MAX_DRAWDOWN_PCT | 最大回撤百分比 | 20.0 | 否 |
| MAX_DAILY_LOSS | 每日最大亏损（USDT，正数） | 50 | 否 |
| CONSECUTIVE_LOSS_LIMIT | 连续亏损熔断次数（连亏达此数即全平熔断）。可经 config.yaml `risk.consecutive_loss_limit` 配置 | 3 | 否 |
| EV_WINRATE_GATE_ENABLED | EV 开仓门是否用实际滚动胜率。`false`=关闭，EV 公式改用固定 `ev_neutral_p_win`、跳过胜率<40%硬阈值与分桶覆盖，仅保留 R:R/成本经济门。可经 config.yaml `risk.ev_winrate_gate_enabled` 配置 | true | 否 |
| ROTATION_CLOSE_HELD_ENABLED | 标的轮换是否强平已持仓标的。`false`=不强平，持仓标的保留在 active 集、出场交 PositionAnalyst（B-revised 保护）；`true`=回退旧行为（轮出即强平）。可经 config.yaml `risk.rotation_close_held_enabled` 配置 | false | 否 |
| EV_NEUTRAL_P_WIN | 关闭胜率门时 EV 公式使用的固定中性胜率，范围 [0.0, 1.0]。可经 config.yaml `risk.ev_neutral_p_win` 配置 | 0.55 | 否 |
| EFFECTIVE_BALANCE_CAP | 逻辑账户拆分：风控按此上限计算余额（真实余额不变）。留空=用真实余额。范围 [10, 1_000_000] | （未启用） | 否 |
| DRAWDOWN_BASELINE_MODE | 回撤基准模式：`session_start`=启动时重置基准（默认）；`persisted_peak`=继承历史峰值（兼容旧行为） | session_start | 否 |
| RESET_RISK_BASELINE_ON_START | 启动时是否重置本轮回撤基准 | true | 否 |
| ANTHROPIC_API_KEY | Claude API密钥 | - | 否（多Agent系统） |
| ANTHROPIC_BASE_URL | Claude API地址（中转） | https://api.anthropic.com | 否 |
| ANTHROPIC_MODEL | Claude模型名 | claude-opus-4-6 | 否 |
| RESEARCH_INTERVAL | 研判层运行周期（秒） | 14400 (4h) | 否 |
| RANKING_ENABLED | 是否启用候选 Top-N Ranking 裁决 | true | 否 |
| RANK_FLUSH_DELAY | Ranking flush 窗口秒数，等待同批候选到齐后统一排序。范围 [1, 30] | 5.0 | 否 |
| MAX_CONCURRENT_POSITIONS | 最大并发持仓数（同时开仓数量）。范围 [1, 20] | 3 | 否 |
| SHORT_REGIME_GUARD_ENABLED | 做空结构性风险门总开关（`Judge._classify_short_entry_risk`）；`false`=整门失效 | true | 否 |
| SHORT_LIVE_MIN_SCORE | 空单入场最低 abs(score)（结构门 `short_score_too_low`） | 55 | 否 |
| SHORT_LIVE_MIN_RSI | 空单入场最低RSI（防超卖追空） | 40 | 否 |
| SHORT_LIVE_MIN_RANGE_POS | 空单入场最低24h区间位置（防底部追空） | 0.45 | 否 |
| SHORT_LIVE_MIN_HTF_VOTES | 空单入场最低 HTF 看跌票数（trend.direction/higher_tf_bias/daily_bias 数 bearish） | 2 | 否 |
| SHORT_LIVE_REQUIRE_DAILY_BEARISH | 空单是否要求日线偏空（不满足记 `daily_bearish_required` 拒/降级 probe） | true | 否 |
| SHORT_LIVE_MAX_PRE_MOVE | 空单入场前12h最大跌幅（防追空） | -0.01 | 否 |
| PHASE2_SIGNAL_CONFIDENCE_SPLIT_ENABLED | Confidence Split：signal_score/execution_confidence/position_scale 三层拆分 | true | 否 |
| PHASE2_MOMENTUM_PROBE_LONG_ENABLED | Momentum Probe Long：RSI 70-85 强趋势追踪小仓位 | true | 否 |
| PHASE2_TREND_SATURATION_ENABLED | Trend Saturation：strength>90 cap + 4h RSI 动态衰减 | true | 否 |
| PHASE2_BUCKETED_EV_ENABLED | Bucketed EV：per side×regime×entry_type 分桶胜率 | true | 否 |
| RR_FLOOR_DEFAULT | R:R 默认 floor（任何不触发其他分支的路径） | 1.5 | 否 |
| RR_FLOOR_LONG_BULLISH | 牛市低 R:R 多头 floor（low_rr_extra 槽） | 1.30 | 否 |
| RR_FLOOR_LONG_ALIGNED_CHOPPY | mixed/choppy 下趋势强一致多头 floor（2026-05-26 新增） | 1.30 | 否 |
| RR_FLOOR_SHORT_BULLISH | 牛市做空 floor（强 guard） | 1.80 | 否 |
| PROBE_RR_FLOOR | probe_short / probe_long 路径专用 floor，主路径与 deferred 路径一致 | 1.30 | 否 |
| LOW_RR_SLOT_ENABLED | 是否启用低 R:R 多头额外槽位（牛市 1.30-1.50 多头） | true | 否 |
| LOW_RR_LONG_ALIGNED_ENABLED | mixed/choppy 下趋势强一致多头是否使用低 R:R floor（2026-05-26 新增） | true | 否 |
| LOW_RR_MAX_LEVERAGE | 低 R:R 多头最大杠杆 | 5 | 否 |
| LOW_RR_MAX_POSITION_PCT | 低 R:R 多头最大仓位比例 | 0.5 | 否 |
| LOW_RR_EXTRA_SLOT | 低 R:R 多头额外槽数 | 1 | 否 |
| LONG_LIVE_POSITION_GUARD_ENABLED | 多头入场位置保护总开关（2026-05-26 新增，防 NEAR 类山顶接货） | true | 否 |
| LONG_LIVE_MAX_RANGE_POS | 24h 区间位置阈值，>= 此值视为接近短期高位 | 0.82 | 否 |
| LONG_LIVE_MAX_PRE_MOVE | 12h 前置涨幅阈值，>= 此值且 range_pos>=`LONG_LIVE_DAILY_GAIN_RANGE_POS` 视为前置过热 | 0.05 | 否 |
| LONG_LIVE_MAX_DAILY_GAIN | 上一根已完成日线涨幅阈值，>= 此值且 range_pos>=`LONG_LIVE_DAILY_GAIN_RANGE_POS` 视为日线过热 | 0.10 | 否 |
| LONG_LIVE_DAILY_GAIN_RANGE_POS | pre_12h / prev_daily 联合判定的辅助 range_pos 阈值 | 0.75 | 否 |
| LONG_LIVE_PULLBACK_MIN_PCT | overheat 触发后等待回调的最小幅度（与 ATR% 取大） | 0.025 | 否 |
| LONG_LIVE_PULLBACK_TIMEOUT_HOURS | `deferred_pullback_overheat` 最大等待小时 | 4 | 否 |
| LONG_LIVE_OVERHEAT_DISABLE_CHASE | overheat deferred 期间禁止 chase 入场 | true | 否 |
| LONG_LIVE_REGIME_AWARE_RANGE_ENABLED | 多头过热阈值是否体制感知（2026-06-21 `regime-aware-long-entry-guard`）。`true`=choppy/mixed/bearish 用收紧阈值、bullish 用默认 0.82；`false`=所有体制用固定 0.82/0.75（即时回退旧行为）。可经 config.yaml `risk.long_live_regime_aware_range_enabled` 配置 | true | 否 |
| LONG_LIVE_MAX_RANGE_POS_CHOPPY | choppy/mixed/bearish 体制下收紧的 range_pos 阈值（仅 REGIME_AWARE 开启时生效）。代码默认/目标 0.55；**生产 config.yaml 缓进起步 0.70**（先只罩最极端追顶，观察后逐步收紧） | 0.55 | 否 |
| LONG_LIVE_DAILY_GAIN_RANGE_POS_CHOPPY | 同上体制下 pre_12h/prev_daily 联合判定的辅助 range_pos 阈值 | 0.50 | 否 |
| EV_BUCKET_MIN_TRADES | bucket 提高 p_win 所需最小样本数（低于此值视为稀疏 bucket） | 10 | 否 |
| EV_BUCKET_SPARSE_ALLOW_UPLIFT | 是否允许稀疏 bucket 抬高 p_win（默认禁止，仅允许降低/缩仓） | false | 否 |
| LADDER_RR_ENABLED | **lever2 逃生阀**（2026-06-17 `trend-entry-levers-default-on`）：阶梯加权 effective_rr 口径（按 executor 真实 50/25/25 离场加权，影响 R:R 地板 gate=多开仓）。**默认开**；设 `false` 即时回退 TP1-only 旧口径（live 决策回滚，无需改代码） | true | 否 |
| TACTICAL_TRACK_ENABLED | Tactical Exit Track 总开关。`false`=所有候选按 Main/hold 旧行为；`true`=Judge 执行 Main-vs-Tactical 分类 | false | 否 |
| TACTICAL_SHADOW_ONLY | Tactical 分类只记录 counterfactual，不真开 Tactical。当前必须保持 `true`：合格候选写入 `data/rejected_signal_*`，不发布 live Tactical 订单。`false` 是历史 legacy live mode，受 2026-08-12 NO-GO gate 禁止，严禁设置 | true | 否 |
| MAIN_QUALITY_GATE_ENABLED | Main Trend quality gate。开启后强趋势候选才留 Main，弱/混合但方向有效的候选才可能降级 Tactical | true | 否 |
| MAIN_QUALITY_MIN_PROVENANCE | Main quality gate 对数据 provenance 的最低要求；低于一半阈值直接 shadow-only | 0.20 | 否 |
| MAIN_QUALITY_BLOCK_LLM_REVERSAL | LLM 明确反向/反转风险时阻止留在 Main | true | 否 |
| MAIN_QUALITY_ALLOW_MIXED_OVERRIDE | mixed regime 是否允许 quality override 留 Main | false | 否 |
| MAIN_QUALITY_REQUIRE_VOLUME_OR_OI | Main 是否要求成交量或 OI 确认 | true | 否 |
| TACTICAL_MAX_LEVERAGE | Tactical 最大杠杆，独立于 Main risk-budget 输出 | 5 | 否 |
| TACTICAL_DEFAULT_POSITION_PCT | Tactical 默认保证金占 Main plan `size_usdt` 比例 | 0.70 | 否 |
| TACTICAL_VERY_NEAR_POSITION_PCT | Tactical stop 极近时允许的保证金比例上限 | 1.00 | 否 |
| TACTICAL_STOP_CAP_R_MAIN | Tactical stop 相对 Main stop 的上限比例 | 0.60 | 否 |
| TACTICAL_VERY_NEAR_STOP_R_MAIN | 判定 `tactical_stop_quality=very_near` 的 stop 比例 | 0.40 | 否 |
| TACTICAL_TP1_R | Tactical TP1 相对 Tactical stop 的 R 倍数；实际 TP1 还会被原 Main TP1 就近约束 | 1.00 | 否 |
| TACTICAL_COST_COVERAGE_MIN | Tactical gross profit 覆盖手续费+滑点成本的最低倍数 | 4.0 | 否 |
| TACTICAL_MIN_RR_FOR_TRACK | 成本门通过后，进入 `track=tactical` true-open 样本所需的最低 Tactical effective R:R | 0.75 | 否 |
| TACTICAL_MIN_EV_FOR_TRACK | 成本门通过后，进入 `track=tactical` true-open 样本所需的最低 Tactical EV；代码使用 `>` 比较 | -0.04 | 否 |
| TACTICAL_MAX_HOLD_MINUTES | Tactical 最大持仓分钟数；超时走本地全平 | 90 | 否 |
| TACTICAL_MIN_PROGRESS_R | Tactical weakened 状态下视为“有进展”的最低 best-profit R | 0.15 | 否 |
| TACTICAL_WEAKENED_NO_PROGRESS_MIN_MINUTES | thesis weakened 且无进展后的最短退出等待分钟 | 30 | 否 |
| TACTICAL_WEAKENED_NO_PROGRESS_MAX_MINUTES | weakened 无进展最大等待分钟，用于后续动态化上限 | 45 | 否 |
| TACTICAL_DAILY_LOSS_LIMIT_USDT | Tactical 独立日亏硬停，负数 | -10.0 | 否 |
| TACTICAL_LOSS_STREAK_PAUSE_COUNT | Tactical 连续亏损暂停阈值 | 3 | 否 |
| TACTICAL_LOSS_STREAK_PAUSE_MINUTES | Tactical 连亏暂停分钟数 | 60 | 否 |
| TACTICAL_QUALITY_WINDOW_TRADES | Tactical 质量窗口交易数 | 20 | 否 |
| TACTICAL_SUCCESS_WINDOW_TRADES | Tactical 成功标准窗口交易数 | 30 | 否 |
| TACTICAL_SUCCESS_MIN_WIN_RATE | Tactical 灰度扩容所需最低胜率 | 0.55 | 否 |
| TACTICAL_SUCCESS_MIN_PROFIT_FACTOR | Tactical 灰度扩容所需最低 profit factor | 1.2 | 否 |
| TACTICAL_V2_MODE | V2 模式：`off` / `shadow` / `live`；live 仍受 sidecar retirement gate 约束 | off | 否 |
| TACTICAL_V2_MARGIN_USDT | V2 单仓固定保证金；首轮安全约束锁定为 100U | 100 | 否 |
| TACTICAL_V2_MAX_CONCURRENT | V2 active+pending 独立槽位数；首轮固定 3 | 3 | 否 |
| TACTICAL_V2_MAX_LEVERAGE | V2 最大杠杆 | 5 | 否 |
| TACTICAL_V2_ENTRY_MAX_WORSE_R | executable ask/bid 相对 frozen entry 允许的最差追价 | 0.10 | 否 |
| TACTICAL_V2_ENTRY_TTL_SECONDS | frozen entry 限价单最长等待；到期终态且不回填 | 900 | 否 |
| TACTICAL_V2_MAX_HOLD_MINUTES | V2 全仓 max-hold | 90 | 否 |
| TACTICAL_V2_ROLLING_LOSS_LIMIT_USDT | V2 滚动 24h final PnL 新开暂停阈值 | -15 | 否 |
| TACTICAL_V2_LOSS_STREAK_COUNT | V2 连续 final loss 暂停阈值；触发后消费并重置 streak | 3 | 否 |
| TACTICAL_V2_LOSS_STREAK_PAUSE_MINUTES | V2 连亏新开暂停时间 | 60 | 否 |
| TACTICAL_V2_STATUS_STALE_SECONDS | `/status` 允许的 V2 快照最大年龄 | 90 | 否 |
| BOT_INSTANCE_ID | Main/V2 交易所 owner tag；实盘必须非空且与 sidecar 不同 | main01 | 是 |
| SIDECAR_BOT_INSTANCE_ID | Sidecar 专用 owner tag；sidecar 启动后会强制用此值覆盖进程内的 `BOT_INSTANCE_ID` | stlive | 是 |
| SHADOW_DECISION_LOGGER_ENABLED | **影子记录器开关**（2026-06-17 `trend-entry-shadow-decision-logger`，observability-only 不碰 live）：每信号旁路跑 both-levers 影子决策写 `data/shadow_decision_log.jsonl`（real vs shadow=lever1 增量）。**默认开**；设 `false` 关闭影子记录 | true | 否 |
| POSITION_RESYNC_CONFIRM_TICKS | **仓位同步补录双确认 tick 数**（2026-06-20 `fix-phantom-position-resync`）：`sync_positions` 对本地缺失、交易所新出现的持仓须连续此数个 sync tick 确认才补录，防交易所平仓后上报延迟产生幽灵持仓（实证 OKX 滞后 76s 击穿原 60s `_close_cooldown`）。范围 [1,10]，调大更保守（真仓补录延迟更多 sync tick） | 2 | 否 |
| TELEGRAM_BOT_TOKEN | Telegram Bot Token | - | 否（通知） |
| TELEGRAM_CHAT_ID | Telegram Chat ID | - | 否（通知） |

**云服运行快照（2026-08-06）**：Tactical V2 为 `LIVE 100U x 3`，`0 active / 0 pending / 3 free`，`integrity_halt=null`，protection/reconciliation 均 `verified`，rolling PnL `-0.9593U`、loss streak `1`。Sidecar 仍 resident 但 `admission_enabled=false`、active=0；不要把旧 V1 `TACTICAL_TRACK_ENABLED` / `TACTICAL_SHADOW_ONLY` 默认值当作 V2 线上状态。重启或回滚前必须重新核对 `.env`、启动 banner 和 `data/tactical_v2_status.json`。

## R:R Floor 策略

`Judge._select_rr_floor(action, plan, tech, score)` 是 R:R floor 的**唯一入口**，主开仓路径与 `_apply_regime_policy`（deferred 路径）共用，禁止在调用点重新写 if/else 分支。函数返回 `(min_rr, rr_policy, rr_floor_reason)` 三元组，按以下顺序匹配第一个命中的分支：

| rr_policy | 触发条件 | min_rr 来源 | 备注 |
|---|---|---|---|
| `probe` | `plan['is_probe']` 为真（probe_short / probe_long） | `PROBE_RR_FLOOR` | 主路径与 deferred 路径一致 |
| `long_bullish_low_rr` | `eff_regime=bullish` AND 多头 AND `LOW_RR_SLOT_ENABLED=true` | `RR_FLOOR_LONG_BULLISH` | 进 low_rr_extra slot，杠杆/仓位受限 |
| `long_aligned_low_rr` | `eff_regime∈{mixed, choppy}` AND 多头 AND `LOW_RR_SLOT_ENABLED=true` AND `LOW_RR_LONG_ALIGNED_ENABLED=true` AND `trend.direction=bullish` AND (`htf_bias=bullish` OR `daily_bias=bullish`) AND 未 `block_long` AND `|score|≥min_deferred_signal_score` | `RR_FLOOR_LONG_ALIGNED_CHOPPY` | 2026-05-26 新增，进 low_rr_extra slot |
| `short_bullish_strong` | `eff_regime=bullish` AND 空头 AND `SHORT_REGIME_GUARD_ENABLED=true` | `RR_FLOOR_SHORT_BULLISH` | 牛市强 guard，仅放行高质量空头 |
| `default` | 不匹配以上任何分支 | `RR_FLOOR_DEFAULT` | mixed/choppy 空头默认仍 1.50；bullish 空头 1.80 |

**约束**：
- `low_rr_extra` 槽位（`long_bullish_low_rr` / `long_aligned_low_rr`）仓位与杠杆受 `LOW_RR_MAX_POSITION_PCT` / `LOW_RR_MAX_LEVERAGE` 双重压缩；并发额外槽数受 `LOW_RR_EXTRA_SLOT` 限制。
- 不放宽空头：`mixed/choppy` 下空头默认仍是 `RR_FLOOR_DEFAULT`；`bullish` 空头是 `RR_FLOOR_SHORT_BULLISH`。
- attribution 字段 `rr_floor_used` / `rr_floor_reason` / `rr_policy` 写入 `trade_decision.attribution`，被拒决策也会落到 `data/journal/events_*.jsonl` 用于复盘。
- 修改任何 R:R floor 必须改 `Judge._select_rr_floor` 单一函数；事件回测 `event_backtest.py` 同步同构验证。

详见 `docs/rr_floor_policy_prd.md` 与 `docs/rr_floor_policy_acceptance.md`。

## Tactical Exit Track

Tactical 是 Main Trend Runner 之外的短线落袋轨道。它不复用 Main ladder TP2/TP3 的 R:R 假设；Judge 先通过 `_classify_track` 判断 `main` / `tactical` / `shadow_only` / `reject`，再由 `_apply_tactical_profile` 改写 stop、TP1、size、leverage、`tactical_effective_rr`、`tactical_expected_value`、`tactical_cost_gate` 和 `tactical_track_gate`。Tactical 门控顺序固定为：先成本门，再 `TACTICAL_MIN_RR_FOR_TRACK` / `TACTICAL_MIN_EV_FOR_TRACK` 阈值门。

`TACTICAL_SHADOW_ONLY=true` 时，系统不会生成 live Tactical 订单，也不是 PaperExecutor 影子持仓；Judge 会通过 `_apply_tactical_shadow_profile` 生成“如果真开 Tactical 会使用的” counterfactual plan，并写入 `data/rejected_signal_events.jsonl` 与 `data/rejected_signal_lifecycle.json`。true-open 样本应带 `track=tactical`、`exit_profile=tactical_v1`、`tactical_cost_gate=pass`、`tactical_track_gate=pass`、`tactical_max_hold_minutes=90`；成本门通过但 RR/EV 阈值门失败的候选会保留 `track=shadow_only` / `exit_profile=tactical_v1` 继续做 90 分钟 max-hold counterfactual，不计入“会真开 Tactical”的盈利样本；成本门失败则为 `track=shadow_only` / `exit_profile=none`。

旧的 Tactical live 分支曾在 `TACTICAL_SHADOW_ONLY=false` 时进入 live 执行链路；该分支在当前 2026-08-12 gate 下为历史行为，禁止启用。Tactical circuit 暂停只阻止 Tactical 新开仓，不等同全局 halt；全局保护单/执行完整性失败仍可停全系统。

`TACTICAL_TP1_R=1.00` 表示 TP1 距离为 1 倍 Tactical stop 距离；若原 Main TP1 更近，则使用更近的 Main TP1 作为上限。

**历史上线顺序（已完成；非当前操作指引，2026-08-12 NO-GO gate 优先；不要复制或执行）**：

```bash
# 1. 只打开分类与 Tactical counterfactual 记录，不真开 Tactical
# TACTICAL_TRACK_ENABLED=true
# TACTICAL_SHADOW_ONLY=true
# TACTICAL_MIN_RR_FOR_TRACK=0.75
# TACTICAL_MIN_EV_FOR_TRACK=-0.04

# 2. 重启后观察 rejected_signal_* 里的 Tactical counterfactual
# python3 -m pytest -q test_tactical_*.py tests/test_tactical_wld_replay.py

# 3. 历史步骤，仅供审计上下文；当前 gate 禁止任何 live admission
# TACTICAL_TRACK_ENABLED=true
# PROHIBITED / no-op under the current gate; do not set live admission:
# TACTICAL_SHADOW_ONLY=false
```

**回滚**：

```bash
TACTICAL_TRACK_ENABLED=false
# 或保留分类但禁止真开
TACTICAL_SHADOW_ONLY=true
```

回滚后用 `/restart` 或外部 supervisor 重启加载 `.env`。已开的 Tactical 仓位仍按持仓记录里的 `track=tactical` 与本地生命周期管理，禁止手工删 `data/positions.json`。

**运维观察点**：
- `trade_decision.attribution.track` / `exit_profile` / `slot_type` 用于区分 Main 与 Tactical。
- shadow-only 复盘以 `data/rejected_signal_events.jsonl` 和 `data/rejected_signal_lifecycle.json` 为准，不看 PaperExecutor 的 `paper_execution_result`。
- true-open Tactical counterfactual 的核心字段：`track=tactical`、`exit_profile=tactical_v1`、`tactical_expected_value`、`tactical_effective_rr`、`tactical_cost_gate=pass`、`tactical_track_gate=pass`、`tactical_max_hold_minutes=90`。
- ledger 结局事件包括 `shadow_tp`、`shadow_sl`、`shadow_tactical_max_hold`、`shadow_expired`；`shadow_tactical_max_hold` 是 Tactical 最大持仓时间到期的影子结算。
- `tactical_close_reason` 只在 Tactical TP1、最大持仓、thesis invalidated、weakened-no-progress 等路径出现。
- `tactical_cost_gate=fail` 的候选应保持 `shadow_only`，不能借 Main ladder effective R:R 过门。
- `tactical_track_gate=fail` 且 `tactical_gate_failed=min_rr_or_ev` 表示成本门已过但未达到当前 RR/EV 样本筛选阈值；它可以继续保留 Tactical exit profile 做 shadow 结算，但不能按“会真开 Tactical”统计。
- Tactical 日亏、连亏暂停和并发槽位独立于 Main；系统级执行/保护单失败仍应触发全局 fail-closed。
- `/status` 会分开显示 `全局熔断`、`Per-symbol halt`、`Tactical circuit`。TG 里看到“熔断”时先确认是哪一行，不能把全局保护单 halt 误判为 Tactical 连亏暂停。

### Tactical V2

#### Shadow admission parity replay 门禁（2026-08-12）

> **NO-GO：Sidecar admission 必须保持 `admission_enabled=false`。在真实 quote-level executable evidence 与 fill-bound protection evidence 分别通过前，禁止恢复 Sidecar admission。本 replay 的 `live_rollout_ready=false`；它也不授权扩大 V2 保证金/槽位或修改生产配置。**

本地重放命令（默认 100 次稳定性循环）：

```bash
python3.12 scripts/replay_tactical_v2_admission.py --fixture tests/fixtures/tactical_v2_shadow_admission_window.json
```

成功结果必须同时满足：22 个 raw candidate（BICO 18、PUMP 4；6 个 unique candidate ID）归一化为 accepted 5（BICO 3、PUMP 2）、`duplicate_episode=17`、other rejected 0、normalized replay `unknown=0`，且 100 次循环的 identities/reasons 与 fixture fingerprint 稳定。历史 V2 persisted intent 只有 3 个且全为 BICO；但 22 个 source candidate 都早于 durable `candidate_handled` receipt，因此 historical receipt evidence 必须单列为 `unknown=22`，不得因 replay `unknown=0` 推断它们已消费或丢失。

PUMP 根因是 terminal episode 后出现 neutral、available/unblocked 且带更新 closed 15m bar 的 candidate；旧 renewal 规则在 intent creation 前返回 `duplicate_episode`。修复后，只有 terminal、available、unblocked、side-compatible 的 neutral candidate 在 closed bar 更新或 structure token 变化时才能续期；one-attempt-per-episode 仍保持权威。durable receipt 从此区分 accepted、duplicate、rejected 与 gap outcome。

5 个 accepted candidate 的 entry-decision check 使用 recorded journal evaluation time 和 synthetic `bid=ask=entry_ref`，仅证明 shared entry reducer、governor capacity 与 900 秒 TTL 行为。必须保持以下限制：`historical_executable_quote_available=false`（PUMP 无历史 bid/ask）、`exchange_fill=false`、`protection_evidence_proven=false`、`protection_check_status=not_run_no_fill`、`live_rollout_ready=false`。synthetic terminal episode boundary 只做 admission normalization，不代表 market fill 或 settlement。

收益只能按 audit worksheet/source aggregation 报告，不是 `scripts/replay_tactical_v2_admission.py` 的输出：read-only cloud source `/opt/crypto-arbitrage/data/rejected_signal_events.jsonl` 在 epoch `1786183980..1786443180` 内按 `shadow_tp`/`shadow_sl` 记录分组，得到 22 个 Legacy Shadow row 的 18 TP / 4 SL、row win rate `81.82%`；row return 为 `sum(pnl_pct) / 100 = +32.4530%`。归一化 opportunity 每个 normalized structural opportunity 只选一个 representative，得到 4 TP / 1 SL、opportunity win rate `80%`，scalar return 合计 `+6.9621%`。本地测试不重新拉取 cloud。不得称为 exchange fill、realized USDT PnL 或 settlement parity。

审计窗口为 epoch `1786183980..1786443180`（2026-08-08 10:13 UTC 至 2026-08-11 10:13 UTC）。cloud source 只能 read-only 收集；实现、replay 和测试必须 local、network-denied、temp-root-only，不得访问或修改 cloud/production data。该 change/branch 的 Python 3.12 验证为 focused Tactical V2 `482 passed`、full repository `2143 passed, 4 deselected, 576 warnings`、network/temp isolation `2 passed, 80 deselected`；本次验证主机实际使用 `/usr/local/anaconda3/bin/python3.12`，这些计数不是 main-branch baseline。

V2 不会重新启用旧的 `TACTICAL_SHADOW_ONLY=false` live 分支。Judge 在 Shadow Tactical 分类点生成固定计划，V2 再按 `100U`、最多 5x 的 full-TP1 净成本口径复核 cost coverage、RR 和 EV；合格计划冻结为 `tactical_intent.v2`，后续 Main 不得重算 entry/SL/TP。相同 symbol/side/15m structure epoch 只允许一次 attempt，capacity skip、account reject、miss、cancel 或 close 都会消费该 episode，释放槽位也不回填旧信号。

入口使用 executable price：long 看 ask、short 看 bid。现价最差偏离不超过 `0.10R` 才允许立即单；否则只挂 frozen entry 一次，最多 900 秒，无 market fallback。挂单期间如果 executable exit price 已到 TP、先到 SL、15m 结构反向或 TTL 到期，必须取消并终结，不能在已经错过的价位追入。partial fill 只保护已成交数量并取消余单，不追满 100U。

成交后由 V2 独占 full-position TP1、SL 和 90 分钟 max-hold。`strategy_owner=tactical_v2` 持仓不得进入 Main partial TP、break-even、trailing、thesis invalidation、weakened/no-progress、Position Analyst close/reduce/add；全局 drawdown、flash move、保护完整性和人工 emergency 仍可通过 owner-bound safety path 全平，并记录 `risk_forced:<source>`。

V2 风控只暂停新开：滚动 24h final PnL `<= -15U`，或 3 次连续 final loss 触发 60 分钟暂停。pending/estimated/duplicate PnL 不计，correction 按同一 position 的最终值修正。execution/protection/ownership 无法证明时进入不自动过期的 integrity halt；已有持仓仍继续管理。禁止通过改状态文件、删除 event ledger、`/force_resume` 或重启来绕过 V2 integrity/cutover gate。

`entry_reconciliation_unknown` 和 `entry_cancel_unproven` 会由 Main 每 30 秒重做一次精确 `clOrdId` 证明；这是证据驱动的自愈，不是计时自动解除。任一 owner/order/position/quantity/TP/SL 不完整都必须继续 halt，且复查绝不得重提 entry。`entry_cancel_unproven` 必须保留原 cancel reason，后续仍见 open order 时继续撤单，不能退回普通 `pending_entry`；已被交易所终态取消且 `remaining_qty=0` 的订单必须收敛为 terminal `expired`。final PnL 先落 durable outbox 再发 bus，未 ack 会在重启后重发；无 `pnl_delivery_required` 的历史 correction 也必须先让 intent 收敛到 `closed_final`，再写 migration ack。governor/Reviewer/Judge 按 `resolution_id` 去重，但在“TG 已收到、outbox ack 尚未落盘”的崩溃窗口仍可能重复一次 TG 通知。

**历史部署与切换顺序（已完成；非当前操作指引，2026-08-12 NO-GO gate 优先；不要复制或执行）**：

```bash
# 1. 历史步骤：先跑 V2 shadow；sidecar 暂时保持 admission，便于同窗对照
# TACTICAL_TRACK_ENABLED=true
# TACTICAL_SHADOW_ONLY=true
# TACTICAL_V2_MODE=shadow
# BOT_INSTANCE_ID=main01
# SIDECAR_BOT_INSTANCE_ID=stlive

# 2. 至少采集 24h executable bid/ask 生命周期和 parity 证据
# python3 scripts/replay_tactical_v2.py \
#   --fixture tests/fixtures/tactical_v2_reproduced_window.json

# 3. 停止 sidecar 新开，但保持 resident monitor 管理旧 owner exposure
# python3 scripts/shadow_tactical_live_sidecar.py stop-admission
# python3 scripts/shadow_tactical_live_sidecar.py drain-report --namespace live

# 4. 只有 report complete=true 才归档；归档失败不得开启 live
# python3 scripts/shadow_tactical_live_sidecar.py \
#   drain-report --namespace live --archive

# 5. 历史切换步骤（当时已完成）；当前 gate 下禁止重新执行
# PROHIBITED / no-op under the current gate:
# TACTICAL_V2_MODE=live
```

shadow 观察至少记录开始/结束时间、重启次数、intent/episode 数、filled/non-filled、stale/invalid quote、parity category、snapshot freshness 和 integrity event。历史旧账本没有 bid/ask 或 15m token 的窗口只能标为不可执行回放，禁止补造价格。首轮少于 30 个 final episode 时只报告样本不足，不能据此扩仓或用旧 143 条重复 row 当作 143 笔交易。

**回滚**：把 `TACTICAL_V2_MODE` 改为 `off` 或 `shadow` 会阻止新 V2 admission，并只取消 ownership 可证明的 pending V2 entry；已经 protected 的 V2 仓必须继续管理直到 exchange-flat 和 final PnL。回滚绝不能把 sidecar `admission_enabled` 自动改回 true，也不能让 V2 接管 legacy sidecar owner row。

`/status` 的 `Tactical V2` 段只读 `data/tactical_v2_status.json`。`STALE` 表示快照缺失、超过 90 秒或 schema/数值不可信，不等于 circuit clear；`new admission PAUSED` 只阻止新仓；`integrity HALT` 必须对账后以完整 proof 清除。`Cutover: BLOCKED`、protection degraded/unknown 或未分类 parity mismatch 都不允许 live cutover。

快速查看当前仍在跟踪或已结算的 Tactical shadow 记录：

```bash
python3 - <<'PY'
import json
from pathlib import Path

path = Path("data/rejected_signal_lifecycle.json")
data = json.loads(path.read_text()) if path.exists() else {}
records = data.values() if isinstance(data, dict) else data
for r in records:
    if r.get("track") == "tactical" or r.get("exit_profile") == "tactical_v1":
        print(r.get("id"), r.get("symbol"), r.get("side"), r.get("status"),
              r.get("track"), r.get("exit_profile"), r.get("tactical_expected_value"))
PY
```

## Long Entry Position Guard

`Judge._check_entry_position_policy(symbol, action, plan, tech, score, context)` 是入场位置保护的**唯一入口**。主开仓路径与三条 deferred 路径（`deferred_15m_confirmation`、`deferred_pullback`、`deferred_chase`）必须共用该函数，禁止在 deferred helper 里再写一份 overheat 判定。

**触发条件**（`action=open_long` 且 `LONG_LIVE_POSITION_GUARD_ENABLED=true`）：

| 标记 | 触发条件 |
|---|---|
| `long_overheat_range_pos` | `position_in_24h_range >= LONG_LIVE_MAX_RANGE_POS` |
| `long_overheat_pre_move` | `pre_12h_return_pct >= LONG_LIVE_MAX_PRE_MOVE` AND `position_in_24h_range >= LONG_LIVE_DAILY_GAIN_RANGE_POS` |
| `long_overheat_daily_gain` | `prev_daily_return_pct >= LONG_LIVE_MAX_DAILY_GAIN` AND `position_in_24h_range >= LONG_LIVE_DAILY_GAIN_RANGE_POS` |

**处理策略**：
- 命中后不允许即时 `open_long`。
- 若 `target_price = max(stop_loss * 1.005, signal_price * (1 - max(LONG_LIVE_PULLBACK_MIN_PCT, atr_pct)))` 满足 `stop_loss < target_price < signal_price`，则创建 `deferred_pullback_overheat`（`chase_eligible=false`，timeout `LONG_LIVE_PULLBACK_TIMEOUT_HOURS`）。
- target 无效（数据缺失或区间冲突）时直接 hold/reject，`blocked_by=long_overheat_no_valid_pullback_target`。
- deferred 触发后必须重新执行：HTF 二次确认、15m 二次确认、R:R floor、EV gate、Entry Position Guard、slot gate/ranking；任一环节失败发布 hold/reject。

**Short side guard**：`open_short` 路径同样走 `_check_entry_position_policy`，复用现有 `SHORT_LIVE_*` 阈值（`range_position_too_low` / `pre_move_too_deep` / `rsi_too_low_for_short`）。

**EV bucket sparse-sample 保护**：
- `plan.entry_type` 在 EV gate 之前写入，避免 `unknown` bucket key。
- 当 bucket `trade_count < EV_BUCKET_MIN_TRADES` 时视为稀疏 bucket。`EV_BUCKET_SPARSE_ALLOW_UPLIFT=false`（默认）禁止稀疏 bucket 把 `p_win_used` 抬到当前值之上；可降低 p_win 或缩仓 60%。
- attribution 写入 `ev_bucket_key` / `ev_bucket_trade_count` / `ev_bucket_min_trades` / `ev_bucket_sparse`。

**约束**：
- 修改任何 entry position 阈值必须改 `Judge._check_entry_position_policy` 单一函数。
- `event_backtest.py` 同步同构验证（`long_live_position_guard_enabled` 默认 true）。
- attribution 字段 `entry_position_status` / `entry_position_block_reason` / `entry_range_pos_24h` / `entry_pre_12h_return_pct` / `entry_prev_daily_return_pct` / `entry_position_policy` / `deferred_target_price` / `deferred_reason` 落到 `trade_decision.attribution` 与 `data/journal/events_*.jsonl`。

详见 `docs/long_entry_position_guard_prd.md` 与 `docs/long_entry_position_guard_acceptance.md`。

## 配置文件

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
  max_trade_amount: 500          # 单次最大保证金（会被 .env 的 MAX_TRADE_AMOUNT 覆盖）
  max_drawdown: 0.20             # 最大回撤20%
  max_daily_loss: 300            # 每日最大亏损
  consecutive_loss_limit: 5      # 连续亏损熔断次数（默认 3，当前放宽到 5）
  ev_winrate_gate_enabled: false # 关闭后开仓门不用实际胜率(胜率低不拦)，EV门仍按R:R/成本（默认 true）
  # rotation_close_held_enabled: false  # 默认即 false：轮换不强平持仓标的（保护，出场交 PositionAnalyst）；设 true 回退旧强平
  ev_neutral_p_win: 0.55         # 关闭胜率门时 EV 公式使用的固定中性胜率

# 手续费
fees:
  binance: 0.001
  okx: 0.001
```

## 监控

### CF 实验室 / 策略诊断工具（observability-only，纯读，绝不改 config）

```bash
# CF 反事实方向推荐：扫 rr_floor/min_confidence 网格，报 baseline_fidelity + 方向 delta
python3 cf_direction_recommendation.py
#   2026-06-18 fix-cf-lab-fidelity-epoch-resolution 后 lab 恢复可信：
#   可信度看 accept/reject 二元保真（≥0.95），gate 严格保真仅诊断参考（对门归因短路顺序过敏）。

# lever1 增量分析：读 shadow_decision_log.jsonl 的 shadow_opens（lever1 解锁、实盘没开的单）
python3 cf_shadow_lever1_compare.py
#   样本薄经诚实门拒答。2026-06-18 实测 shadow_opens=0 → lever1 暂无上行证据，未上 live。

# 60 分边缘多单 PnL 跟踪：关联"边缘多单成交→已实现 PnL"，对比边缘60单 vs 信念≥70单
python3 scripts/track_marginal60.py            # 跨所有日累计
python3 scripts/track_marginal60.py 20260618   # 指定日
#   衰减期关 EV 胜率门后，放行多为 confidence=60 门槛线的 rule_signal 多单；
#   看『边缘60单』均PnL是否持续为负——若是=放水，考虑收紧（重开胜率门/提 min_confidence）。
```

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

### 问题：保护单残留 / Judge cooldown 被风控强平污染（2026-05-28 P0 修复）

**症状**：close 后 OKX 仍能查到 owner-tagged trigger algo；或风控强平 / 全平 / 价格失败也被 Judge 计入 SL hit，导致 escalating cooldown 误升。

**根因**：
- 旧 Agent close path 在多个分支（trade_decision close、risk_alert、close_all、local_stop）直接 `cancel_order(sl_order_id)`，与 root `close_position()` 内部清理重复或冲突。
- Judge 旧逻辑在 `status == 'force_closed'` 即调 `_record_sl_hit`，没有区分 close cause。

**状态**：2026-05-28 已修复，PRD/验收 见 `docs/audit_remediation_20260528_prd.md` / `docs/audit_remediation_20260528_acceptance.md`：
- `agents/trading/executor.py` 全部 close 分支改成只调 `executor.close_position(symbol)`；保护单 cancel + orphan algo sweep 由 root `_cleanup_protective_orders_on_close()` 完成，结果挂在 `result.protective_cleanup_state ∈ {cleaned/none/failed/unknown}`。
- `_build_execution_result()` 在 close action 自动注入 `exit_reason / close_cause / is_strategy_stop / is_risk_forced`，由 `_classify_close_cause(source, reason)` 单一函数生成。
- Judge `force_closed` / `closed_externally` 分支必须用 `payload['is_strategy_stop']` 门控 `_record_sl_hit()`；老 payload 缺字段时 fail-safe 不计 SL。

**复检**：
```bash
rg -n "cancel_order\(" agents/trading/executor.py    # 应该只剩 helper / sweep 内部引用
rg -n "is_strategy_stop|_record_sl_hit" agents/trading/judge.py
python3 -m pytest -q test_protective_sl_owner.py test_judge_close_cause.py
```

---

### 问题：TG 显示熔断，但 Tactical V2 未暂停

**症状**：`/status` 显示全局熔断或旧的 `okx_sl_algo_unresolved:<symbol>` 原因，容易误判为 Tactical V2 连亏暂停；或 V2 段显示 `STALE`，却被误读为 circuit clear。

**状态**：2026-07-15 `protective-sl-halt-recovery` 已修复：
- OKX attached SL 首次回查不到 `algoId` 时，Executor 先做有界验证；验证找到 SL 就标 `protection_state=protected`，不写终态保护单 halt。
- allowlist 保护单原因（`okx_sl_algo_unresolved:<symbol>` / `migrate_missing_sl`）只在对应仓位已关闭或已恢复保护，且没有其它 unresolved protection halt 时自动清除。若还有另一个 unresolved symbol，全局 halt 保持 active 并 repoint 到那个 symbol。
- manual halt、daily hard stop、reconciliation mismatch、未知原因不走自愈，仍需 `/resume` 或 `/force_resume`。
- `/status` 分开显示全局 halt、per-symbol halt 和 Tactical V2 circuit；V2 只读 `tactical_v2_status.json`，不回退到 V1 `riskguard_state.tactical_circuit`。`STALE`/unknown 不是 healthy，也不能作为开 live 的依据。

**复检**：
```bash
python3 - <<'PY'
import json
from pathlib import Path
for p in ["data/halt_state.json", "data/tactical_v2_status.json", "data/agent_health.json"]:
    data = json.loads(Path(p).read_text()) if Path(p).exists() else {}
    print(p, {k: data.get(k) for k in ("halted", "reason", "mode", "updated_at", "timed_pause_until", "integrity_halt", "halted_symbols")})
PY
python3 -m pytest -q test_halt_resume_ownership.py tests/test_phantom_position_resync.py test_tg_status_enhancement.py
```

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

**错误51169：仓位方向不匹配 / 错误51205：reduceOnly 无对应持仓**
- 原因：本地认为有持仓但交易所已无（被 SL 触发或被手动平仓），或 posMode 与下单参数不匹配
- 处理（2026-05-25 起代码已落地）：Executor 不再无限重试，改为 `_handle_okx_close_reject` 状态复核：
  - `already_flat`：本地无仓位 + 交易所无 → 清理 idempotency
  - `external_closed`：本地有仓位 + 交易所无 → 标记为已被外部平仓，清理本地 + cooldown 60s
  - `still_open`：本地有 + 交易所有 → 保留本地，halt symbol 阻止重复提交
  - `direction_conflict`：方向冲突 → 保留本地状态，halt symbol，等待人工介入
- 如反复出现：核对 `executor._okx_pos_mode` 与 OKX 账户配置一致；如 live 探测失败要重启进程

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
  -d '{"model":"claude-opus-4-6","max_tokens":20,"messages":[{"role":"user","content":"hi"}]}'
```

### 问题：数据库锁定

**症状**：`database is locked`错误

**解决**：
```bash
# 停止所有运行实例
pkill -f run_agents.py

# 重启
python3 run_agents.py
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
