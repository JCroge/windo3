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
# 系统写入 data/.restart_flag 后优雅退出，run_agents.py 检测标记后通过 execv 重启解释器
```

> `/restart` 现在会在优雅停机后执行 `os.execv(sys.executable, [sys.executable] + sys.argv)`，重新加载磁盘上的源码。`execv` 置换的是当前进程镜像，所以 **PID 可能保持不变**，这是正常现象；关键是 Python 解释器和 `sys.modules` 会被重建。若变更的是 Python/venv/系统级依赖，仍建议外部重启进程：
> ```bash
> kill -TERM $(pgrep -f run_agents.py) && sleep 5
> nohup python3 run_agents.py > logs/launcher_$(date +%Y%m%d_%H%M%S).log 2>&1 &
> ```
> 验证新代码上线（OKX 路径）：日志出现 `[OKX posMode] 探测成功: net_mode/long_short_mode (testnet=...)`。

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
python3 -m pytest -q              # 618 passed / 4 deselected / 1 warning（2026-05-27，含 partial TP lifecycle 32 case + Long Entry Position Guard 23 case + R:R Floor Policy 20 case + OKX posMode 38 case）
python3 -m pytest -q -m network   # 仅跑 network 测试（需 data/klines.db 和实时网络）

# OKX 真实 testnet 端到端语义验收（需 .env.testnet 隔离凭证）
python3 verify_okx_testnet_semantics.py   # mock 矩阵 10 case，CI 一定要先过这个
python3 verify_okx_testnet_real.py        # 真实 OKX testnet T0-T9，2026-05-27 7 PASS / 3 SKIP
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
| OKX_POS_MODE_OVERRIDE | OKX posMode 兜底覆盖（仅 testnet 生效，可选 `net_mode` / `long_short_mode`）。live 永远以 `private_get_account_config` 返回为准；testnet 拿不到时若设此值则使用，否则降级 `net_mode` 并 warning | （未启用） | 否 |
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
| EV_BUCKET_MIN_TRADES | bucket 提高 p_win 所需最小样本数（低于此值视为稀疏 bucket） | 10 | 否 |
| EV_BUCKET_SPARSE_ALLOW_UPLIFT | 是否允许稀疏 bucket 抬高 p_win（默认禁止，仅允许降低/缩仓） | false | 否 |
| TELEGRAM_BOT_TOKEN | Telegram Bot Token | - | 否（通知） |
| TELEGRAM_CHAT_ID | Telegram Chat ID | - | 否（通知） |

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
  -d '{"model":"claude-opus-4-7","max_tokens":20,"messages":[{"role":"user","content":"hi"}]}'
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
