# Comet Design Handoff

- Change: deterministic-replay-golden-master
- Phase: design
- Mode: compact
- Context hash: 1025e29cc03601b30be6a5222a838596255df231c823b47fad1d5cc9a04c8ce2

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/deterministic-replay-golden-master/proposal.md

- Source: openspec/changes/deterministic-replay-golden-master/proposal.md
- Lines: 1-33
- SHA256: ef4466f6480dd2296795baa578db9ff8ddcad8a733103fff7f21db6dfaac32b9

```md
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
```

## openspec/changes/deterministic-replay-golden-master/design.md

- Source: openspec/changes/deterministic-replay-golden-master/design.md
- Lines: 1-72
- SHA256: 67755203028018e0d32e67ea0b0d6bbb412cca3d125c950535dc95f062862458

```md
## Context

反事实策略实验室 L2。L1 已落地决策磁带 + 反事实 PnL + 诚实 gate（全 observability-only）。L2 探索（Explore agent 精读 `agents/trading/judge.py::_make_decision`）确认：

- Judge 决策依赖 ~14 个跨决策可变状态（slot 占用、archetype cooldown、EV bucket 计数、probe SL 计数、`_symbol_state`、`_available_balance`、`_regime_manager`）。L1 磁带未存这些。
- 非确定性来源已摸清且可控：`time.time()`（mock 成磁带 timestamp）、exchange 余额（快照恢复，不调）、无 random、uuid 仅 request_id（不影响决策）、`publish` 副作用（override 成 capture）。
- 测试已有 `MultiJudge.__new__` + 手设状态的隔离构造范式（`test_rr_floor_policy.py::_make_judge`）。

红线（CLAUDE.md）：observability-only write-only，严禁交易决策读回放产物。

## Goals / Non-Goals

**Goals:**
- 决策磁带扩展存 ~14 个跨决策状态白名单快照（forward-only）。
- 确定性回放 harness：还原状态 + mock time/exchange/bus + 喂 tech 调真实 `_make_decision` + 截获 publish。
- golden-master 比对：离散字节级 + 连续 <0.5% 容差。
- 端到端 replay-report driver（补 I1）。
- 全程 observability-only write-only，零交易行为改动。

**Non-Goals:**
- L3 组合态扰动（counterfactual PnL 反馈进 EV/cooldown）。
- L4 旋钮扫描 + 置信度门。
- event_backtest 执行层（SL/TP/trailing/partial-TP）退役——L2 只收决策层。
- LLM 旋钮扰动（改 prompt/换模型）。
- L1 已攒的无状态 record 的复现（永久不可，只够 L1 级挖掘）。

## Decisions

### D1 — 状态快照：完整集白名单显式字段，不 pickle
- **选择**：在 `build_bundle` 新增可选 `state_snapshot_before_decision`，由 Judge 接线点用白名单 helper 显式序列化 ~14 字段：`_open_positions`(list)、`_pending_open_symbols`(list)、`_position_slots`(dict)、`_pending_open_slots`(dict)、`_archetype_cooldown`(history+cooldown_until)、`_recent_wins`/`_total_completed_trades`/`_recent_win_rate`、`_probe_short_active`/`_probe_short_sl_count`/`_probe_short_cooldown_until`、`_symbol_state[symbol]`、`_available_balance`、`_regime_manager.snapshot()` 全量。
- **替代**：pickle 整个 Judge `__dict__`（臃肿、把实现细节冻进 schema、跨版本脆）；只存最小集（复现不全路径）。
- **理由**：白名单完整集既保真又不泄露实现细节；显式字段让 schema 可演进、可审计。set 转 list 保证 JSON 可序列化。
- **forward-only**：从 L2 上线攒；L1 record 无此字段，回放时 fail-safe 标 `replayable=false`。

### D2 — 回放 harness：隔离构造 + mock 注入 + publish capture
- **选择**：新模块（如 `utils/decision_replay.py`）。`MultiJudge.__new__(MultiJudge)` 绕过 `__init__` → `_restore_state(snapshot)` 白名单还原 `self.*` → mock `time.time()` 为 record timestamp（`unittest.mock.patch`）→ mock exchange（余额直接置 `_available_balance`，`_update_balance` 打桩为 no-op）→ override `publish` 收集 payload 到 list → 注入磁带内联 LLM（在 `_ask_llm` 前置 stub 返回 `llm_output_inline`）→ `await _make_decision(symbol, tech)` → 取 captured payload 为回放输出。
- **理由**：复用真实 `_make_decision`，不重写决策逻辑——这是"线上代码即回测代码"的核心，根治 event_backtest 发散。隔离构造确保绝不触真实交易所/总线/状态文件。
- **复现不重算 PnL**：EV gate 读的 `_recent_wins/_total` 由快照还原为决策当时值，不重算 realized PnL（重算反馈是 L3 范畴）。

### D3 — golden-master 比对：离散字节级 + 连续容差
- **选择**：`compare_decision(replayed, recorded) -> DiffResult`。离散字段（action/confidence/reasoning/dispatch_path/各 gate 决策标签）要求严格相等；plan 连续字段（size_usdt/entry_ref/stop_loss/take_profit/leverage）允许 <0.5% 相对误差。
- **理由**：同输入同代码理论上离散字段字节一致；连续浮点跨平台末位可能微差，极小容差兜底而不放水。比对结果带逐字段 diff 便于定位发散。

### D4 — 端到端 replay-report driver（补 I1）
- **选择**：driver 读 `rejected_signal_events.jsonl`（被拒影子单），对每条按其存续时段从 `klines_1s.db`（缺则 `klines.db`）取 bars → `resolve_counterfactual` → 汇成 rows → `build_cf_report`。这是 L1 各单元的 glue，不依赖状态快照。
- **理由**：补 L1 命名的 I1，让"旧数据立刻见数"真正可运行；与 golden-master 回放是两条独立读磁带流（一个验复现、一个出分析），共享读层。

### D5 — 验证策略：fixture 先行，真实数据终验
- **选择**：开发期写合成 fixture（手造完整状态快照 + tech + 期望 decision），驱动确定性 golden 测试覆盖主路径与若干 gate 分支。真实数据 golden 终验列为 follow-up（待 L2 埋点累积 ≥ N 条带状态 record）。
- **理由**：L2 上线即无历史带状态数据；fixture 让 harness 正确性立刻可测，真实终验是后续运维步骤。

## Risks / Trade-offs

- **[状态快照不全 → 复现失败]** → 白名单完整集 + golden 测试覆盖多 gate 分支暴露遗漏；遗漏字段表现为 diff，可迭代补。
- **[Judge 决策路径有未识别的非确定性]** → harness mock 全部已知源（time/exchange/bus/llm）；若 golden 测试出现非确定 diff，定位补 mock。
- **[`_make_decision` 内部 await 其他 async（如 _update_balance/_ask_llm）]** → 全部打桩；harness 在 asyncio 事件循环跑单次决策。
- **[状态快照体积膨胀磁带]** → 白名单而非全对象；retention 沿用 L1；体积监控。
- **[红线误用：harness/driver 被决策读]** → 守卫测试扩展（同 L1 `test_cf_red_line_guard.py`）。
- **[L1 record 无快照被误当可复现]** → record 标 `replayable` flag，harness 跳过无快照 record。

## Migration Plan

- 纯新增 + 磁带 schema 向后兼容扩展（新增可选字段，旧 record 缺失 fail-safe）。
- 回放 harness/driver 是离线工具，不进生产决策链路。
- 回滚：状态快照采集受 L1 `DECISION_TAPE_ENABLED` flag 控制（关闭即不采集，回到 L1 行为）。

## Open Questions

- 状态快照 helper 放 `decision_tape.py` 还是 Judge 内（取决于哪边更易拿到 self.* 白名单）——build 阶段定。
- golden 比对的离散字段全集清单（哪些 attribution 字段纳入严格比对）——build 阶段对照 `trade_decision.v2` 契约定。
- 真实数据终验的样本量门槛 N 与触发方式（手动 vs 攒够告警）——follow-up。
- replay-report driver 的 klines 取数范围（被拒单存续 24h 窗口 × symbol）与缺数据降级——build 阶段定。
```

## openspec/changes/deterministic-replay-golden-master/tasks.md

- Source: openspec/changes/deterministic-replay-golden-master/tasks.md
- Lines: 1-37
- SHA256: d4af19a2f448d38d070b25763d21db8e3094b3dde988f62930f016f317d699b9

```md
# Tasks — deterministic-replay-golden-master (L2)

> 反事实策略实验室路线图 #2。observability-only write-only，零交易决策影响。
> 深度技术决策（快照 helper 落点、离散字段全集、klines 取数窗口）在 comet-design 的 Superpowers Design Doc 收口。

## 1. 决策状态快照（decision-state-snapshot）

- [ ] 1.1 白名单状态序列化 helper：显式取 ~14 字段（set→list），不 pickle；放 `utils/decision_tape.py` 或 Judge（design 定）
- [ ] 1.2 `build_bundle` 新增可选 `state_snapshot_before_decision` 字段 + `replayable` 标记
- [ ] 1.3 Judge accept/reject 两接线点采集快照传入 record_decision（复用现有 record_decision，受 `DECISION_TAPE_ENABLED` flag 控制）
- [ ] 1.4 单测：快照含全字段、set 可序列化、不 pickle、旧 record 缺快照标 replayable=false、flag 关停不采集

## 2. 确定性回放 harness（deterministic-replay-harness）

- [ ] 2.1 新建回放模块（如 `utils/decision_replay.py`）：`MultiJudge.__new__` 构造 + `_restore_state(snapshot)` 白名单还原（list→set 等）
- [ ] 2.2 确定性 mock：`time.time()`=record timestamp、exchange 余额快照恢复 + `_update_balance` no-op、`_ask_llm` 注入 `llm_output_inline`、`publish` override 为 capture
- [ ] 2.3 单次回放入口：喂 record 的 tech_analysis 调真实 `_make_decision`，返回 captured payload
- [ ] 2.4 golden-master 比对 `compare_decision`：离散字段严格相等 + plan 连续字段 <0.5% 容差 + 逐字段 diff
- [ ] 2.5 单测（合成 fixture 驱动）：手造完整状态快照 + tech + 期望 decision，覆盖 main 路径 accept、reject（某 gate）、slot 满拒单、regime 相关分支；复现一致；故意改一字段→diff 暴露

## 3. 端到端报表 driver（replay-report-driver）

- [ ] 3.1 driver：读 `rejected_signal_events.jsonl` → 每条按存续 24h 窗口从 klines_1s（缺→klines 1m）取 bars → `resolve_counterfactual` → rows → `build_cf_report`
- [ ] 3.2 缺数据降级（跳过 + 计数）、价格源双轨（1s 优先 1m 退化）
- [ ] 3.3 单测：端到端出报表、双轨取价、缺数据降级不中断

## 4. 红线守卫与文档

- [ ] 4.1 扩展 `tests/test_cf_red_line_guard.py`：决策/风控路径不读状态快照 / 回放 harness / driver 产物
- [ ] 4.2 docs：CLAUDE.md 红线补 L2 声明；design/spec 链接；docs/to-do-list.md 路线图更新（#2 完成，#3/#4 待做）；真实数据终验列为 follow-up
- [ ] 4.3 memory：更新 [[counterfactual_replay_lab_roadmap]] L2 完成

## 5. 验证

- [ ] 5.1 全量 `python3 -m pytest -q` 通过，基线不回归（当前 1185，只增不减）
- [ ] 5.2 `python3 -m compileall -q .` 通过
- [ ] 5.3 零回归确认：`DECISION_TAPE_ENABLED=false` 时状态快照不采集、决策不变
```

## openspec/changes/deterministic-replay-golden-master/specs/decision-state-snapshot/spec.md

- Source: openspec/changes/deterministic-replay-golden-master/specs/decision-state-snapshot/spec.md
- Lines: 1-38
- SHA256: 7ba4b7ae6627bce941c3c773f4d67bb452631105fe17711233f564bad6be13b8

```md
## ADDED Requirements

### Requirement: 决策点跨决策状态白名单快照
系统 SHALL 在 Judge 每次开仓决策点（accept + reject）随决策磁带记录一份 `state_snapshot_before_decision`，白名单显式序列化决策依赖的跨决策可变状态，禁止 pickle 整个对象。

#### Scenario: 快照含全部白名单字段
- **WHEN** Judge 决策落磁带
- **THEN** `state_snapshot_before_decision` SHALL 含 `_open_positions`、`_pending_open_symbols`、`_position_slots`、`_pending_open_slots`、archetype cooldown（history + cooldown_until）、`_recent_wins`、`_total_completed_trades`、`_recent_win_rate`、`_probe_short_active`、`_probe_short_sl_count`、`_probe_short_cooldown_until`、`_symbol_state[symbol]`、`_available_balance`、`_regime_manager` 完整 snapshot

#### Scenario: set 可 JSON 序列化
- **WHEN** 快照含 set 类型状态（如 `_open_positions`）
- **THEN** 系统 SHALL 转为 list 落盘，保证磁带 JSON 可序列化

#### Scenario: 不 pickle 实现细节
- **WHEN** 序列化状态快照
- **THEN** 系统 SHALL 只取白名单字段，SHALL NOT pickle/dump 整个 Judge `__dict__`

#### Scenario: 快照落点职责分离
- **WHEN** 采集状态快照
- **THEN** 字段收集 SHALL 由 Judge `_capture_state_snapshot()`（知道自身字段）完成，JSON 化（set→list 等）SHALL 由 `decision_tape` 纯 helper 完成

### Requirement: 快照 forward-only 且向后兼容
系统 SHALL 仅对启用后产生的 record 写状态快照；缺快照的旧 record（L1）回放时 SHALL fail-safe 标记不可复现，不报错。

#### Scenario: 旧 record 标不可复现
- **WHEN** 回放读到无 `state_snapshot_before_decision` 的 record
- **THEN** 系统 SHALL 标 `replayable=false` 并跳过 golden-master 复现，不抛异常

#### Scenario: flag 关停不采集
- **WHEN** 决策磁带 feature flag 关闭
- **THEN** 系统 SHALL NOT 采集状态快照，回到 L1 行为

### Requirement: 状态快照 observability-only write-only
系统 SHALL 保证状态快照为纯观测写入，任何 gate/veto/halt/rank/daily-stop SHALL NOT 读取快照做交易决策。

#### Scenario: 快照不进决策路径
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其代码路径 SHALL NOT 读取 `state_snapshot_before_decision`
```

## openspec/changes/deterministic-replay-golden-master/specs/deterministic-replay-harness/spec.md

- Source: openspec/changes/deterministic-replay-golden-master/specs/deterministic-replay-harness/spec.md
- Lines: 1-57
- SHA256: 6e43ee905fc8efdeb819bf600b4c6531b6a0e801d8e0d23aff94083d14d791b9

```md
## ADDED Requirements

### Requirement: 隔离回放构造真实 Judge
系统 SHALL 用 `MultiJudge.__new__` 绕过 `__init__` 构造 Judge，从 record 的状态快照白名单还原 `self.*`，并复用真实 `_make_decision` 决策逻辑，不重写评分/gate。

#### Scenario: 状态还原
- **WHEN** 给定一条带状态快照的 record
- **THEN** harness SHALL 还原快照内全部白名单 `self.*` 字段（list 还原回 set 等），使 Judge 看到与历史一致的隐藏状态

#### Scenario: 复用真实决策代码
- **WHEN** harness 执行回放
- **THEN** 其 SHALL 调用真实 `MultiJudge._make_decision`，SHALL NOT 另写第二份评分/gate/RR-floor 实现

### Requirement: 回放确定性 mock
系统 SHALL mock 决策路径全部已知非确定性来源，使同一 record 回放结果确定。

#### Scenario: 时间确定
- **WHEN** 回放执行
- **THEN** `time.time()` SHALL 返回 record 的 timestamp，使 cooldown/TTL/deferred timeout 判定确定

#### Scenario: 不触交易所
- **WHEN** 回放需要余额
- **THEN** 系统 SHALL 用快照 `_available_balance` 恢复，余额刷新打桩为 no-op，SHALL NOT 调真实交易所

#### Scenario: LLM 复用内联
- **WHEN** 回放走 LLM 决策路径
- **THEN** 系统 SHALL 注入 record 的 `llm_output_inline`，SHALL NOT 重新调用 LLM

#### Scenario: publish 截获
- **WHEN** 回放中 Judge 调用 `publish`
- **THEN** harness SHALL override 为 capture，收集 payload 而非发真实总线消息

### Requirement: golden-master 决策比对
系统 SHALL 比对回放输出与 record 的 `trade_decision_output`：离散字段严格相等，plan 连续字段允许极小相对容差。

#### Scenario: 严格字节级字段（决定决策）
- **WHEN** 比对回放与历史决策
- **THEN** `action`/`confidence`/`dispatch_path`/`entry_type`/`slot_type`/`is_probe`/`is_low_rr`/`short_gate_decision`/`short_gate_reason`/`rr_policy`/`rr_floor_used`/`entry_position_status`/`entry_position_block_reason`/`blocked_by` SHALL 严格相等，任一不等即判 mismatch

#### Scenario: 连续字段容差
- **WHEN** 比对 plan 的 `size_usdt`/`entry_ref`/`stop_loss`/`take_profit`（逐元素）/`leverage`
- **THEN** 系统 SHALL 允许 <0.5% 相对误差，超出即判 mismatch

#### Scenario: 自由文本仅信息不判负
- **WHEN** 比对 `reasoning`/`key_factors`/`risk_warnings`（LLM 自由文本透传）
- **THEN** 系统 SHALL 记录 diff 但 SHALL NOT 因其不一致判 mismatch（golden-master 钉决策逻辑，不钉自由文本）

#### Scenario: 复现不重算 PnL
- **WHEN** 回放经过 EV gate
- **THEN** 系统 SHALL 用快照 `_recent_wins`/`_total_completed_trades` 还原值，SHALL NOT 重算 realized PnL

### Requirement: harness observability-only write-only
系统 SHALL 保证回放 harness 为离线工具，严禁被任何 gate/veto/halt/rank/daily-stop 读取或进入生产决策链路。

#### Scenario: harness 不进生产链路
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其 SHALL NOT import 或调用回放 harness
```

## openspec/changes/deterministic-replay-golden-master/specs/replay-report-driver/spec.md

- Source: openspec/changes/deterministic-replay-golden-master/specs/replay-report-driver/spec.md
- Lines: 1-27
- SHA256: 9a8a21a8eed7a66ff9b8814a8c6024a43bae0e9cc1c4e82b8b8dab8696c1a697

```md
## ADDED Requirements

### Requirement: 端到端被拒单反事实报表 driver
系统 SHALL 提供 driver 读取被拒影子单 `rejected_signal_events.jsonl`，对每条按其存续时段取价格 bars，经 `resolve_counterfactual` 解析后由 `build_cf_report` 汇成分桶报表。

#### Scenario: 端到端可运行
- **WHEN** driver 在有被拒单历史 + klines 数据时运行
- **THEN** 系统 SHALL 输出按 reject_reason×regime×side 分桶、经诚实 gate 的反事实报表，无需手工拼装 rows

#### Scenario: 取数窗口对齐 shadow 过期
- **WHEN** driver 解析某被拒单
- **THEN** 其 SHALL 取 `[created_at, created_at+24h]` 窗口的 bars（对齐 CounterfactualLedger shadow 24h 过期）

#### Scenario: 价格源双轨
- **WHEN** 某被拒单存续时段有 1s bar（`klines_1s.db`）
- **THEN** driver SHALL 优先用 1s bar 取价精度；缺则退化 `klines.db` 1m

#### Scenario: 缺数据降级
- **WHEN** 某被拒单时段无任何 klines 覆盖
- **THEN** driver SHALL 跳过该条并计数，不中断整体报表

### Requirement: driver observability-only
系统 SHALL 保证 driver 为离线分析工具，输出严禁被任何 gate/veto/halt/rank/daily-stop 读取做交易决策。

#### Scenario: driver 输出不进决策
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其 SHALL NOT 读取 driver 报表产物
```

