# Tasks: cf-choppy-neutral-tp1-floor-ab

## 1. 驱动骨架与加载

- [ ] 1.1 新建 `cf_choppy_neutral_tp1_floor_ab.py`（repo 根），module docstring 标 observability-only write-only + 红线，常量 `LADDER_ON={"ladder_rr_enabled": True}` / `LADDER_OFF={"ladder_rr_enabled": False}`、`TAPE`/`KL1`/`KL`/`LIFECYCLE` 路径。
- [ ] 1.2 `load_tape_accepts()`：读 `decision_replay_tape.jsonl`，过滤 `decision=="accept" AND replayable AND state_snapshot_before_decision`（镜像 ev-decouple）。
- [ ] 1.3 `scope_filter(records, regime)`：按 `regime_state==regime AND tech_analysis.trend.direction=="neutral" AND` 录值 action 为 open_long 过滤；主桶 regime=choppy，旁路 regime=mixed。

## 2. 两臂分类与自检闸

- [ ] 2.1 `classify_accepts(records, replay_fn=replay_decision)`：每条先 `replay(LADDER_ON)`，非 accept→`baseline_mismatch` 排除；再 `replay(LADDER_OFF)`，翻 reject→`tp1_floor_rejected`，仍 accept→`survives_tp1_floor`；返回三类 + mismatch 计数 + 翻转拒因 Counter。
- [ ] 2.2 `_reject_reason(decision)`：从 CF 臂 reject 决策取 blocked_by/reject_reason 首段（镜像 ev-decouple），确认翻转主因是 rr_below_floor。

## 3. 结算与诚实门（复用 ev-decouple helper 形态）

- [ ] 3.1 `extract_settle_fields(rec)`：从 plan 提 `side`/`entry_ref`/`stop_loss`/`take_profit` 算 `_sl_dist`/`_tp1_dist`，`_plan` 传 `entry_price`/`created_at`/`side`/`stop_loss`/`take_profit`（**非 `entry_ref`**，ev-decouple Critical 教训）；缺字段或非正距返回 None。
- [ ] 3.2 `dedup_clusters` / `load_bars`(klines_1s→klines fallback) / `settle_clusters`(TP1 保守 R) / `bucket_verdict`(min_sample=30 不下调) / `fuzzy_join_real_pnl`(matched only)。
- [ ] 3.3 `main()`：主桶 + mixed 旁路各跑 classify→settle→verdict，打印两桶（`tp1_floor_rejected` / `survives_tp1_floor`）簇数/结算/净 R/簇/诚实门裁定 + 解读判据注脚 + klines 覆盖限制注脚。

## 4. 红线守卫与测试

- [ ] 4.1 扩展 `tests/test_cf_red_line_guard.py`：新增 `test_decision_paths_do_not_read_choppy_tp1_floor_ab`，断言决策/风控模块源码不含 `cf_choppy_neutral_tp1_floor_ab`。
- [ ] 4.2 新增驱动单测（镜像 ev-decouple 测试）：`classify_accepts` 用 mock replay_fn 验证翻转/自检闸/mismatch 排除；`extract_settle_fields` 验证传 `entry_price`/`created_at` 而非 `entry_ref`（不全 mock resolve，集成 sanity）；scope_filter 验证 choppy/mixed+neutral 过滤。
- [ ] 4.3 全量 `python3 -m pytest -q` 绿（基线 1416 + 新测试），`compileall` 通过。

## 5. 真跑与结论

- [ ] 5.1 `python3 cf_choppy_neutral_tp1_floor_ab.py` 真跑，记录主桶/旁路两桶净 R/簇 + 诚实门裁定 + 翻转单数。
- [ ] 5.2 结论写入 verify 报告：是否「收紧对 choppy+neutral +EV」（仅诚实门通过时下结论），样本薄则标 suggestive + 常驻累积重跑；real PnL sanity join 对照。**不改 config、不上 live**——是否上 live 由后续 change 另议。
