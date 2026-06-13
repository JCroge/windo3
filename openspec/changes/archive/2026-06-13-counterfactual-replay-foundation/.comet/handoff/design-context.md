# Comet Design Handoff

- Change: counterfactual-replay-foundation
- Phase: design
- Mode: compact
- Context hash: 0781174ddb322f6826a10b8a04d6970d9874134d4f6b98f7184026596d6dd627

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/counterfactual-replay-foundation/proposal.md

- Source: openspec/changes/counterfactual-replay-foundation/proposal.md
- Lines: 1-34
- SHA256: d8ca2524553065e74d736ec6a546ea128cc4674e4b2c7cb97ab02c21971ce696

```md
## Why

我们想要一个能"指明整个策略调整方向"的回放回测器，但现有工具都不够：`event_backtest.py` 用 RobustStrategy 的 MA 信号另写了一套评分/gate，与线上 LLM-Judge 对不上，结论无效（to-do-list 已记 trap：5 笔样本 +5.47 全来自 1 笔 ADA → 无效）；`CounterfactualLedger` 虽记录被拒单，但 PnL 是玩具级（到价即成交、零成本、只看 tp[0]、`pnl_pct%`），数字没人敢拿来做决策。

最关键的缺口：**系统现在只持久化决策的输出（`trade_decision` 入 journal），却把 Judge 的输入扔掉了**（`tech_analysis` / `market_data` / `price_tick` 不在 CRITICAL_TOPICS journal）。忠实回放需要忠实输入——没有原料，未来任何"喂真实输入给真实 Judge 重放"的方案都无从谈起。**每多等一天，就多一天不可忠实回放的数据在流失。** 这是"要做就现在做"在数据资产上的字面成立。

本 change 是"反事实策略实验室"多 change 路线图的**地基（#1）**：先锁住未来可忠实回放的原料，并把被拒单回放从玩具升级成可信数字立刻见数。

## What Changes

- **新增决策磁带埋点（keystone）**：在 Judge 决策点（开仓 accept 与 reject 都要）原子落一条 `decision_replay_record`——捕获完整输入 bundle（`tech_analysis` 9 维全量 + 当时 price + 引用的 `llm_audit` 调用 id + regime 状态 + 最终 `trade_decision` 输出 + `request_id` 关联），带时间戳 append-only 落盘，供未来忠实回放。**observability-only write-only，不改 Judge 任何决策逻辑。**
- **升级被拒单反事实 PnL**：把 `utils/counterfactual_ledger.py` 的假设 PnL 从到价% 升级为真金白银——扣手续费/资金费、用 K 线判定 SL/TP 触发、SL/TP 同根冲突时保守取 SL-first 并量化偏差带、输出真实 USDT 净 PnL。旧 `rejected_signal_events.jsonl` 凭已有 attribution 立刻可挖。
- **新增双轨 tick 采集**：轻量 tick/trade 快照采集，从上线日起攒 tick 级精度（让未来回放 tick 精确）；历史回放退化用 1m K 线 + 量化 SL-first 偏差带。
- **新增诚实性 gate**：所有方向/PnL 结论强制带样本量 + 置信区间，样本太薄时拒答"不准动"——防过拟合噪声。
- 不改变任何现有交易/风控行为；现有 `CounterfactualLedger` 的 observability-only 性质保持不变。

## Capabilities

### New Capabilities
- `decision-replay-tape`: Judge 决策点全量输入+输出 bundle 的 append-only 落盘（决策磁带），含 schema、原子写、`request_id`/`llm_audit` 引用、observability-only write-only 红线。
- `counterfactual-pnl`: 被拒单反事实 PnL 的可信化——成本模型（手续费/资金费）、K 线 SL/TP 触发判定、SL-first 保守假设 + 偏差带量化、真实 USDT 净值，以及"样本量+置信区间、薄样本拒答"的诚实性 gate。
- `tick-snapshot-capture`: 轻量 tick/trade 快照前向采集与持久化，回放价格精度的双轨数据源（tick 优先、1m 退化）。

### Modified Capabilities
<!-- 无：现有 CounterfactualLedger / journal 没有对应 openspec spec，本 change 全部为新 capability。 -->

## Impact

- **新增代码**：决策磁带 writer（Judge 决策点埋点，可能 `utils/decision_tape.py` + `agents/trading/judge.py` 调用点）；反事实 PnL 引擎（成本模型 + K 线触发判定，扩展 `utils/counterfactual_ledger.py` 或新 `utils/counterfactual_pnl.py`）；tick 采集器（扩展 `agents/trading/multi_data_collector.py` 或新采集模块）；诚实性 gate（置信区间/样本量，回放报表层）。
- **新增数据文件**：`decision_replay_tape.jsonl`（决策磁带）、tick 快照文件；经 `utils/state_paths.py` 派生 namespace 路径，禁止硬编码。
- **复用既有数据**：`logs/llm_audit_*.jsonl`（LLM 输出缓存，磁带只存引用 id）、`data/klines.db`（1m K 线，历史价格）、`data/rejected_signal_events.jsonl`（旧被拒单）。
- **红线合规**：observability-only write-only，严禁任何 gate/veto/halt/rank/daily-stop 读决策磁带/反事实 PnL 做交易决策（与 `data-source-provenance` / `agent-health-supervisor` 同性质）。不触 Judge 决策公式，无需 event_backtest 同构（本身不参与开仓）。
- **非目标（留后续 change）**：L2 确定性全带回放 + golden master（#2）、L3 组合态扰动回放（#3）、L4 旋钮扫描+排名（#4）、LLM 旋钮扰动（改 prompt/换模型需重调 LLM）。
- **性能**：决策磁带与 tick 采集是热路径旁路写入，必须有界（采样/批量 flush），不得阻塞决策或采集主循环。
```

## openspec/changes/counterfactual-replay-foundation/design.md

- Source: openspec/changes/counterfactual-replay-foundation/design.md
- Lines: 1-78
- SHA256: 819f7981faa8ef3836208ff8c720ed7f0905142a359462a4f06ccc7f98c1c9ff

```md
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
```

## openspec/changes/counterfactual-replay-foundation/tasks.md

- Source: openspec/changes/counterfactual-replay-foundation/tasks.md
- Lines: 1-45
- SHA256: 2350071efac36588095cb50e0eb10003bff8e76979418e4e650f37bb3b9a7f78

```md
# Tasks — counterfactual-replay-foundation

> 路线图 #1（L1 + 未来原料地基）。observability-only write-only，零交易决策影响。
> 深度技术决策（bundle 字段最终定稿、置信区间方法、retention 默认值）在 comet-design 的 Superpowers Design Doc 收口。

## 1. 决策磁带埋点（decision-replay-tape）

- [ ] 1.1 新建 `utils/decision_tape.py`：`record_decision(bundle)` writer + `decision_replay_record` schema（schema_version/request_id/timestamp/symbol/decision/tech_analysis/price_at_decision/regime_state/llm_audit_ref/trade_decision_output）
- [ ] 1.2 原子追加写 jsonl + 路径经 `utils/state_paths.py` 派生（namespace 隔离）+ feature flag `DECISION_TAPE_ENABLED`（默认开）+ retention 配置
- [ ] 1.3 writer fail-safe：写失败不抛进调用方，丢弃 + 计数告警；有界/异步 flush 不阻塞
- [ ] 1.4 Judge 决策点接线：accept（open_long/open_short 发布点）与 reject（各 gate 拦截点）各调一行 `record_decision`，复用现有 `request_id`/attribution，引用 llm_audit 调用 id
- [ ] 1.5 单测：accept 落带、reject 落带、writer 异常不污染决策、flag 关停零文件、namespace 路径、llm_audit_ref 可解析

## 2. tick 采集（tick-snapshot-capture）

- [ ] 2.1 新建独立 tick/trade 快照采集模块 + 持久化格式（逐 trade vs 秒级聚合，按 design 定）+ 路径经 state_paths + flag `TICK_CAPTURE_ENABLED`
- [ ] 2.2 有界批量/异步写入，故障隔离不拖累 `multi_data_collector` 与决策链路
- [ ] 2.3 单测：采集落盘、批量 flush 不阻塞、flag 关停无残留、故障隔离

## 3. 反事实 PnL 升级（counterfactual-pnl）

- [ ] 3.1 反事实 PnL 引擎：复用 executor `CostModel` 算手续费/资金费 → USDT 净值（扩展 `utils/counterfactual_ledger.py` 或新 `utils/counterfactual_pnl.py`，按 design）
- [ ] 3.2 K 线 SL/TP 触发判定 + 同根冲突 SL-first 保守 + 不确定标记；价格源双轨（tick 优先、1m 退化）
- [ ] 3.3 价格精度偏差带量化（保守笔数/占比 + TP-first 上界区间）
- [ ] 3.4 数据来源标注 `source ∈ {attribution_reconstructed, tape_exact}`，不混用
- [ ] 3.5 旧 `rejected_signal_events.jsonl` 凭 attribution 重算入口
- [ ] 3.6 单测：净 PnL 扣成本、单边触发、同根 SL-first、偏差带、source 标注、旧数据重算

## 4. 诚实性 gate（counterfactual-pnl）

- [ ] 4.1 单一报表层函数：样本量 + 置信区间（胜率 Wilson / 净 PnL bootstrap，按 design 定）+ `INSUFFICIENT_SAMPLE` 拒答阈值
- [ ] 4.2 所有汇总/方向结论路径收口到该函数（单点收敛，调用点不重写）
- [ ] 4.3 单测：薄样本拒答、足量带区间、单点收口（无第二份判定）

## 5. 红线守卫与报表出口

- [ ] 5.1 observability-only 守卫测试：仿 `test_paper_dual_track.py::test_reviewer_does_not_consume_idealized` —— 任何 gate/veto/halt/rank/daily-stop 不读磁带/反事实 PnL/tick
- [ ] 5.2 报表出口：被拒单反事实汇总（按 gate × regime × source 分桶 + 偏差带 + 诚实性 gate），扩展或复用 `replay_report.py`
- [ ] 5.3 docs：CLAUDE.md 风控红线补一条 observability-only 声明；design/spec 链接；docs/to-do-list.md OPEN 条目更新为"#1 进行中 + 后续 #2/#3/#4 路线图"

## 6. 验证

- [ ] 6.1 全量 `python3 -m pytest -q` 通过，基线不回归（当前 1149，本 change 只增不减）
- [ ] 6.2 `python3 -m compileall -q .` 通过
- [ ] 6.3 现有行为零回归确认：flag 全关时系统与现状等价（无新文件、决策不变）
```

## openspec/changes/counterfactual-replay-foundation/specs/counterfactual-pnl/spec.md

- Source: openspec/changes/counterfactual-replay-foundation/specs/counterfactual-pnl/spec.md
- Lines: 1-64
- SHA256: d7475677c9e9bd0d282408e2a43d2a3758e1a2b73a4a6e0b5d5a461f0e570b80

```md
## ADDED Requirements

### Requirement: 被拒单反事实 PnL 用真实成本模型
系统 SHALL 用真实成本模型（手续费 + 资金费）计算被拒单假设成交的 USDT 净 PnL，复用 executor 既有 `CostModel`，不重写成本逻辑。

#### Scenario: 净 PnL 扣成本
- **WHEN** 一个被拒单的影子结果被解析
- **THEN** 输出 SHALL 为扣除手续费（与持仓时长相关的资金费，若纳入）后的真实 USDT 净 PnL，而非到价毛%

#### Scenario: 复用 CostModel 不发散
- **WHEN** 成本计算执行
- **THEN** 系统 SHALL 调用 executor 同一 `CostModel`，不存在第二份成本公式实现

#### Scenario: 资金费近似标注
- **WHEN** 计算被拒单净 PnL 纳入资金费
- **THEN** 系统 SHALL 用决策时点 `funding_rate` 当持仓期常数近似，并把结果标注 `funding=approximated`，不假装逐 8h 精确

### Requirement: K 线 SL/TP 触发判定与 SL-first 保守假设
系统 SHALL 用 K 线 high/low 判定被拒单 SL/TP 是否触发；当同一根 K 线同时触及 SL 与 TP 时，SHALL 保守取 SL-first，并将该笔标记为价格精度不确定。

#### Scenario: 单边触发
- **WHEN** 某 K 线仅 high 触及 TP 或仅 low 触及 SL（long 视角）
- **THEN** 系统 SHALL 判定该单边结果，记录触发价与时间

#### Scenario: 同根冲突保守取 SL
- **WHEN** 同一根 K 线 high 触 TP 且 low 触 SL
- **THEN** 系统 SHALL 取 SL-first（保守下界），并把该笔计入偏差带（结果不确定）

### Requirement: 价格精度偏差带量化
系统 SHALL 在汇总输出中量化"因价格精度不可判而保守处理"的样本占比与其对净 PnL 的影响范围（偏差带）。

#### Scenario: 偏差带随报告输出
- **WHEN** 生成被拒单反事实汇总
- **THEN** 报告 SHALL 含 SL-first 保守笔数、占比，以及"若取 TP-first 上界"的 PnL 区间

### Requirement: 数据来源标注不混用
系统 SHALL 标注每条反事实结果的来源（`attribution_reconstructed` 旧数据重算 vs `tape_exact` 磁带精确回放），二者不混用、不互相覆盖。

#### Scenario: 来源可区分
- **WHEN** 汇总同时含旧 `rejected_signal_events.jsonl` 重算与新磁带数据
- **THEN** 每条结果 SHALL 带 `source` 标签，报告可按来源分组

### Requirement: 诚实性 gate — 三档样本 + Wilson/bootstrap 区间
系统 SHALL 经单一报表层函数对所有方向/PnL 结论计算样本量与置信区间：胜率用 Wilson score 区间，净 PnL 用 bootstrap 重采样区间；并按三档样本量分级输出，薄样本拒答。

#### Scenario: 薄样本拒答 (n<30)
- **WHEN** 某分桶（如某 gate × regime）样本量 < `CF_MIN_SAMPLE`（默认 30）
- **THEN** 系统 SHALL 输出 `INSUFFICIENT_SAMPLE — 不准动`，不给净 PnL 方向结论

#### Scenario: 中样本 low_confidence (30≤n<100)
- **WHEN** 样本量在 `CF_MIN_SAMPLE` 与 `CF_LOWCONF_SAMPLE`（默认 100）之间
- **THEN** 输出 SHALL 含 Wilson 胜率区间 + bootstrap 净 PnL 区间，并标 `low_confidence`，不判 actionable

#### Scenario: 足量且区间不跨 0 才 actionable (n≥100)
- **WHEN** 样本量 ≥ `CF_LOWCONF_SAMPLE` 且 bootstrap 净 PnL 区间不跨 0
- **THEN** 输出 SHALL 标 `actionable` 并给方向；若区间跨 0，SHALL NOT 标 actionable

#### Scenario: bootstrap 暴露单笔主导
- **WHEN** 某分桶净 PnL 主要由极少数交易贡献（如单笔 ADA 主导）
- **THEN** bootstrap 区间 SHALL 反映该脆弱性（宽区间/跨 0），不被点估计掩盖

#### Scenario: 单点收口
- **WHEN** 任意报表/汇总路径需要给出统计结论
- **THEN** 其 SHALL 调用同一诚实性 gate 函数，不在调用点重写样本/区间判定
```

## openspec/changes/counterfactual-replay-foundation/specs/decision-replay-tape/spec.md

- Source: openspec/changes/counterfactual-replay-foundation/specs/decision-replay-tape/spec.md
- Lines: 1-49
- SHA256: 7dba5a0e9eeb3dcfc0e32cab4f6cab66fa3b156f90d55602355f34326fb5278d

```md
## ADDED Requirements

### Requirement: 决策点磁带落盘
系统 SHALL 在 Judge 每次开仓决策点（包括 accept 与 reject）原子追加一条 `decision_replay_record` 到独立磁带文件，捕获足以未来忠实回放的完整输入与输出 bundle。

#### Scenario: 开仓 accept 落磁带
- **WHEN** Judge 发布 `trade_decision.v2` 且 action 为 open_long/open_short
- **THEN** 磁带追加一条记录，含 `request_id`、`timestamp`、`symbol`、`decision="accept"`、`tech_analysis` 9 维全量快照、`price_at_decision`、`regime_state`、`llm_audit_ref`、`trade_decision_output`（plan + attribution）

#### Scenario: 拒单也落磁带
- **WHEN** Judge 拒绝一个开仓计划（任一 gate 拦截）
- **THEN** 磁带追加一条记录，`decision="reject"`，含同样的输入 bundle 加 `reject_reason` 与拒单 attribution

#### Scenario: 原子写不污染主链路
- **WHEN** 磁带 writer 写入失败或抛异常
- **THEN** 异常 SHALL NOT 传播进 Judge 决策路径，记录 fail-safe 丢弃并计数告警，决策正常继续

### Requirement: 磁带 LLM 输出自包含
系统 SHALL 在磁带中内联存储 parsed LLM 输出（action/confidence/reasoning/key_factors/risk_warnings），使磁带自包含、不依赖 `logs/llm_audit_*.jsonl` 存活；`llm_audit_ref` 作为 7 天内可取原始 prompt 的 best-effort 指针。

#### Scenario: 内联输出抗 llm_audit 过期
- **WHEN** 一条 accept/reject 记录由 LLM 参与决策，且其后 llm_audit 文件已过 7 天保留期被清理
- **THEN** 磁带内 `llm_output_inline` SHALL 仍可被回放读取到当时 LLM 输出，无需 llm_audit

#### Scenario: 规则降级无 LLM
- **WHEN** 决策由规则引擎降级产生（LLM 不可用）
- **THEN** `llm_output_inline` SHALL 为 null，记录照常落带

### Requirement: 磁带 observability-only write-only
系统 SHALL 保证决策磁带为纯观测写入，任何 gate/veto/halt/rank/daily-stop SHALL NOT 读取磁带做交易决策。

#### Scenario: 磁带不进决策路径
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其代码路径 SHALL NOT 读取 `decision_replay_tape` 文件或 writer 状态

### Requirement: 磁带路径与 retention 受控
系统 SHALL 经 `utils/state_paths.py` 派生磁带文件路径（禁止硬编码），并支持 retention 配置与 feature flag 关停。

#### Scenario: 命名空间隔离
- **WHEN** `STATE_NAMESPACE` 为 testnet/paper
- **THEN** 磁带文件 SHALL 带对应前缀，与 live 隔离

#### Scenario: flag 关停回到现状
- **WHEN** 决策磁带 feature flag 关闭
- **THEN** 系统 SHALL NOT 写磁带、SHALL NOT 产生任何文件，且决策行为零变化

#### Scenario: retention 滚动封顶
- **WHEN** 磁带超过配置的保留窗口（默认 90 天）或总大小上限
- **THEN** 系统 SHALL 按先到条件滚动清理最旧数据，不无界增长
```

## openspec/changes/counterfactual-replay-foundation/specs/tick-snapshot-capture/spec.md

- Source: openspec/changes/counterfactual-replay-foundation/specs/tick-snapshot-capture/spec.md
- Lines: 1-41
- SHA256: c02d1b1cef8eeffe2c338cfb8f99162730ef7079965d23eeb1d8875bc8a0e157

```md
## ADDED Requirements

### Requirement: 前向 1 秒聚合 bar 采集
系统 SHALL 提供独立的轻量采集模块，从上线日起以 1 秒聚合 bar（OHLC+volume）持久化价格数据到独立 `klines_1s.db`（复用 kline schema，不污染主 klines.db），与 9 维行情采集解耦。

#### Scenario: 上线即采集 1s bar
- **WHEN** tick 采集 feature flag 开启
- **THEN** 系统 SHALL 持续采集并持久化在交易标的的 1 秒聚合 bar 到 `klines_1s.db`，带时间戳

#### Scenario: 不污染主 klines.db
- **WHEN** 1s bar 写入
- **THEN** 系统 SHALL 写独立 `klines_1s.db`，主 `data/klines.db` 内容不受影响

#### Scenario: 独立于行情主链路
- **WHEN** tick 采集模块发生故障或被关停
- **THEN** `multi_data_collector` 的 9 维行情采集与决策主链路 SHALL 不受影响

### Requirement: 有界写入不阻塞
系统 SHALL 以有界方式（批量 flush / 采样）写入 tick 快照，不得阻塞采集或决策循环。

#### Scenario: 批量 flush
- **WHEN** tick 快照高频到达
- **THEN** 写入 SHALL 批量/异步进行，单条写入不得同步阻塞主循环

### Requirement: 价格精度双轨数据源
系统 SHALL 让反事实 PnL 的价格判定优先使用 tick 快照（上线后的时段），缺 tick 时退化用 1m K 线。

#### Scenario: tick 优先
- **WHEN** 某被拒单的存续时段存在 tick 快照
- **THEN** SL/TP 触发判定 SHALL 用 tick 精度，避免 1m 同根 SL/TP 不可判

#### Scenario: 缺 tick 退化 1m
- **WHEN** 某时段无 tick 快照（上线前历史）
- **THEN** 系统 SHALL 退化用 1m K 线 + SL-first 保守假设，并计入偏差带

### Requirement: tick 路径与 retention 受控
系统 SHALL 经 `utils/state_paths.py` 派生 tick 文件路径，支持 retention 与 feature flag 关停。

#### Scenario: flag 关停无残留
- **WHEN** tick 采集 feature flag 关闭
- **THEN** 系统 SHALL NOT 采集或写 tick 文件，且不影响其余功能
```

