---
comet_change: cf-choppy-neutral-tp1-floor-ab
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-24-cf-choppy-neutral-tp1-floor-ab
status: final
---

# Design: cf-choppy-neutral-tp1-floor-ab

> Canonical 需求源 = OpenSpec delta spec `openspec/changes/cf-choppy-neutral-tp1-floor-ab/specs/cf-choppy-neutral-tp1-floor-ab/spec.md`。本文档只记技术实现/风险/测试，不重复定义需求。

## 1. 背景与已验证机制

深查 13 笔已结算「边缘60」亏损单（均 PnL −2.58U）证明同一原型：**choppy + neutral + `effective_risk_reward_ratio` 1.51–1.65 贴 1.50 地板，而 `effective_rr_tp1` 1.28–1.40 全部 < 1.50**。靠 lever2 阶梯口径抬过地板进场。

反事实机制已在源码验证：
- `judge.py:_build_plan`(3690) → `_effective_rr_for_plan`(3682)：`_ladder_rr_enabled=True` 返回阶梯口径；`False` 返回 TP1-only 口径 `(notional*tp1 - cost)/(gross_loss+cost)`。
- floor gate `judge.py:1483`：读 `plan['effective_risk_reward_ratio']` 比 `_select_rr_floor` 返回的 `min_rr`；< floor → `rr_below_floor` reject。
- `utils/decision_replay.py:replay_decision` 真实重跑 `_make_decision`/`_build_plan`，`_install_config_flags`(233) 接受 `ladder_rr_enabled` override。

⟹ 对 choppy+neutral 多单 toggle `ladder_rr_enabled` 即干净复现「TP1 口径地板」反事实，零 live 代码发散。

## 2. 数据流

```
decision_replay_tape.jsonl (accept 流, replayable, 有 state_snapshot)
        │  scope_filter(regime, trend==neutral, action=open_long)
        ▼
  ┌──────────── classify_accepts (per record) ────────────┐
  │  baseline = replay(LADDER_ON)                          │
  │     not accept → baseline_mismatch (exclude)           │
  │  cf = replay(LADDER_OFF)                               │
  │     accept                       → survives_tp1_floor  │
  │     reject & rr_below_floor       → tp1_floor_rejected │
  │     reject & other reason         → other_flip (report)│
  └────────────────────────────────────────────────────────┘
        │  两结算桶: tp1_floor_rejected / survives_tp1_floor
        ▼
  extract_settle_fields → dedup_clusters(symbol+side,>1h)
        ▼
  settle_clusters(klines_1s→klines, TP1 保守 R) → bucket_verdict(min_sample=30)
        ▼
  print 主桶(choppy) + 旁路桶(mixed) + real PnL fuzzy-join sanity
```

## 3. 单元划分（每个一职、可独立测）

| 函数 | 职责 | 依赖 |
|---|---|---|
| `load_tape_accepts(path)` | 读磁带，过滤 accept+replayable+有快照 | 文件 |
| `scope_filter(records, regime)` | 按 regime + trend==neutral + open_long 过滤 | 录值 |
| `classify_accepts(records, replay_fn)` | 两臂复盘 + 自检闸 + 翻转纯度分类 | `replay_decision`（可注入） |
| `_reject_reason(decision)` | 取 reject 首段原因 | — |
| `extract_settle_fields(rec)` | 提结算字段，`_plan` 传 resolve 契约字段 | — |
| `dedup_clusters` / `load_bars` / `settle_clusters` / `bucket_verdict` / `fuzzy_join_real_pnl` | 与 ev-decouple 同形态结算栈 | `resolve_counterfactual` / `summarize_bucket` / sqlite |
| `main()` | 编排主桶 + mixed 旁路 + 打印 | 上述 |

`classify_accepts` 的 `replay_fn` 参数化是关键可测点（单测注入假 replay 验证四类分支）。

## 4. 关键决策

1. **toggle 复用 lever2 开关**，不另写门逻辑（最小失真）。`LADDER_ON`=baseline 自检锚（live lever2 默认开），`LADDER_OFF`=CF TP1 地板。
2. **翻转纯度门**（用户确认）：只有 CF 臂 `reject_reason` 首段 == `rr_below_floor` 才归 `tp1_floor_rejected`；其它原因翻转归 `other_flip` 桶、报告标出、不结算。保证净 R 可干净归因到 TP1 地板。
3. **scope 录值过滤**：`regime_state`(顶层) + `tech_analysis.trend.direction`，不依赖 replay 输出（避免循环依赖）。主桶 choppy、旁路 mixed。
4. **结算契约**：`_plan` 传 `entry_price`/`created_at`/`side`/`stop_loss`/`take_profit`（ev-decouple Critical 教训：传原始 plan 的 `entry_ref`/无 `created_at` 会真跑 KeyError，被 mock-resolve 测试掩盖）。
5. **诚实门 min_sample=30 不下调**；两桶均通过才下「收紧 +EV」结论。

## 5. 风险 / 取舍

| 风险 | 缓解 |
|---|---|
| 主桶样本 <30 → INSUFFICIENT_SAMPLE | 如实报；mixed 旁路补样本；常驻累积后重跑 |
| klines_1s 覆盖近 ~数日 ~数十标的 | 无覆盖簇跳过+计数，与姊妹 driver 同限 |
| `ladder_rr_enabled=False` 也改 sizing 口径 | 低 R:R 缩仓本就用 `effective_rr_tp1`；本驱动只看 accept/reject + 结算，sizing 不入结论 |
| over-determination（某单 baseline 就被它门拦） | baseline 自检判非 accept 排除，不误计翻转 |
| 观测非因果 | 诚实门 + baseline 自检 + 翻转纯度门三重护栏；结论限定「反事实」措辞 |

## 6. 测试策略

- **驱动单测**（镜像 `tests/test_ev_decouple_ab.py` 形态，不全 mock resolve）：
  - `classify_accepts` 注入假 `replay_fn`：验证 baseline_mismatch 排除 / rr_below_floor→tp1_floor_rejected / other reason→other_flip / 两臂 accept→survives。
  - `extract_settle_fields`：断言 `_plan` 含 `entry_price`+`created_at`、不含 `entry_ref`；非正距/缺字段返回 None。
  - `scope_filter`：choppy/mixed + neutral + open_long 过滤正确，bullish/short/非 neutral 被排除。
- **红线守卫**：`tests/test_cf_red_line_guard.py::test_decision_paths_do_not_read_choppy_tp1_floor_ab`（决策/风控模块源码不含驱动名）。
- **全量**：`python3 -m pytest -q` 绿（1416 + 新测试）；`compileall` 通过。
- **真跑**：`python3 cf_choppy_neutral_tp1_floor_ab.py` 出主桶/旁路两桶净 R + 诚实门裁定，结论入 verify 报告（不改 config/不上 live）。
