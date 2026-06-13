## Why

反事实策略实验室路线图 #2（L2）。L1 落地了决策磁带（`decision_replay_tape.jsonl`，Judge accept/reject 的 tech_analysis + price + regime + 内联 LLM + 决策输出），但 L1 review 与 L2 探索都发现一个更深的缺口：**`MultiJudge._make_decision` 不是纯函数**——它除了当次 tech_analysis，还依赖约 14 个跨决策可变状态（slot 占用 `_open_positions`/`_pending_open_slots`、archetype cooldown、EV bucket 计数 `_recent_wins`/`_total_completed_trades`、probe short SL 计数、`_symbol_state`、`_available_balance`、`_regime_manager` 内部状态）。同一个 tech，slot 满/空、regime bullish/bearish 时决策完全不同。

**后果**：L1 磁带没存这些状态 → 拿现有 record 喂真实 Judge **复现不出历史决策**。要让"反事实实验室"可信，必须先证明回放能 bit 级复现历史（golden master）——否则 L3/L4 的每个反事实数字都是空中楼阁。这一步同时让真实 Judge 代码成为回测引擎，从根上治 `event_backtest` 决策层另写一套评分/gate 的发散病。

## What Changes

- **扩展决策磁带埋点（keystone）**：决策点连同 ~14 个跨决策可变状态一起白名单显式快照（**不 pickle** 整个对象），forward-only。observability-only write-only，不改 Judge 决策逻辑。
- **新增确定性回放 harness**：`MultiJudge.__new__` 构造 + 从状态快照还原 `self.*` + mock `time.time()`=磁带 timestamp + mock exchange（余额用快照恢复，**不调交易所**）+ override `publish` 为 capture；喂磁带 tech_analysis 调真实 `_make_decision`，截获 published payload。复现**不重算 PnL**（只用快照计数值）。
- **新增 golden-master 比对**：离散字段（action/confidence/reasoning）字节级一致；plan 连续字段（size_usdt/entry/sl/tp）<0.5% 容差。
- **新增端到端 replay-report driver**：补 L1 命名的 I1 边界——读 `rejected_signal_events.jsonl` + klines/klines_1s → `counterfactual_pnl.resolve_counterfactual` → `replay_report.build_cf_report`，让"旧数据立刻见数"真正可运行。
- 不改变任何交易/风控行为；不退役 `event_backtest` 执行层模拟（L2 只收决策层）。

## Capabilities

### New Capabilities
- `decision-state-snapshot`: 决策磁带扩展——决策点白名单快照 ~14 个跨决策可变状态（forward-only，observability-only write-only），使后续回放可忠实还原 Judge 决策时的全部隐藏输入。
- `deterministic-replay-harness`: 隔离回放引擎 + golden-master 比对——还原状态快照、mock time/exchange/bus、喂 tech 调真实 `_make_decision`、截获 publish，按离散字节级 + 连续容差判定复现，复现不重算 PnL。
- `replay-report-driver`: 端到端被拒单反事实报表 driver——读被拒影子单 + klines → `resolve_counterfactual` → `build_cf_report`，补齐 L1 的 I1 边界。

### Modified Capabilities
<!-- decision-replay-tape（L1 master spec）的 record schema 被扩展（新增 state_snapshot_before_decision）。归档时以 delta 形式补到 decision-replay-tape；但本 change 用新 capability decision-state-snapshot 表达扩展语义，避免改写 L1 既有 requirement。 -->

## Impact

- **扩展代码**：`utils/decision_tape.py::build_bundle`（新增 `state_snapshot_before_decision` 可选字段）；Judge 接线点采集状态快照（`agents/trading/judge.py` accept/reject 两处，复用现有 record_decision，白名单序列化 helper）。
- **新增代码**：回放 harness（如 `utils/decision_replay.py`：状态还原 + mock 注入 + publish capture + golden 比对）；端到端 driver（如 `replay_report.py` 扩展或新 `cf_replay_driver.py`）。
- **复用既有**：L1 的 `decision_tape` / `counterfactual_pnl` / `cf_honesty_gate` / `build_cf_report` / `klines.db` / `klines_1s.db`；真实 `MultiJudge` 代码（不重写决策逻辑）。
- **验证**：开发期合成 fixture 驱动确定性 golden 测试（手造状态快照 + tech + 期望输出）；真实数据终验待 L2 埋点累积（明确为 follow-up）。
- **红线合规**：observability-only write-only，回放 harness/driver 严禁被任何 gate/veto/halt/rank/daily-stop 读取；红线守卫测试扩展。harness 用 `MultiJudge.__new__` 隔离构造，绝不触真实交易所/总线/状态文件。
- **非目标（留后续 change）**：L3 组合态扰动（counterfactual PnL 反馈进 EV/cooldown）、L4 旋钮扫描、event_backtest 执行层退役、LLM 旋钮扰动。
- **状态快照体积**：~14 字段 JSON，随每条 record 落盘；retention 沿用 L1 磁带配置，体积监控。
