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
