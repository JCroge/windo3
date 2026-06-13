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
