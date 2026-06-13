---
comet_change: deterministic-replay-golden-master
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-14-deterministic-replay-golden-master
status: final
---

# Deterministic Replay + Golden Master — 技术设计 (L2)

> 需求事实源是 OpenSpec：`openspec/changes/deterministic-replay-golden-master/{proposal,design,specs/*}.md`。
> 本文档只讲 HOW。需求新增以 delta spec 回写为准。

## 1. 范围回顾

反事实策略实验室 #2。三件事：① 决策磁带扩存 ~14 个跨决策状态白名单快照（forward-only）② 确定性回放 harness（还原状态 + mock + 喂 tech 调真实 `_make_decision` + 截获 publish + golden 比对）③ 端到端 replay-report driver（补 L1 I1）。observability-only write-only。只收决策层。

## 2. 模块边界

```
Judge._capture_state_snapshot()  ── 读 self.* ~14 字段 → 原始 dict（字段知识在 Judge）
decision_tape._jsonable()        ── set→list 等 JSON 化（通用 util）
        ↓ 经 build_bundle 落 state_snapshot_before_decision
utils/decision_replay.py
  ├─ restore_state(judge, snapshot)   ── list→set 还原 self.*
  ├─ replay_decision(record)          ── __new__ + restore + mock + _make_decision + capture
  └─ compare_decision(replayed, recorded) → DiffResult（分层比对）
replay_report.py / cf_replay_driver  ── 读 rejected_signal_events.jsonl + klines → resolve → build_cf_report
```

## 3. 关键技术决策

### D1 — 快照落点：字段知识在 Judge，序列化在 decision_tape
- Judge `_capture_state_snapshot() -> dict`：直接读自身 `_open_positions`/`_pending_open_symbols`/`_position_slots`/`_pending_open_slots`/`_archetype_cooldown`(history+cooldown_until)/`_recent_wins`/`_total_completed_trades`/`_recent_win_rate`/`_probe_short_active`/`_probe_short_sl_count`/`_probe_short_cooldown_until`/`_symbol_state[symbol]`/`_available_balance`/`_regime_manager.snapshot()`。
- `decision_tape._jsonable(v)`：纯函数，set→sorted list、dataclass→dict，保证可序列化。
- accept/reject 两接线点：`snapshot = self._capture_state_snapshot(symbol)` 传入 `build_bundle`。受 `DECISION_TAPE_ENABLED` flag 控制。

### D2 — 回放 harness：__new__ + restore + 全 mock + capture
- `MultiJudge.__new__(MultiJudge)` → `restore_state` 白名单还原（list→set；regime snapshot 还原 `_regime_manager` 必要字段或注入轻量 stub 返回 effective_regime）。
- **mock 全部已知非确定源**（build 第一步枚举决策路径所有 await/外部调用）：`time.time()`→record timestamp（`mock.patch`）；`_update_balance`→no-op（余额由快照置）；`_ask_llm`→返回 `llm_output_inline`；`publish`→capture 到 list；任何其它 await 外部调用（fetch_*）→stub。
- `replay_decision(record)`：在 asyncio 事件循环跑单次 `await judge._make_decision(symbol, tech)`，取 captured payload。
- 复用真实 `_make_decision`，不重写决策逻辑。

### D3 — golden 比对：分三层定义"复现"
- **严格字节级（fail）**：`action`/`confidence`/`dispatch_path`/`entry_type`/`slot_type`/`is_probe`/`is_low_rr`/`short_gate_decision`/`short_gate_reason`/`rr_policy`/`rr_floor_used`/`entry_position_status`/`entry_position_block_reason`/`blocked_by`。
- **连续容差 <0.5%（fail if 超）**：`size_usdt`/`entry_ref`/`stop_loss`/`take_profit`（逐元素）/`leverage`。
- **仅信息（报 diff 不 fail）**：`reasoning`/`key_factors`/`risk_warnings`（LLM 自由文本透传）。
- `DiffResult` 含逐字段 diff + 总判定（match / mismatch）。**复现钉在决策逻辑（gate/action/sizing），不被 LLM 文本噪声污染。**

### D4 — 端到端 driver
- 读 `rejected_signal_events.jsonl`；每条被拒单取 `[created_at, created_at+24h]` 窗口（对齐 shadow 过期），`klines_1s.db` 优先、`klines.db` 1m 退化；两者都不覆盖→跳过+计数。
- 每条 → `resolve_counterfactual(record, bars, source=...)` → row（含 reject_reason/effective_regime/side/outcome/net_usdt/price_ambiguous/source）→ `build_cf_report`。
- 与 golden-master 回放是两条独立读磁带流（验复现 vs 出分析），共享读层。

### D5 — 验证：fixture 先行，真实数据终验 follow-up
- 合成 fixture：手造完整状态快照 + tech + 期望 decision，覆盖 main accept、reject（某 gate）、slot 满拒单、regime 相关分支；复现一致 + 故意改一字段→diff 暴露。
- 真实终验：N≥50 条带状态 record，运维手动跑 driver 期望 replayable 100% 复现（runbook，follow-up）。

## 4. 红线守卫
observability-only write-only：状态快照 / 回放 harness / driver 严禁被 gate/veto/halt/rank/daily-stop 读取。扩展 `tests/test_cf_red_line_guard.py`。harness `__new__` 隔离构造，绝不触真实交易所/总线/状态文件。

## 5. 测试策略
- **decision-state-snapshot**：快照含全字段 / set 可序列化 / 不 pickle / 旧 record 标 replayable=false / flag 关停不采集。
- **harness**：fixture 状态还原 / time/exchange/llm/publish mock 生效 / 复用真实 `_make_decision` / 复现不重算 PnL。
- **golden 比对**：离散字节级 fail / 连续 <0.5% pass、超出 fail / reasoning 仅报 diff / 逐字段 diff 定位 / 改一字段暴露。
- **driver**：端到端出报表 / 双轨取价 / 缺数据降级不中断。
- **红线守卫** + **零回归**：flag 全关 == L1 行为；全量 pytest 不低于 1185。

## 6. 风险 / 取舍

| 风险 | 缓解 |
|---|---|
| 决策路径有未 mock 的 await 外部调用 → 回放挂死/打网络 | build 第一步枚举 `_make_decision` 全部 await，逐一 stub；fixture 测试暴露遗漏 |
| 状态快照不全 → 复现 fail | 白名单完整集 + golden 覆盖多 gate 分支；遗漏表现为 diff 可迭代补 |
| regime snapshot 还原不足 | 还原 `_regime_manager` 决策读取的字段（effective_regime 等）；必要时轻量 stub |
| 状态快照膨胀磁带 | 白名单非全对象；retention 沿用 L1 |
| L1 无快照 record 被误当可复现 | `replayable` flag，harness 跳过 |
| 红线误用 | 守卫测试扩展 |

## 7. Spec Patch（回写 delta spec）
深度设计新增/细化已回写：
- `deterministic-replay-harness`：golden 比对三层（严格字节级字段全集 / 连续容差 / reasoning 仅信息）。
- `replay-report-driver`：24h 取数窗口。
- `decision-state-snapshot`：快照落点（Judge `_capture_state_snapshot` + decision_tape `_jsonable`）。
