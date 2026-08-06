# 项目交接文档

> 本文件是**完整历史演进与里程碑**的家。每个阶段只留 1–2 句要点与彼时测试基线（历史快照，非当前基线）；逐项实现细节见对应 `docs/*_prd.md` / `docs/*_acceptance.md` / `docs/superpowers/specs/*-design.md` / `docs/audit_remediation_*`。当前事实与硬约束见 `CLAUDE.md`，当前待办见 `docs/to-do-list.md`。

## 项目状态

**开始日期**：2026-05-06
**当前状态（2026-08-06）**：Tactical V2 已完成 shadow gate、sidecar drain 和首轮 live cohort，云服保持 `LIVE 100U x 3`；Sidecar 为 resident monitor 且 admission 关闭。最新修复包括精确 `clOrdId` 入口回查、取消终态自愈、保护 halt 自愈/旧 halt 迁移和重启后 durable final-PnL replay。云服快照与回滚证据见 `docs/superpowers/reports/2026-07-28-promote-shadow-tactical-v2-live-verify.md` 和 `docs/superpowers/reports/2026-08-06-fix-tactical-canceled-entry-self-heal-verify.md`。
> 下一行起的长段是历史阶段累计记录（截至 2026-06-11 的审计流水），不代表当前线上状态；当前事实以本段、`CLAUDE.md` 和 `docs/to-do-list.md` 为准。
**历史阶段摘要（截至 2026-06-11，非当前）**：2026-06-11 第五次审计阻断项（P1-01 加仓 TP 自我熔断 / P1-02·P1-03 短单 gate or-falsy + 单点收口 / P2-02）+ 6 项 fail-closed 加固，其后再合并 ccxt keysort 崩溃修复（OKX null-id 市场致 `load_markets` 崩溃，恢复 3860 markets）+ Agent 故障可见性（setup 失败打 traceback + `agent_task_failed` 去重告警）两 change，全部合并入 main；2026-06-12 再加 OKX 持仓同步瞬时重试（`sync_positions` 对 `ccxt.NetworkError` 有界重试，止 ERROR 刷屏）+ Agent Health Supervisor（四维度健康聚合 + `/health` + 边沿告警，observability-only）+ tick-loop 挂死检测（`agent-tick-stall-detection`，loop_health 加 tick-stall）；2026-06-13 再加 bot LLM env 隔离（`bot-llm-config-isolation`，bot 改读 `BOT_LLM_*` 与 Claude Code 的 `ANTHROPIC_*` 解耦，+3 隔离测试）；2026-06-13/14 建成**反事实策略实验室 L1-L4**（5 个 comet change：L1 `counterfactual-replay-foundation` 决策磁带埋点+可信被拒单PnL+1s tick+诚实gate / L2 `deterministic-replay-golden-master` 真实Judge即回测引擎+确定性回放+golden三层比对（修了accepted-open回放CRITICAL与RegimeStub发散）/ L3a `perturbation-replay-per-decision` 逐决策扰动gate翻转 / L3b `sequential-portfolio-perturbation` 序列组合态重演整策略delta（修了per-record注入reality计数掩盖级联的CRITICAL）/ L4 `perturbation-knob-sweep` 旋钮扫描+诚实方向推荐含多重比较守卫，全 observability-only write-only，复用真实Judge代码零发散，已 push windo3），全量实测基线 `1223 passed / 4 deselected / 1 warning`；2026-06-15 兑现实验室时发现空转根因——Judge 录制点把决策磁带 `tech_analysis`/`llm_output` 写死为空致全部磁带不可回放（L2 fidelity 虚高、L4 delta=0），已修复（`decision-tape-capture-fix`：经专属侧信道 `_symbol_llm_cache` + `_symbol_tech_tape_cache` 捕获真实 tech+llm，`replayable` 收紧为有快照且 tech 非空，schema v2，observability-only 绝不写 live `_symbol_tech_cache`；OS 重启后生产实测生效），基线升至 `1234 passed`，旧磁带永久不可回放、需新磁带累积后用 `cf_direction_recommendation.py` 重跑方向推荐；同日再合并 hotfix `tick-capture-retention-prune`（OneSecBarStore 接通 retention prune，klines_1s.db 默认 30 天有界，observability-only/fail-safe），基线升至 `1238 passed`；**2026-06-16 兑现反事实实验室时连揪三层隐藏 bug 并连修（均 comet 归档入 main，observability-only），使 L3b 端到端首次跨可信线**——(1) `fix-cf-lab-ev-coldstart-deadlock`（基线→`1247`）：CF EV-gate 因 win-rate 语义错配（`to_snapshot` 把 `_recent_win_rate` 派生为 wins/total 而非 Reviewer 滚动率）冷启动死锁，修为 CF rolling 胜率窗口镜像 Reviewer 20 窗口 + 暖启动播种 + `baseline_fidelity` 改 gate-level 比对 + driver v2 过滤；(2) `fix-cf-lab-replay-config-parity`（基线→`1252`）：replay 用空 config 致 Phase-2 flag 默认 False vs live True 致 confidence 路径发散，修为 replay 用生产 config 基线 `production_base_config()` + 决策磁带录 `config_snapshot`（schema v3），直接 L2 fidelity 0.34→0.914；(3) `fix-cf-lab-symbol-state-injection`（基线→`1255`）：`_inject_cf_state` 把 `_symbol_state` 清空致信号强度路径饿死（`trend_streak`/`last_tech` 缺失），修为还原录制 `_symbol_state`（A-minimal 一行），sequential baseline_fidelity 0.798→0.944。**驱动 `cf_direction_recommendation.py` baseline_fidelity 1.0(虚假死锁)→0.34→0.798→0.944（untrustworthy=False）首次可信**；首个可信结论：放宽 choppy R:R 地板/`min_confidence` 的 PnL delta≈0 → 非高价值杠杆，独立佐证地板 1.50 维持。归档时 comet-archive `|| cp` 盲覆盖 bug 复发一次（config-parity 4 master spec 被砍，核 requirement 数发现并 restore+append 修正）；**2026-06-16 再加 `joint-knob-sweep`（基线→`1270`，comet 归档）**：多旋钮笛卡尔积联合扫描（`utils/joint_knob_sweep.py`：`sweep_grid` + `compute_interactions` 2-way 因子交互 + `recommend_direction_nd` 多维孤峰守卫），真跑 853 条 fidelity 0.947 **全 additive 无交互**——rr_floor × min_confidence 联合放宽翻转 90% gate-label 仍 PnL delta=0/CF opens=2，证伪"被另一门掩盖"假设，独立佐证地板 1.50 维持；**2026-06-17 `trend-entry-rr-fidelity`（基线→`1285`，comet 归档，2 新 capability `trend-aligned-rr-floor`+`ladder-weighted-rr`）**：诊断"近三天对 4 个干净趋势（HYPE/WLD/UNI long、NEAR short，沿途逆行仅 0.1–0.3R/峰值 1.9–9.5R）零开仓"，根因双杠杆（regime 判 choppy + bias 漏报 → 拿 default 1.50 而非 long_aligned 1.30；`effective_rr` 只数 TP1 而 executor 实际 50/25/25 阶梯离场）→ 实现两入场杠杆 ① `_select_rr_floor` path-evidence OR 分支（禁前视，policy `long_aligned_path_evidence`，已接两处 `low_rr_policies`）/ ② `_compute_ladder_rr` 离场比例加权（Option B 无概率折扣——概率折扣只缩分子不缩阶梯化后风险分母会反向压低 R:R，初版把 HYPE R:R 1.14 压到 0.86 被证伪后改）；**两个 config 开关 `path_evidence_aligned_enabled`/`ladder_rr_enabled` 均默认关、实盘零影响**；CF 重放四臂 A/B inconclusive（lever1 目标人群=干净趋势+中性 bias 在磁带为空 / lever2 旋钮生效但 CF 退出无阶梯+组合 slot/EV 只开 2 仓 delta0），lever2 在 `rejected_signal_events` 流忠实 A/B 单笔含亏单净 +0.21R/簇但样本薄（13% 覆盖/近 3 天）→ 保持默认关，加 `tech_context` 埋点供 lever1 日后验证；后续拆出 ① P2 bias 根治 / ① lever1 A/B / ② v2 概率校准 / ② 组合 slot/EV 瓶颈诊断；**2026-06-17 当天在 1285 之上连归 4 个 comet change（基线→`1302`）并重启 live 加载新代码（PID 46766，~20:45，资金 cap 仍 300）**：(1) `cf-lab-driver-portfolio-param-parity`（+0，CF 分析驱动组合参数对齐 live −300/300，observability）——同会话诊断「CF opens 恒 2」**证伪"组合 slot/EV 瓶颈"假设**（仪表化实证 slot_full=0/day_halted=0 组合门从不触发，真因=入场旋钮造不出新 accept 的 over-determination + ADA 保真残差去重成 2，最强独立坐实地板 1.50）；(2) `trend-entry-levers-default-on`（+3，**lever2 阶梯 effective_rr 口径修正默认开——首个真·改 live 开仓决策**）——lever2 定价坐实**是 bug 非赌**（被拒干净趋势 P(达TP2)=68%/R:R 频率不敏感/rejected 流 A/B 含亏单 +0.181R/簇），翻默认副作用=打破翻转前磁带回放保真（3 config-parity 守卫 pin `ladder_rr_enabled=False` 钉旧纪元），env `LADDER_RR_ENABLED=false` 可回滚；(3) `trend-entry-shadow-decision-logger`（+10，**前向影子决策记录器 observability-only 不碰 live**）——复用 `replay_decision` 在决策磁带 chokepoint 旁路跑 both-levers 影子决策，write-only 记 real(lever2-only) vs shadow(both)=lever1 增量到 `shadow_decision_log.jsonl`，fire-and-forget fail-safe，填 lever1 path-evidence 数据墙；(4) `fix-lever2-low-rr-sizing-tp1`（+4，hotfix）——code review 揪出 lever2 把低-R:R 趋势单从保护性缩仓松绑成全仓满杠杆（阶梯抬高 effective_rr 误喂缩仓判定），修为缩仓判定用 TP1 口径 `effective_rr_tp1` + 提取单一收口 `_apply_low_rr_sizing`（地板 gate 仍用阶梯多开仓不变）。lever1 仍默认关，下一步=影子前向攒 `shadow_opens` 样本→`cf_shadow_lever1_compare.py` 看 lever1 增量→决定是否上 live。在此之前已完成：第四次审计 F4-001/002/003（2026-05-29 闭环，真实 OKX owner-tag T0/T1/T6 PASS）、TG Graceful Ops（`/halts` `/resume_symbol` `/pnl` `/pnl_id`）、Entry Drift Hybrid Policy、Pullback Entry Paper Parity、Short Main Path Risk Guard Parity、研究层低流动性硬过滤器、Paper Dual-Track Simulation（`/paper_gap`）、Data Source Provenance。
**2026-06-18 当天连归 6 个 comet change（基线 1302→1314，两轮重启 live：风控调参 PID 32773 ~10:39、轮换修复 PID 15057 ~18:29，资金 cap 仍 300）**：(1) `raise-consecutive-loss-limit`（tweak，连亏熔断 3→5）；(2) **`ev-gate-winrate-decouple`（full +3，新 capability `open-gate-ev`，改 live）——剔除开仓门胜率因子**：`ev_winrate_gate_enabled`（默认 True、config.yaml 现 false）关闭后用固定 `ev_neutral_p_win`(0.55)、跳过胜率<40%硬阈值与分桶覆盖、保留 EV 经济门；衰减期放开（近20笔胜率25%/PF0.64），卡点下移到 quality_gate(LLM观望 conf<60)+Short Regime Guard；(3) **`rotation-respect-position-hold`（+11，新 capability `symbol-rotation-position-guard`，改 live）——轮换尊重持仓研判（B-revised）**：SymbolRouter 轮换时持仓标的保留在 active 集（不强平），出场交 PositionAnalyst；`rotation_close_held_enabled`（默认 false=保护）；根因=轮换路径从未查持仓、越权砍 PA 判 hold 的持仓右尾（XLM 实证三次判 hold 被轮换平在低点、事后涨 +1.33%）；(4) **`fix-cf-lab-fidelity-epoch-resolution`（observability-only，MODIFIED `deterministic-replay-harness`）——CF 实验室保真度纪元解析修复**：`replay_decision` 改四层合并 `production_base < _EPOCH_FALLBACK(缺键录制纪元默认) < config_snapshot < 扰动override`，修磁带横跨两纪元致全局 pin 系统性发散（gate 0.732→0.969）；残余根因=`_install_config_flags` 漏还原 `_ev_winrate_gate_enabled`/`_ev_neutral_p_win`（ev_gate getattr 默认 True 强制门开）已补；**可信度判据改为 accept/reject 二元保真 ≥0.95（实测 0.996），gate 严格保真降诊断**；加纪元守卫防默认翻转静默复发。**2026-06-18 兑现可信 CF lab 重跑方向推荐 + 实盘门级归因诊断（observability，未改 config）**：① CF 方向推荐 baseline_fidelity 0.969，松 rr_floor(1.5→1.2)/min_confidence 净 PnL delta 恒 0、CF opens 恒 2 → over-determination 坐实、地板 1.50 维持 再获佐证（磁带 1655 全 reject/仅 2 开仓，样本撬不动 PnL，需前向攒新磁带）；② **lever1 前向 0 shadow_opens**（影子日志 1280：same 1262/shadow_holds 18/shadow_opens 0）→ lever1 不解锁新开仓，**暂不上 live**；③ 实盘门级归因（judge 日志）：被拒 421 单卡 `range_position_too_low` 55%(主拦做空)/`quality_gate` 21%/`ev_gate` 8%/`rr_below_floor` **仅7%**；放行 27 单 **85% 卡 confidence=60 门槛线**，做空被拒 395 vs 做多 27。**结论：衰减期开仓少+边缘单是策略态非调参问题，绑定约束在 range_position+quality_gate 非 rr_floor。** 新增 `scripts/track_marginal60.py` 跟踪 60 分边缘多单 PnL。
**2026-06-23 策略层对抗评审 → 形态 edge 实验室 → 前向验证（连归 4 个 comet change，基线 1359→`1416 passed / 0 failed` 全套件首次全绿）**：对策略层做多 agent 对抗评审 + 实盘账本(`trade_history` 87笔)/磁带(`decision_replay_tape` 10093条)实证，定位根结点=**赌动量但市场无动量**（这组 alt 在 ~1h 收益自相关≈0、延伸末端继续率 41.7%，信号分↔实盈 ρ≈0），严格证伪全部价格 alpha（MA 趋势/均值回归/OI/funding/taker/盘口/爆仓/基差；套利做市 carry 团队历史已排除）；关键方法论教训=固定 horizon 的 ρ 在自相关/小样本上造假阳性（详见 memory `alpha-source-hunt-verdict`）。回归蜡烛形态、搬到**日线尺度**（一波 5-15% ≫ 成本 20bp、2.75年跨多体制，解决样本墙+单一体制+成本地板）：① `daily-pattern-edge-lab`（新 capability `pattern-edge-discovery`，~28 形态库 + ATR 退出 + OOS 三分 + FDR + 诚实门，13308 信号 → 2 空头候选过四关）→ ② `fix-fetch-subdaily-backward-pagination`（hotfix，修 4h 向后分页 + harness 窗口 interval 感知，**`Bearish Engulfing|低位跌势` 跨周期确认** 日线 +0.326R/4h 时间对齐 +0.208R；Evening Star 4h 翻负否决；方法论=跨周期确认须时窗可比）→ ③ `pattern-forward-shadow-recorder`（新 capability `pattern-forward-shadow`，独立日线 record-only 前向影子验证上线，首跑 5 live 信号）→ ④ `fix-replay-register-reversal-pseudo-keys`（hotfix，`_EPOCH_FALLBACK` 登记 reversal-veto/pseudo-resonance 4 键，收掉预存正交 `test_no_unclassified_missing_snapshot_keys`，套件 1416/0）。全链 observability-only、红线守卫扩展、绝不接入 live。**确认稳健（前向数周 + 诚实门）前不上实盘。** 运维：README「日线形态前向影子记录器」节的每日 cron（须系统 crontab）。详见 memory `daily-pattern-lab-candidates`/`alpha-source-hunt-verdict` + `docs/superpowers/specs/2026-06-23-*-design.md`。

**2026-06-24 边缘60亏损单深查 + CF 驱动量化 TP1 地板（comet change `cf-choppy-neutral-tp1-floor-ab`，基线 1416→`1430`，observability-only 归档入 main）**：深查 11 笔已结算「边缘60」多单（均 −2.58U/胜率18%）→ join 决策磁带归因，**13/13 全 choppy+neutral+`effective_rr` 1.51-1.65 贴 1.50 地板、真实 `effective_rr_tp1` 1.28-1.40 全<1.50**，靠 lever2 阶梯口径抬过地板 + ev-decouple p_win=0.55 联合放行。新驱动 `cf_choppy_neutral_tp1_floor_ab.py`（镜像 `cf_ev_decouple_ab.py`）对磁带 accept 流 ladder-toggle 两臂复盘量化「choppy+neutral 卡 TP1≥地板」反事实：**真跑主桶 195 accept→86 忠实/109 失真排除，84/86(98%)翻 reject（全 rr_below_floor）、避开桶 −0.50R/簇（1tp/12sl=8%胜率）、mixed 旁路 0 accept；诚实门 INSUFFICIENT_SAMPLE(n=13)→方向强但 suggestive，不改 config/不上 live**。三证同向（98%靠阶梯/CF避开桶8%胜率/实盘边缘60互证负期望）。运维：日更 cron（`crontab` 每日 10:13 重跑驱动→`~/Library/Logs/cf_choppy_tp1_floor_ab.log`，等样本 n≥30 诚实门跨过；`/usr/sbin/cron` 的 Full Disk Access **2026-06-24 已授权并验证 cron 跑通**，详见 README §日更 cron）。详见 memory `cf_lab_strategy_diagnosis_winrate` + `docs/superpowers/specs/2026-06-24-cf-choppy-neutral-tp1-floor-ab-design.md`。

**2026-06-25 运维 + 连归 2 个 comet change（基线 1430→`1460`）**。运维：**LLM 中转端点更换**——旧 `156.238.228.230:8080` 已死（620+连续超时、bot 一度降级跑规则），换 `BOT_LLM_BASE_URL=https://www.codevips.cc`（OpenAI 兼容 + UA 过 Cloudflare 不变），重启 live 验证恢复、隔夜 3000+ 调用 0 失败。(1) **`pattern-shadow-broaden-universe-and-4h`（observability，1430→1437）——扩盘 + 4h 影子 + 形态 edge 干净证伪**：universe 30→~100 binance 流动币冻结快照 + runner interval 参数化(1d/4h)+ settle-when-determinable + dedup-by-bar-ts，re-fetch 102 币 1d+4h 重跑回测——**宽 universe 下日线/4h 形态 `过三关=0` 干净证伪**，30 币的 `Bearish Engulfing|低位跌势` +0.326R 是小样本/选择偏差不泛化（栽在 OOS 三段同号 + FDR，非翻负）；故 **4h 加速 cron 刻意不部署**（不加速收集非-edge），日线 cron 续作 null-monitor。**推翻形态"确认信号"**，与 `alpha-source-hunt-verdict` 同向。(2) **`fix-open-direction-regression-choppy-flat-gate`（改 live 开仓，1437→`1460`，新 capability `regime-flat-entry-gate`）——体制空仓硬门修复开仓方向回归**：方向质量时间线分析(止损无关 48h MFE/MAE，lifecycle 69 笔)证 **方向对% 改前 60%(<06-17)→ 改后 0%(≥06-20)**，按体制拆穿——**趋势单一直 80% 方向对/+16.9%、choppy/mixed/neutral 一直 0-15%**，病根=06-17 lever2 默认开 + 06-18 ev-胜率解耦把开仓结构推成 100% choppy(无方向)。修法=单点收口 `_classify_regime_flat_gate`（long-only，choppy/mixed + 无方向论据→拒 open_long，path_evidence ungated 救回被误判趋势），**不回滚 ev/lever2**（代码核查证明钝器、会连趋势单一起杀）。子agent 双阶段审查抓出 Critical（零回归重构丢 `_select_rr_floor` 守卫）已修；6 个既有测试因行为变更打挂全 opt-out（非逻辑回归），全量 **1460/0**。**改 live 需用户手动 OS 重启 live 才生效**（截至归档 live 仍跑 06-24 旧进程未重启）；env `REGIME_FLAT_GATE_ENABLED=false` 回滚。**关键认知:edge 在趋势单、不在形态/入场预测;回归=门放水放进 choppy 无方向单非脑子坏。** 详见 memory `cf_lab_strategy_diagnosis_winrate` + `docs/superpowers/specs/2026-06-25-*-design.md`。

**2026-06-20 连归 3 个 comet change（基线 1314→1338；前 2 个 observability-only 不碰 live、重启 live PID 98028 ~10:47，第 3 个改 live executor.py 需手动重启 live）**：(1) **`fix-shadow-logger-replay-baseline-parity`——影子记录器 lever1 增量口径修正**：复盘影子日志发现对比口径 `live(real) vs replay(both-levers)` 拿真 live 决策比复盘决策、混入复盘保真偏差（实证 37 条 shadow_holds 本地重放证明 lever1 两臂复盘 delta=0、13/37 baseline 复盘复现不出 live accept），改为 `replay(lever2-only baseline) vs replay(both shadow)` 两臂同复盘（偏差抵消，对齐 sequential_perturbation）+ baseline 复现自检闸（不复现 live 即标 `baseline_mismatch` 排除，对齐 perturbation_replay）；新增 jsonl `baseline_action`/`baseline_gate`/`baseline_mismatch`，`flip_kind` 改基于 baseline vs shadow，judge.py 零改动；**lever1 真实增量=0 再坐实**；原 config-parity 假设实测证伪（34/37 config_snapshot 正确含 ev_winrate=False）。(2) **`ev-decouple-forward-ab`（新 capability，新驱动 `cf_ev_decouple_ab.py` 镜像 cf_lever2_rejected_ab）——复核胜率解耦放行单前向期望**：复盘 2026-06-20 窗口中的 8 笔开仓全是 neutral 趋势+勉强压地板 R:R~1.5 边缘单、p_win=0.55 fixed 放行、实盘净亏 ~−16U，疑 ev-gate-winrate-decouple(06-18) 放行亏损单；对磁带 accept 流 gate-toggle 两臂复盘（baseline `replay(ev_winrate_gate_enabled=False)` 自检 vs 反事实 `=True` 旧门）分出"解耦放行"，两桶簇去重+resolve_counterfactual+klines 统一 CF 结算（TP1 保守）比净 R + cf_honesty_gate(min_sample=30 不下调)领先裁定 + real PnL 模糊 join sanity；**真跑 69 accept→54 忠实/38 解耦放行(69% 只因解耦才过门、全 ev_gate)，但两桶均 INSUFFICIENT_SAMPLE 诚实门拒答，suggestive 解耦放行 −0.35R/簇 反优于双门皆过 −0.80R/簇 → 证伪复盘假设、近期负收益不能干净归因到胜率解耦（挡住一次薄样本错误结论）**；常驻 harness 数据累积后重跑，回滚/约束解耦须另起 change；code review 揪出 Critical（settle 传 live plan `entry_ref` 而非 resolve 要的 `entry_price`/`created_at`→真跑 KeyError、被 mock-resolve 测试掩盖）已修+加不 mock 集成测试。**复盘实盘**：真实余额=1732 USDT（用户手动出金后确认，旧 ~3994 作废）；60 笔已平仓累计 −20.29U/胜率 21.7%、近期负收益期（XLM −10.09 拖累，宽 SL 高 ATR 币干净止损非 bug）。(3) **`fix-phantom-position-resync`（MODIFIED `position-sync-resilience`，改 live executor.py）——仓位同步补录双确认**：复盘 2026-06-20 早 XRP desync 毛刺（XRP 短单 02:16:33 干净平仓后 02:17:49 `sync_positions` 从交易所滞后快照补录幽灵持仓 → protection-unknown ERROR ×131/~69min + per-symbol halt 需人工 /resume），定位根因=60s `_close_cooldown` 被 OKX 76s 上报延迟击穿，近 3 天系统性复发 3 次（UNI/XLM/XRP，每次平仓后）。修法=双确认 persist-2-ticks（本地缺失+交易所新出现的持仓连续 `position_resync_confirm_ticks`(默认2,HARD_LIMITS 1-10)个 sync tick 确认才补录，`_pending_resync` 计 tick+扫尾清幽灵，幽灵下个 tick 消失自然过滤，`_close_cooldown` 作第一道防线保留）+ `_alert_protection_unknown` 单点收口告警去重（同 symbol+reason 仅状态变化记 ERROR+halt 幂等,testnet 不 halt 语义保留）+ 幽灵移除时清 migrate_missing_sl halt 自愈（仅此 reason）。**安全不放松**（真无保护仓 2tick 补录后 reconcile 无 SL 仍 halt）。**20x 杠杆查明=`_calc_risk_budget` 恒定风险公式 leverage=max_loss(5%)/(margin×sl_dist) 上限 20x、tight-SL 设计输出、max_loss bounded、非 bug、排除**。build 期新 config 键触发 CF-lab epoch-completeness 守卫已修（登记入 `_GATE_IRRELEVANT`，教训：新增 DEFAULTS 键须登记 epoch 分类）。code review APPROVED。基线 1338。

**2026-06-26 归档 `cf-neutral-momentum-rescue-ab`（基线 1460→`1474`，observability-only）**：体制空仓硬门 path_evidence 救援阀门双重失效诊断（阀门从未触发，sym_dir==bullish + strength>=60 是 bullish 隐式代理），信号口径测量 A/B（方向无关谓词），结论 suggestive 不达 actionable（A 样本全<30/紧 SL edge 坍塌）→不改门。详见 memory `cf_lab_strategy_diagnosis_winrate` + `docs/superpowers/specs/2026-06-26-cf-neutral-momentum-rescue-ab-design.md`。

**2026-07-01 体制分类改进部署（改 live，commit a02fa40，基线 1474 不变）**：诊断脚本验证体制过保守（7 天 64% choppy 但实际 48h 均涨 6.18%/25% 强趋势 >10%/0% bullish-bearish → 不识别趋势市），修改 `utils/market_regime.py::_compute_raw_regime` 引入 **BTC anchor 权重 2.0/ETH 1.5 + 阈值调整（bullish/bearish 0.6→0.5/choppy 0.5→0.6）**。测试同步（14 passed）。**Live 已重启（PID 21550, 2026-07-01 20:29）**，预期 48h 验证：choppy 64%→<40%/bullish-bearish 0%→>0%/开仓数增加。部署前 baseline 已记录（`data/diagnostic_regime_classification.json`）。回滚=`git checkout HEAD -- utils/market_regime.py test_regime_hysteresis.py` + 重启 live。教训：体制分类是决策上游，阈值过严会完全不识别趋势；诊断需用前向价格验证非仅看标签分布；BTC/ETH 市场领头羊应有更高权重。详见 memory `cf_lab_strategy_diagnosis_winrate` 2026-07-01 节。

**2026-07-03 体制分类加权逻辑修复（改 live，commit 97825a1→08a7552，基线 1474 不变）**：2026-07-01 部署效果不达预期（choppy 占比维持 93%），诊断发现实现存在 **5 个逻辑缺陷**：1) Python truthy bug（`if btc_bias` 对 'neutral' 为 True → weighted_total 错误增加）；2) neutral_pct 未加权（CHOPPY 判断绕过权重系统）；3) BULLISH 阈值 0.5 过高（权重 boost 不足以跨越）；4) CHOPPY neutral_pct≥0.6 过宽（易误判）；5) BTC bias 用日线级别（滞后，P2 deferred）。97825a1 已修 P0-1/P0-2/P1-1/P1-2 并将 live 重启到 PID 34929（2026-07-03 14:22），但 2026-07-03 清理复核发现 **follow-up P0：`anchor_neutral_weight` 已进 `weighted_neutral` 但未进 `weighted_total`**，导致 BTC/ETH neutral 时 `neutral_pct` 可超过 1。commit `08a7552` 已补公式 `weighted_total = weighted_bullish + weighted_bearish + weighted_neutral` 和回归测试，并清理重复 live 进程；live 当前由 `screen` 会话 `crypto_live` 单进程运行，Python PID 24714，2026-07-03 15:05:11 OS 层重启加载。15:05 后 LLM 中转多次 504，尚未产出新的 decision tape 样本，需待新样本后重新统计 24-48h 验证。详见 `openspec/changes/archive/2026-07-07-fix-regime-weighting-logic/`。教训：权重设计正确不等于实现正确；所有百分比计算须满足分子分母同权重基准；加权后阈值需重新校准。

**2026-07-05 Live 重启（PID 52108/52110）**，2026-07-07 完成 96h 验证：**不回滚**。开仓量 +153%（30→76 accepts, 4→6 opens）、胜率 57%（4胜3负）达标；choppy 14.66%（vs 目标 60-70%）因市场从 bullish(98.2%) 转 mixed(77.5%) 主导，非修复失效；mixed 仍受体制空仓硬门约束、RR 默认 1.5，未绕过风控。验证报告见 memory `regime_fix_verification_2026_07_07`。基线升至 **1490 passed** (+16，来自 3 个归档 changes：`fix-regime-weighting-logic` 新增体制加权测试、`low-rr-early-trailing` +10 early trailing 测试、`llm-streaming-resilience` +6 流式/重试测试）。

**2026-07-07 归档 3 个 changes**（OpenSpec 状态漂移修复）：(1) `2026-07-07-fix-regime-weighting-logic`（体制修复，新 spec `market-regime-classification`）；(2) `2026-07-07-low-rr-early-trailing`（CF 回测 38672 样本均 R +0.2486/胜率 73.5%，新 spec `low-rr-early-trailing`）；(3) `2026-07-07-llm-streaming-resilience`（修 spec 格式后归档，Live 全链路零 504/截断，新 spec `llm-stream-resilience`）。状态漂移修复：`.comet.yaml` 标记 `archived: true` 的 changes 已物理移入 archive 目录。

**2026-07-10 `add-tactical-exit-track` 归档**：围绕 WLD 式弱/混合环境落袋诉求，把 Main Trend Runner 与 Tactical Exit Track 分轨；Tactical 默认 disabled + shadow-only，使用独立 R:R/EV/TP1/cost gate、thesis-health、max-hold、独立风控桶和分桶元数据。验证：Tactical suite 21 passed，邻近回归 118 passed / 3 warnings，OpenSpec strict PASS。

**2026-07-15 `protective-sl-halt-recovery` 归档**：Tactical live WLD 事件暴露 OKX attached SL 回查延迟导致全局保护单 halt 残留。已补 bounded attached-SL verification、allowlist exact-match 自愈、multi-halt repoint、`/status` 全局/per-symbol/Tactical circuit 分行。全量验证 `1543 passed, 4 deselected, 1 warning`；云服 HEAD `a5396aa`，Tactical live 灰度配置为 track=true、shadow_only=false、RR=0.75、EV=-0.04，当前 `halted=false`、`can_open_new=true`、Tactical circuit 未暂停。

**2026-07-17~2026-07-23 Shadow Tactical live sidecar 系列归档**：用户要求“直接照 shadow Tactical 开 24h”，不改 Main、不放宽 Main gate。已新增 sidecar runner 尾随 `data/rejected_signal_events.jsonl`，只消费 strict eligible Tactical shadow 记录并写独立 state/owners/ledger；后续补齐 sidecar exit monitoring、exchange-flat reconcile、100U 进程局部放大、ghost-position safety、OKX `net_mode` 同标的堆叠阻断、sidecar entry drift 保护和 Main migration 保护。最新 HEAD `9f5d297`，聚焦验证 `142 passed` + OpenSpec strict PASS。

**下一阶段（2026-08-06）**：继续观察 Tactical V2 固定 `100U x 3` 首轮 cohort，按 final PnL 和 exchange proof 分桶，不扩大容量、不恢复 Sidecar admission；策略改善仍回到上游方向质量、体制识别和趋势筛选。若要长期无人值守，先另起 supervisor 设计与验收，不能把当前 `nohup` 常驻进程当作自动拉起。

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
| OKX 持仓同步瞬时重试 | 2026-06-12 | `sync_positions` 对 `ccxt.NetworkError` 有界重试（`_fetch_positions_with_retry`），吸收 OKX 网络抖动止 ERROR 刷屏 | 1102 | comet change `fix-okx-position-sync-transient-retry` |
| Agent Health Supervisor | 2026-06-12 | `utils/health_snapshot.py` 纯函数聚合 loop-alive/queue backlog/LLM degraded/data degraded 四维度，扩展 `agent_health.json` + `/status` 总括 + `/health` 明细 + 边沿告警/恢复通知；BaseAgent `_last_alive_ts`/`_last_work_ts` 心跳，collector `_latest_data_health`；observability-only write-only，无需 event_backtest | 1135（其后延伸 tick-stall 见下行） | `docs/superpowers/specs/2026-06-12-agent-health-supervisor-design.md` + `docs/superpowers/plans/2026-06-12-agent-health-supervisor.md` |
| tick-loop 挂死检测（agent-health-supervisor 延伸） | 2026-06-12 | `BaseAgent._periodic_loop` tick 埋点 `_tick_enter_ts`/`_tick_exit_ts`；`_loop_health` 测"当前 tick 执行多久"（`enter>exit AND now-enter>120s`）并入 loop_health；告警/`/health` 区分 message-loop vs tick 卡死；扁平阈值 120s 锚定最长健康单次 tick 60s | 1146 | `docs/superpowers/specs/2026-06-12-agent-tick-stall-detection-design.md` |
| bot LLM env 隔离（bot-llm-config-isolation） | 2026-06-13 | `agents/llm_client.py` + `.env.example` 改读 `BOT_LLM_*` 独立变量名，与 Claude Code 自身的 `ANTHROPIC_*` 解耦，止环境串扰；comet 全流程归档 | 1149 | comet change `decouple-bot-llm-env-from-claude-code` + `test_bot_llm_env_isolation.py` |
| Tactical Exit Track | 2026-07-10 | Main 与 Tactical 出口分轨；Tactical 独立 stop/TP1/R:R/EV/cost gate、local lifecycle、risk governor、Reviewer/CF metadata，默认 disabled + shadow-only | 21 tactical tests + 118 adjacent regression | `openspec/changes/archive/2026-07-10-add-tactical-exit-track/` |
| Tactical shadow-only live observation | 2026-07-11 | 云服打开 `TACTICAL_TRACK_ENABLED=true` + `TACTICAL_SHADOW_ONLY=true` + `TACTICAL_TP1_R=1.00` 跑 24h；shadow-only 通过 `_apply_tactical_shadow_profile` 写 true Tactical counterfactual 到 `rejected_signal_*`，ledger 支持 `shadow_tactical_max_hold`，不真开 Tactical | 32 tactical/counterfactual tests | `docs/runbook.md#tactical-exit-track` |
| Tactical threshold-gated observation | 2026-07-12 | 按 24h ledger 回放收紧 shadow 样本：`TACTICAL_MIN_RR_FOR_TRACK=0.75`、`TACTICAL_MIN_EV_FOR_TRACK=-0.04`；成本门过但 RR/EV 阈值门失败的样本保留 `exit_profile=tactical_v1` 做 max-hold counterfactual，但不算 true-open Tactical | 32 tactical/counterfactual tests | `docs/runbook.md#tactical-exit-track` |
| Protective SL Halt Recovery | 2026-07-15 | OKX attached SL 有界验证；`okx_sl_algo_unresolved:<symbol>` / `migrate_missing_sl` 保护单 halt 在风险消失后 exact-match 自愈；multi-halt unresolved symbol 会保持全局 halt 并 repoint；`/status` 分开显示全局 halt、per-symbol halt、Tactical circuit | `1543 passed, 4 deselected, 1 warning` | `openspec/changes/archive/2026-07-15-protective-sl-halt-recovery/` + `docs/superpowers/reports/2026-07-14-protective-sl-halt-recovery-verify.md` |
| Shadow Tactical live sidecar | 2026-07-17 | 独立 sidecar 尾随 `rejected_signal_events.jsonl`，把 strict eligible Tactical shadow 记录映射为 live plan；绕过 Main Judge/Ranker/Tactical admission gates，但保留机械执行硬限、保护单验证、独立 state/owners/ledger 和 Main owner-ignore | sidecar core/executor/CLI/owner isolation tests | `openspec/changes/archive/2026-07-17-promote-shadow-tactical-live-48h/` |
| Sidecar exit monitoring | 2026-07-17 | sidecar run loop 监控 open sidecar-owned 仓位，复用 Tactical exit evaluator，按可证明归属执行 TP1/TP2 reduce、invalidated/weakened/max-hold close，stop 路径只处理 proven exposure | 相关 sidecar + executor 测试通过 | `openspec/changes/archive/2026-07-17-shadow-tactical-sidecar-exit-monitoring/` + `docs/superpowers/reports/2026-07-16-shadow-tactical-sidecar-exit-monitoring-verify.md` |
| Sidecar exchange-flat reconcile | 2026-07-20 | unproven owner 在 OKX 明确 flat 时关闭 owner 元数据并写 pending external close；present/unknown 保持 fail-closed，不提交 close/reduce | `31 passed` | `openspec/changes/archive/2026-07-20-fix-sidecar-exchange-flat-reconcile/` + `docs/superpowers/reports/2026-07-20-fix-sidecar-exchange-flat-reconcile-verify.md` |
| Sidecar 100U process-local scaling | 2026-07-22 | 只重启 sidecar，进程局部 `MAX_TRADE_AMOUNT=100` / `EFFECTIVE_BALANCE_CAP=1000` + CLI `--size-usdt 100 --max-active 3`，不改 Main `.env`、不重启 Main | 运维状态核对 | `openspec/changes/archive/2026-07-22-scale-sidecar-100u-only/` |
| Sidecar ghost-position safety | 2026-07-23 | Main migration 保留 sidecar-owned present/unknown exposure 的 manual/ambiguous protection；sidecar admission 阻断 OKX `net_mode` 同标的堆叠和 exchange-position fetch unknown；monitor 对 ghost/ambiguous exposure fail-closed；sidecar open 增 entry drift 守卫 | `142 passed` + OpenSpec strict PASS | `openspec/changes/archive/2026-07-23-fix-sidecar-ghost-position-safety/` + `docs/superpowers/reports/2026-07-22-fix-sidecar-ghost-position-safety-verify.md` |
| Tactical V2 live promotion | 2026-07-31 | 完成 32h shadow gate、sidecar drain archive 和首轮 live cohort；V2 固定 `100U x 3`，Sidecar admission 关闭但 resident monitor 保留 | `1869 passed`；云服 live cohort 五笔 final、无重复提交、无未分类 mismatch | `openspec/changes/archive/2026-08-05-promote-shadow-tactical-v2-live/` + `docs/superpowers/reports/2026-07-28-promote-shadow-tactical-v2-live-verify.md` |
| Tactical V2 entry/PnL recovery | 2026-08-06 | 精确 `clOrdId` 成交/取消回查、取消终态收敛、保护 halt 自愈、旧 halt 迁移和重启后 durable final-PnL replay；保留 fail-closed 证据门 | `1878 passed`；云服 `LIVE 100U x 3`、0 active、无 integrity halt、保护/对账 verified | `openspec/changes/archive/2026-08-06-fix-tactical-canceled-entry-self-heal/` + `docs/superpowers/reports/2026-08-06-fix-tactical-canceled-entry-self-heal-verify.md` |

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

- **运行时**：Python 3.10+ / pip3
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
