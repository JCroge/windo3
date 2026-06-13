## Context

系统目标是一个能"指明整个策略调整方向"的反事实回放回测器（Counterfactual Policy Laboratory）。完整愿景分层：

```
L0 现状(玩具)  → L1 可信被拒单回放 → L2 确定性全带回放+golden master → L3 组合态扰动 → L4 旋钮扫描+置信度门
```

本 change 只交付 **L1 + 未来原料地基**，是整条路线图的 #1。当前状态约束：

- `utils/counterfactual_ledger.py`：已记录被拒单 + tick 驱动 TP/SL 影子跟踪，但 PnL 是到价%、零成本、只看 tp[0]、24h 过期 + 反向作废。observability-only。
- `data/journal/events_*.jsonl`：只 journal CRITICAL_TOPICS（`trade_decision`/`execution_result`/`risk_alert`/`daily_hard_stop_triggered`/`system_command`）。**`tech_analysis`/`market_data`/`price_tick` 不入 journal** —— Judge 的输入未被持久化。
- `trade_decision.attribution`：富含派生信号（score/regime/RSI/range_pos/gate metrics/llm_relation），但是 Judge"嚼过的渣"，非原料，不足以忠实重放。
- `logs/llm_audit_*.jsonl`：缓存了 Judge 看到的 LLM prompt + 输出（7 天保留）。
- `data/klines.db`：1m+ K 线（无 tick）。

红线（CLAUDE.md）：observability-only write-only 类特性（`data-source-provenance`/`agent-health-supervisor`）严禁被任何 gate/veto/halt/rank/daily-stop 读取做交易决策。本 change 遵循同一性质。

## Goals / Non-Goals

**Goals:**
- 锁住未来可忠实回放的原料：Judge 决策点（accept + reject）原子落 `decision_replay_record` 全量输入+输出 bundle。
- 把被拒单反事实 PnL 升级为可信真金白银（扣费/资金费、K 线 SL/TP 判定、SL-first 偏差带、真实 USDT 净值）。
- 前向 tick/trade 快照采集，价格精度双轨（tick 优先、1m 退化）。
- 诚实性 gate：结论强制带样本量 + 置信区间，薄样本拒答。
- 全程 observability-only write-only，零交易决策影响，现有行为零回归。

**Non-Goals:**
- L2 确定性全带回放 + golden master（#2）。
- L3 组合态扰动回放（slot gate / daily stop / 资金曲线重演）（#3）。
- L4 旋钮扫描 + 排名（#4）。
- LLM 旋钮扰动（改 prompt / 换模型 → 缓存失效需重调）。
- 历史 tick 回填（上线前的数据只能 1m 退化，不追溯补 tick）。

## Decisions

### D1 — 决策磁带：独立 `utils/decision_tape.py`，不扩 journal CRITICAL_TOPICS
- **选择**：新建 `utils/decision_tape.py` writer，Judge 决策点调一行 `record_decision(bundle)`；磁带落独立文件 `decision_replay_tape.jsonl`（经 `state_paths` 派生）。
- **替代**：把 `tech_analysis` 加进 journal CRITICAL_TOPICS 复用现成基建。
- **理由**：`tech_analysis` 是高频（每标的每 tick 级）信号，灌进关键事件流会污染/膨胀 journal（journal 是给风控审计的关键因果链，不该被高频派生数据淹没）。独立磁带更干净、可独立关停、retention 可独立配置。bundle 只存 `llm_audit` 调用 id 引用而非 LLM 原文（避免重复 + 脱敏复用既有 audit 脱敏）。
- **bundle 字段**（草案，design 阶段细化）：`schema_version`、`request_id`、`timestamp`、`symbol`、`decision`（accept/reject）、`tech_analysis`（9 维全量快照）、`price_at_decision`、`regime_state`、`llm_audit_ref`、`trade_decision_output`（accept 时的 plan/attribution，reject 时的 reject_reason/attribution）。

### D2 — 反事实 PnL：成本模型 + K 线触发判定，SL-first 保守 + 偏差带
- **选择**：被拒单的假设结果用真实成本模型（手续费 + 资金费）计算 USDT 净 PnL；SL/TP 触发用 K 线 high/low 判定；同根 K 线 high 触 TP 且 low 触 SL 时**保守取 SL-first**，并把"该笔结果不确定"标记进偏差带统计。
- **替代**：维持现有到价% + 零成本（玩具）；或用乐观 TP-first。
- **理由**：决策要看真金白银，不看%。SL-first 是反事实 PnL 的保守下界（不高估被拒单价值，避免"放宽 gate"的乐观偏差）。偏差带量化让使用者知道结论的不确定度来自价格精度。
- **复用**：成本模型复用 executor 现有 `CostModel`（避免重写发散）；旧 `rejected_signal_events.jsonl` 凭已有字段重算。

### D3 — tick 采集：独立模块，不塞 `multi_data_collector`
- **选择**：独立轻量 tick/trade 快照采集模块，写独立 tick 文件；与 9 维采集解耦。
- **替代**：扩 `multi_data_collector`（已在采行情，顺路）。
- **理由**：独立模块更好关停、retention 独立、故障隔离（tick 采集挂了不拖累决策主链路的行情采集）。必须有界写入（批量 flush / 采样），不阻塞。

### D4 — 诚实性 gate：报表层统一收口
- **选择**：所有方向/PnL 结论经单一报表层函数计算样本量 + 置信区间（如 Wilson 区间 for 胜率、bootstrap for 净 PnL），样本 < 阈值时输出"INSUFFICIENT_SAMPLE — 不准动"而非给数。
- **理由**：防过拟合噪声是这工具的灵魂（to-do-list trap：+5.47 全来自 1 笔 ADA）。单点收口符合本项目"单一函数收敛"惯例。并入 `counterfactual-pnl` capability（它是"PnL 结论怎么报"的一部分）。

## Risks / Trade-offs

- **[1m K 线 SL/TP 先后不可判]** → SL-first 保守假设 + 偏差带量化；上线日起双轨 tick 采集，未来回放渐准。历史段永久带偏差带。
- **[决策磁带是热路径旁路写入，可能拖累 Judge]** → 有界异步/批量 flush，writer 故障不得抛进决策路径（fail-safe drop + 计数告警）。
- **[磁带/tick 文件无界增长]** → retention 配置（默认 N 天）+ 大小监控。
- **[红线误用：未来有人拿反事实数据做 gate]** → 测试守卫（仿 `test_paper_dual_track.py::test_reviewer_does_not_consume_idealized`）+ 文档红线显式声明 observability-only。
- **[旧数据用 attribution 重算的 PnL 与未来 input-exact 重放不一致]** → 明确标注两类来源（`source=attribution_reconstructed` vs `source=tape_exact`），不混用、不互相覆盖。
- **[成本模型与 executor 发散]** → 复用同一 `CostModel`，不重写。

## Migration Plan

- 纯新增 + observability-only，无破坏性变更、无 schema 迁移、无现有行为改动。
- 上线即生效：决策磁带 + tick 采集从上线那刻开始攒原料；被拒单 PnL 升级对旧 `rejected_signal_events.jsonl` 立刻可用。
- 回滚：feature flag（如 `DECISION_TAPE_ENABLED` / `TICK_CAPTURE_ENABLED`，默认开），关闭即回到现状，零残留影响交易。

## Open Questions

- bundle 中 `tech_analysis` 全量快照的体积与 retention 默认值（design 阶段定）。
- 置信区间方法选型（Wilson vs bootstrap vs 两者并报）与"薄样本"阈值默认值。
- tick 采集的精度/频率与存储格式（逐 trade vs 秒级聚合），按存储成本权衡。
- 反事实 PnL 是否纳入资金费（持仓时长 × funding）还是仅手续费——取决于被拒单假设持仓时长的定义（到 TP/SL/24h 过期）。
