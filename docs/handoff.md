# 项目交接文档

> 本文件是**完整历史演进与里程碑**的家。每个阶段只留 1–2 句要点与彼时测试基线（历史快照，非当前基线）；逐项实现细节见对应 `docs/*_prd.md` / `docs/*_acceptance.md` / `docs/superpowers/specs/*-design.md` / `docs/audit_remediation_*`。当前事实与硬约束见 `CLAUDE.md`，当前待办见 `docs/to-do-list.md`。

## 项目状态

**开始日期**：2026-05-06
**当前阶段**：2026-06-11 第五次审计阻断项（P1-01 加仓 TP 自我熔断 / P1-02·P1-03 短单 gate or-falsy + 单点收口 / P2-02）+ 6 项 fail-closed 加固，其后再合并 ccxt keysort 崩溃修复（OKX null-id 市场致 `load_markets` 崩溃，恢复 3860 markets）+ Agent 故障可见性（setup 失败打 traceback + `agent_task_failed` 去重告警）两 change，全部合并入 main；2026-06-12 再加 OKX 持仓同步瞬时重试（`sync_positions` 对 `ccxt.NetworkError` 有界重试，止 ERROR 刷屏），全量实测基线 `1102 passed / 4 deselected / 1 warning`。在此之前已完成：第四次审计 F4-001/002/003（2026-05-29 闭环，真实 OKX owner-tag T0/T1/T6 PASS）、TG Graceful Ops（`/halts` `/resume_symbol` `/pnl` `/pnl_id`）、Entry Drift Hybrid Policy、Pullback Entry Paper Parity、Short Main Path Risk Guard Parity、研究层低流动性硬过滤器、Paper Dual-Track Simulation（`/paper_gap`）、Data Source Provenance。
**下一阶段**：live 扩容为 CONDITIONAL GO。扩容前需将 `BOT_INSTANCE_ID` 写入 systemd / pm2 启动配置，完成真实 TG 命令链与 drift gate 运维验收，并继续每日复核 `data/live_position_lifecycle.json` 与 OKX algo 残留。

## 重大决策：放弃套利策略（2026-05-06）

跨交易所套利经全面验证不可行：REST 扫描 122 币种 196 次 0 机会、WebSocket 30min 0 机会、三角套利 565 组合 0 机会、深度验证全为负。根因——市场效率极高价差被瞬间抹平，成本（手续费 0.2% + 滑点 0.1%）> 价差，HFT 公司占速度/费率优势。转向**趋势交易 + 合约**（可多空、机会更多、利用 AI 做信号）。套利代码归档保留，见 `docs/architecture.md ## 套利系统归档说明`。

## 已完成功能

### ✅ Phase 1: 套利策略验证（2026-05-06）
行情聚合器 / 套利检测引擎 / 深度验证器 / 市场扫描器 / WebSocket 监控 / 三角套利检测全部跑通但 0 机会，确认策略不可行。

### ✅ 新方向：趋势交易系统（2026-05-06 完成 MVP 核心）
K 线采集（`kline_collector.py`）+ 技术指标（`indicators.py`）+ Freqtrade 式策略基类（`strategy_base.py`/`optimize_1h.py`）+ 回测引擎（`backtest.py`）+ 样本外验证。关键发现：1h 周期最优、反欺骗机制把胜率从 46.67% 提到 83.3%、最佳参数 MA 7/25 + RSI 75 + 量因子 1.0。

### ✅ Phase 3: 实盘交易系统（2026-05-06 完成）
`risk_manager.py`（余额/回撤/日亏限制 + 多空 SL/TP + 峰值持久化）+ `executor.py`（CCXT 统一接口、OKX posMode-aware 参数构造、杠杆、盈亏含杠杆、持仓持久化）+ `live_trading.py`（单策略入口，**现已 deprecated**）+ `verify_*.py` 15/16 通过。OKX 真实账户连通。

### ✅ Phase 5: 多 Agent 系统（2026-05-07 完成）
- **5a 基础框架**：消息总线（asyncio Queue + topic:symbol 路由 + 广播隔离）、Agent 基类、Claude LLM 客户端（OpenAI 兼容中转 + 限流重试）、编排器（两层生命周期 + 优雅退出）。
- **5b 研判层 6 Agent**：MarketScanner（OKX 324 合约扫描）/ SentimentResearcher（恐贪 + CoinGecko + Taker 比）/ NewsResearcher（6 家 RSS）/ Synthesizer（两阶段初选→终选）/ Censor（言官逆向审查）/ SymbolRouter（标的轮换）。
- **5b 交易层**：MultiDataCollector（9 维度分频采集）/ MultiTechAnalyst（9 维度信号 + 规则层 + LLM 层）/ MultiJudge（7 维度加权评分 + 交易计划 + 反欺骗）/ MultiExecutor / PortfolioRiskGuard（6 维风控 + 状态持久化）/ ReviewerAgent（历史追踪 + Daily Hard Stop）。
- 关键决策：LLM 不可用规则降级；两阶段研判防过度自信。

## 待开发功能

> 下列 Phase 6/7 及各轮审计均已完成（保留历史小节标题）。

### ✅ Phase 6a: Telegram 通知（2026-05-07）
`TelegramNotifier`：实时推送 + 每日摘要 + 零配置降级 + 1 msg/s 限流。

### ✅ Phase 6b: 关键 Bug 修复（2026-05-08）
contractSize 修复（`amount = size_usdt*lev/(price*contract_size)` + `amount_to_precision`）；Judge 杠杆上限对齐 OKX `[1,2,3,5,10,20]`。

### ✅ Phase 6d: 方向决策修复（2026-05-08）
根因：RSI 极端超卖区做空连亏。`_compute_score` 重写——RSI 硬性保护（<25 禁空 / >75 禁多）+ 趋势强度衰减 + 散户反指条件化 + RSI 背离权重 +15→+35 + prompt 加 RSI 禁令。

### ✅ Phase 6e: Post-mortem + 入场质量优化（2026-05-09）
`correlation_risk` 改用保证金计算、force_close 300s 冷却；R:R<1.5 强制 hold、负面催化剂否决（近 4h hack/监管关键词 → confidence=0）、30min 新闻轮询、price-in 检测（有新闻 + 同向 >3% → score×0.5）。

### ✅ Phase 6g: Judge 主驱动修复（2026-05-09）
rule_signal（回测 83% 胜率 MA 交叉）给 ±35 基础分过门槛；LLM 从一票否决改为仓位修正（最多降 30%）。

### ✅ 2026-05-09 Bug 修复
`RobustStrategy` 补做空 4 重确认 + `exit_short`；ticker 统一永续格式 `BASE/USDT:USDT`；日线阻力区阈值 3%→1.5%。

### ✅ Phase 6h: MA alignment 信号 + Symbol sync 修复（2026-05-11）
新增 `ma_aligned_long/short`（对齐 ≥3 根）给 ±20 次驱动分（修 crossover 点事件导致永久 hold）；`sync_positions` 统一 `BASE/USDT:USDT`→`BASE-USDT-SWAP`（修每次 sync 重建丢 SL/TP）；SL 距离 ATR 封顶 2.5×（max 5%）+ TP 下限 SL×1.5（2026-05-13）。

### ✅ Phase 6i: 持仓管理三角决策 + flash_move 修复（2026-05-12）
PositionAnalyst（6 因子评分 + 5 条硬覆盖 + 4 级裁决矩阵）+ BehavioralCritic（LLM 检测 7 种认知偏差，规则降级）；flash_move 改为只平触发标的；交易层 Agent 7→9。

### ✅ Phase 6j: 持仓防遗憾优化 + Telegram 远程命令（2026-05-13）
PA 周期 30min→2h、新增 `entry_thesis_intact`（HTF 方向保护）、动作阈值放宽；TG 远程命令（/status /positions /stop /restart /halt /resume /log，经消息总线路由）；`/restart` 写 flag + `os.execv` 置换镜像。

### ✅ Phase 6k: 回调入场 + Censor 超时 + Executor margin 修复（2026-05-14）
回调入场三级响应（R:R≥1.5 正常 / 1.2–1.5 追价 / 弱信号等回调 3h / <1.2 放弃）+ deferred_entry 状态机；Censor BATCH_SIZE=4 分批（修 Cloudflare 100s 超时）；`required_margin = size_usdt`（修语义）。

### ✅ Phase 6l: HYPE 重复做空事故修复（2026-05-15）
5 层防护（日线强趋势中 RSI 背离降权 / 无 rule_signal 门槛 25→40 + confidence 上限 / 开仓 300s 冷却 / 开仓失败 120s 冷却）+ 下单前 SL/TP 方向校验。

### ✅ Phase 6m: 加仓/减仓功能修复（2026-05-15）
PositionAnalyst add 信号 → `add_to_position()`（加权均价、SL/TP 比例重算、保证金上限 ×2）；reduce 信号尊重 size_pct → `reduce_position()`（先撤旧 SL）；execution_result 增 `is_add`/`risk_reduced` 状态。

### ✅ Phase 6n: PA 动态阈值 + Close 冷却 + Telegram 去重（2026-05-15）
PA Rule 1/3b 阈值改用 SL 含杠杆距离（修 ZEC 10x 误平）；close_position 后 60s 冷却（修 sync 重建循环）；TG 过滤 source=sync + 60s 去重。

### ✅ Phase 6o: Symbol 格式统一修复（2026-05-15）
execution_result handler 入口 strip `-SWAP`（修 Judge/PA/RiskGuard 用错 key 导致冷却失效、幽灵持仓）。

### ✅ Phase 6p: PnL 追踪 + 递增冷却 + 上线时间过滤（2026-05-17）
closed_externally 始终算 close_profit；StoplossGuard 4h 窗口递增冷却 300→600→1200→3600s；研判层排除上线 <1 年标的；Synthesizer 终选保底（<非 reject 半数时补充）；Logger 防重复。

### ✅ Phase 7: 4h RSI 衰减 + 逻辑账户拆分 + Paper Trading（2026-05-19）
4h RSI 二级保护（1h 未触发但 4h ≥70/≤30 时 score×0.5，修 ZEC -135U 事故）；逻辑账户拆分（`EFFECTIVE_BALANCE_CAP`，真实 6020U 按 1000U 风控）；Paper Trading 全并行（`paper_executor.py`，独立 `paper_execution_result` 不污染实盘）；交易层 9→10。

### ✅ 第五~七轮审计修复（2026-05-19）
订单预检覆盖全部 5 个 create_order 落点；默认 pytest CI 口径（`-m "not network"`）；`_get_balance()` 实数校验；event_backtest 权益曲线前视偏差修复；PaperExecutor 原子写入；`live_trading.py` 标 DEPRECATED。

### ✅ 最终审计收尾 1+2（2026-05-20）
15m 入场用已闭合 K 线；Judge Ranking Top-N + pending TTL 120s sweep；LiveLedger `record_add()` 加权均价；Reconciler 每 10min 运行期对账 + 偏差发 risk_alert；Synthesizer 按 cycle_id 分桶缓存（修跨轮丢 sentiment/news）；`RANK_FLUSH_DELAY`/`MAX_CONCURRENT_POSITIONS` 配置化。彼时基线 373 passed。

### ✅ Phase 8: 市场 Regime 优化（2026-05-21）
RegimeManager（bullish/bearish/mixed/choppy + 2 次确认 + 30min min_hold）、CounterfactualLedger（被拒信号影子追踪）、Short Regime Guard（牛市强空才放行）、Probe Short（牛市小仓探针）、Dynamic R:R、Low R:R Extra Slot；全部 feature-flagged。彼时基线 293 passed。

### ✅ R:R Floor Policy 修复（2026-05-26）
单一函数 `Judge._select_rr_floor`，主路径与 `_apply_regime_policy` 共用，五分支（probe/long_bullish_low_rr/long_aligned_low_rr/short_bullish_strong/default）+ 新策略 `long_aligned_low_rr`（mixed/choppy 强一致多头 1.30 floor 进 low_rr_extra slot）+ attribution 全链路。彼时基线 551。详见 `docs/rr_floor_policy_prd.md` / `_acceptance.md`。

### ✅ Long Entry Position Guard（2026-05-26）
单一函数 `Judge._check_entry_position_policy`，long overheat（range_pos/pre_move/daily_gain 三阈值）+ short side guard 主路径生效，四路径（主 + 三 deferred）共用；EV bucket key 修正（消除 unknown + sparse 不 uplift）。根因 NEAR 山顶追多。彼时基线 575。详见 `docs/long_entry_position_guard_prd.md` / `_acceptance.md`。

## 后续里程碑（2026-05-27 之后，逐项见各 design/audit 文档）

| 里程碑 | 完成 | 要点 | 彼时基线 | 文档 |
|---|---|---|---|---|
| 分批止盈生命周期收敛（1+2+3） | 2026-05-27 | TP/SL owner 收敛、`_replace_protective_sl` 单一入口、重启 algo 迁移 | 618 | `docs/partial_tp_lifecycle_*` |
| OKX 真实 testnet 语义验收 | 2026-05-27~28 | T0–T15 真实链路；`cancel_algos` 序列化 bug（mock 不可覆盖） | — | `docs/generated_reports/OKX执行语义testnet验收报告_*` |
| 真实已实现 PnL 账本 Phase 1+2+3 | 2026-05-28 | `realized_pnl_resolver` 唯一 OKX fills+bills 入口、dual-payload pending→final、backfill 脚本 | 711→727 | `docs/exchange_realized_pnl_ledger_*` |
| 第三次审计 P0/P1/P2 整改 | 2026-05-28 | reduce fail-closed / owner-bound cleanup / close evidence / 新闻 ticker 边界匹配 | 807 | `docs/audit_remediation_third_pass_20260528_*` |
| 第四次审计 F4-001/002/003 | 2026-05-29 | reduce 失败传播单点契约 / pnl_resolved 证据 + 幂等链 / owner-tag clOrdId 真实 SL 下单 | 860 | `docs/audit_remediation_fourth_pass_20260528_acceptance.md` |
| TG Graceful Ops | 2026-06-01 | `clear_symbol_halt` + `/halts` `/resume_symbol` `/pnl` `/pnl_id` + agent_health 快照 | 921 | `docs/audit_remediation_tg_graceful_ops_acceptance.md` |
| Entry Drift Hybrid Policy | 2026-06-01 | 单一 `_classify_entry_drift` 4 档 gate（双 Gate 基准恒为原 entry_ref）+ `_set_position_tp` 单一收口 | 954 | `docs/superpowers/specs/2026-06-01-entry-drift-hybrid-policy-design.md` |
| Pullback Entry Paper Parity | 2026-06-03 | Paper 限价撮合对齐 live（`_pending_limits` + `_wait_paper_limit_fill`，仅 in-memory） | 993 | `docs/superpowers/specs/2026-06-03-pullback-entry-paper-parity-design.md` |
| Short Main Path Risk Guard Parity | 2026-06-05 | 短单结构性 gate 收敛到单一 `_classify_short_entry_risk`，main + deferred 三路径共用 | 1010 | `docs/superpowers/specs/2026-06-05-short-main-path-risk-guard-parity-design.md` |
| 研究层低流动性硬过滤器 | 2026-06-07 | `MarketScanner._apply_liquidity_hard_filter` volume+OI 双 gate、缺 OI fail-closed（BABY-USDT 事件根因） | — | `docs/superpowers/specs/2026-06-07-research-liquidity-hard-filter-design.md` |
| Paper Dual-Track Simulation | 2026-06-10 | PaperExecutor `book ∈ {realistic, idealized}` + `/paper_gap`，量化限价漏单成本（不进 live Reviewer） | 1035 | `docs/superpowers/specs/2026-06-10-paper-dual-track-sim-design.md` |
| Data Source Provenance | 2026-06-10 | 跨源 `source/freshness_sec/confidence` 穿透至 tech_analysis + Judge attribution + Reviewer 分桶（observability-only） | 1066 | `docs/superpowers/specs/2026-06-10-data-source-provenance-design.md` |
| 第五次审计 P1-01/P1-02/P1-03/P2-02 + 6 项 fail-closed 加固 | 2026-06-11 | 加仓 TP 单点收口防自我熔断 / 短单 gate or-falsy 哨兵合并 + 单点收口 / resume 语义诚实回显 / DLQ 告警 / config clamp / fsync / 原子写 | 1088 | `docs/generated_reports/系统性审计报告_20260610_第五次.md` + `docs/superpowers/specs/2026-06-11-*-design.md` |
| ccxt keysort 崩溃修复 + Agent 故障可见性 | 2026-06-11 | `utils/ccxt_compat.py` 容 None 键 shim 修 OKX null-id 市场致 `load_markets` 崩溃（恢复 3860 markets）/ `base.run()` setup try-except 打 traceback / orchestrator 对失败 agent 任务发去重 `telegram_alert{agent_task_failed}` | 1098 | comet changes `fix-data-collector-ccxt-keysort-crash`、`agent-fault-visibility`（master spec `exchange-client-resilience` / `agent-fault-visibility`） |

## 技术债务

历史已修复项（R:R 计算、套利代码归档、异常处理粒度）见各阶段记录。**当前活跃技术债与后续优化统一维护在 `docs/to-do-list.md`**（如 `ContractExecutor` exchange 创建收敛到 factory、Binance legacy path 标识、文档瘦身、LLM audit 脱敏策略、Judge 弱信号降权等），不在本文件分叉维护。

## 关键决策记录

### 方向转变（2026-05-06）

| 决策 | 原因 | 影响 |
|------|------|------|
| 放弃套利策略 | 所有测试 0 次机会，成本>收益 | 重新设计系统架构 |
| 转向趋势交易 | 更适合技术栈和资金规模 | 采用 MVP 方式，1-2 周完成 |
| 使用合约交易 | 可以做多做空，机会更多 | 需要学习合约 API |

### 技术选型

| 决策 | 选择 | 原因 |
|------|------|------|
| 交易所 API 库 | ccxt | 统一接口，支持 200+ 交易所 |
| 数据库 | SQLite | 本地运行，无需额外安装 |
| 异步框架 | asyncio | Python 内置，适合 IO 密集 |
| LLM 调用 | OpenAI 兼容中转 | 绕过 Cloudflare Bot 防护，规则降级兜底 |

## 已知问题

当前已知问题与阻断项统一见 `docs/to-do-list.md`（含 live 扩容前置、OPEN 调参项与各次审计闭环状态）。早期套利相关的"价差不足"等问题随策略转向已不适用。

## 环境配置

- **运行时**：Python 3.9+ / pip3
- **依赖**：见 `requirements.txt`（ccxt / pandas / python-dotenv / pyyaml / openai / anthropic）
- **可选**：交易所 API 密钥（执行交易必需；无密钥时仅采集公开行情）

## 运行指南

```bash
pip3 install -r requirements.txt
cp .env.example .env          # 编辑 .env 填入 API 密钥
python3 verify_system.py      # 基础验证
python3 run_agents.py         # 生产入口（或 ./start.sh）
# live_trading.py / main.py 已 deprecated，仅作单策略调试参考
```

## 文档位置

- **项目约定与硬约束**：`CLAUDE.md`
- **当前待办与阻断项**：`docs/to-do-list.md`
- **架构设计**：`docs/architecture.md`
- **运维手册**：`docs/runbook.md`
- **集成指南**：`docs/integration-guide.md`
- **本文档（历史演进）**：`docs/handoff.md`
