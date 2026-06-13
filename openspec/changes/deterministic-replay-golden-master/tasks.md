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
