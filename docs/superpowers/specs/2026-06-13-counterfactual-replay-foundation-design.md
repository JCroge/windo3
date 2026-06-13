---
comet_change: counterfactual-replay-foundation
role: technical-design
canonical_spec: openspec
---

# Counterfactual Replay Foundation — 技术设计 (L1 + 原料地基)

> 需求事实源是 OpenSpec：`openspec/changes/counterfactual-replay-foundation/{proposal,design,specs/*}.md`。
> 本文档只讲 HOW（架构、数据 schema、算法、边界、测试）。需求新增以 delta spec 回写为准，本文不重复定义需求。

## 1. 范围回顾

反事实策略实验室路线图 #1：交付 **L1 可信被拒单回放** + **未来忠实回放的原料地基**。observability-only write-only，零交易决策影响。L2/L3/L4 与 LLM 旋钮扰动是后续 change。

## 2. 模块边界

四个独立单元，各一个明确职责、可独立测试：

```
┌─ utils/decision_tape.py ──────────────┐   决策点全量 bundle append-only 落盘
│   record_decision(bundle) -> None      │   (accept + reject)，self-contained
└────────────────────────────────────────┘
┌─ 独立 1s tick 采集模块 ────────────────┐   1 秒聚合 bar → klines_1s.db
│   (复用 kline 写入 pattern)            │   故障隔离，有界写
└────────────────────────────────────────┘
┌─ utils/counterfactual_pnl.py ─────────┐   被拒单真金白银净 PnL：
│   resolve(record, price_source)        │   CostModel 成本 + SL/TP 触发判定 +
│   -> CfResult                          │   SL-first 保守 + 偏差带 + source 标注
└────────────────────────────────────────┘
┌─ 诚实性 gate (报表层单一函数) ─────────┐   Wilson + bootstrap + 三档薄样本
│   summarize(results, bucket) -> Verdict│   收口；INSUFFICIENT_SAMPLE 拒答
└────────────────────────────────────────┘
```

## 3. 关键技术决策

### D1（修订）决策磁带 self-contained，内联 parsed LLM 输出
**问题**：`logs/llm_audit_*.jsonl` 只保留 7 天，而磁带要长期累积当原料 → `llm_audit_ref` 90 天后悬空，回放取不到当时 LLM 输出。
**决策**：磁带**内联存 parsed LLM 输出**（`action/confidence/reasoning/key_factors/risk_warnings`，几百字节），不依赖 llm_audit 存活；`llm_audit_ref` 降级为"7 天内可取原始 prompt 的 best-effort 指针"。LLM prompt 本身可从磁带内 `tech_analysis` 重建，不存。
**`decision_replay_record` schema**：
```
schema_version, request_id, timestamp, symbol, decision(accept|reject),
tech_analysis(9 维全量快照), price_at_decision, regime_state,
llm_output_inline{action,confidence,reasoning,key_factors,risk_warnings} | null,
llm_audit_ref(best-effort 指针) | null,
trade_decision_output{ accept: plan+attribution | reject: reject_reason+attribution }
```

### D2 决策磁带 writer：fail-safe 有界写，不污染主链路
- append-only jsonl，原子按行写；路径 `state_paths` 派生，flag `DECISION_TAPE_ENABLED`（默认开）。
- writer 异常**绝不**抛进 Judge 决策路径：try/except 包裹，失败 → 丢弃 + `_drop_count++` + 节流告警。
- 有界：内存队列 + 批量 flush（或同步小写，benchmark 后定）；单条不阻塞决策。
- retention：默认 **90 天** + 总大小滚动封顶（防失控），两者取先到。

### D3 tick 采集：1 秒聚合 bar → 独立 `klines_1s.db`
- 格式 **1s OHLC+vol bar**（非逐 trade）：1s 精度解掉 1m 同根 SL/TP 歧义；比逐 trade 小 ~60×。
- 复用现有 kline schema/writer，写**独立 `klines_1s.db`**（不污染主 klines.db）。
- 独立模块、flag `TICK_CAPTURE_ENABLED`、故障隔离（挂了不拖累 `multi_data_collector` 与决策）、有界批量写。
- retention：1s bar 体积可观，默认保留窗口短于决策磁带（如 30 天，可配）+ 大小封顶。

### D4 反事实 PnL：CostModel + SL/TP 触发判定 + SL-first + 双轨价格
- **成本**：复用 executor 现有 `CostModel`，手续费精确；**资金费用决策时点 `funding_rate` 当持仓期常数近似**，结果标 `funding=approximated`。不重写成本公式。
- **触发判定算法**（被拒单 `record` → 结果）：
  1. 价格源：存续时段有 1s bar → 用 1s（`source=tape_exact` 维度之一）；否则退化 1m K 线。
  2. 逐 bar 扫描 entry 之后到 24h 过期：
     - long：bar.low≤SL 且 bar.high≥TP（同根冲突）→ **SL-first**，标 `price_ambiguous=True`；仅 low≤SL → SL；仅 high≥TP → TP。short 对称。
  3. 到 24h 无触发 → `expired`，按 expiry 时点价计 mark-to-market 净 PnL。
  4. 净 PnL = 价格毛利 − CostModel(手续费 + 近似资金费)，真实 USDT。
- **偏差带**：汇总 `price_ambiguous` 笔数/占比 + "若改 TP-first 上界"的 PnL 区间。
- **source 标注**：每条结果带 `source ∈ {attribution_reconstructed(旧 jsonl), tape_exact(新磁带)}`，不混用、不互相覆盖。

### D5 诚实性 gate：Wilson + bootstrap + 三档，单点收口
- 报表层**单一函数** `summarize(results, bucket_key)`，所有汇总/方向结论收口于此（调用点不重写）。
- **胜率** → Wilson score 区间；**净 PnL** → bootstrap 重采样区间（暴露单笔主导脆弱）。
- 三档：`n<30` → `INSUFFICIENT_SAMPLE`（拒答，不给方向）；`30≤n<100` → 给区间 + `low_confidence`；`n≥100 且 bootstrap CI 不跨 0` → `actionable`。阈值 `CF_MIN_SAMPLE/CF_LOWCONF_SAMPLE` 可配。

## 4. 红线守卫

observability-only write-only：任何 gate/veto/halt/rank/daily-stop **严禁**读磁带/反事实 PnL/tick。守卫测试仿 `test_paper_dual_track.py::test_reviewer_does_not_consume_idealized`：静态扫描 + 行为断言，确认决策路径不 import/不读这三类产物。CLAUDE.md 风控红线补一条声明（同 provenance / agent-health 性质）。

## 5. 数据流

```
Judge 决策点(accept/reject)
   └─> decision_tape.record_decision(bundle)  ──> decision_replay_tape.jsonl  [原料,长存]
1s tick 采集(独立) ─────────────────────────> klines_1s.db                   [价格精度]
被拒单(旧 rejected_signal_events.jsonl / 新磁带 reject 条)
   └─> counterfactual_pnl.resolve(record, price_source=1s|1m)
        └─> CfResult{net_usdt, outcome, price_ambiguous, funding_approx, source}
             └─> 诚实性 gate summarize(bucket) ──> 报表(replay_report 扩展)
```

## 6. 测试策略

- **decision_tape**：accept 落带 / reject 落带 / writer 异常不污染决策 / flag 关停零文件 / namespace 路径 / llm_output_inline 自包含（llm_audit 不存在也能回放）/ retention 滚动。
- **tick 采集**：1s bar 落 klines_1s.db / 批量不阻塞 / flag 关停无残留 / 故障隔离不拖累 collector。
- **counterfactual_pnl**：净值扣成本 / 单边 SL / 单边 TP / 同根 SL-first + price_ambiguous / expired mark-to-market / funding=approximated 标注 / source 标注 / 旧 jsonl 重算 / 1s 优先 1m 退化。
- **诚实性 gate**：n<30 拒答 / 30–100 low_confidence / ≥100 跨 0 不 actionable / ≥100 不跨 0 actionable / Wilson 边界(0%、100%) / bootstrap 单笔主导被 CI 暴露 / 单点收口（无第二份判定）。
- **红线守卫**：决策路径不消费三类产物。
- **零回归**：flag 全关 == 现状（无新文件、决策不变）；全量 `pytest -q` 不低于 1149。

## 7. 风险 / 取舍

| 风险 | 缓解 |
|---|---|
| 1m 历史段 SL/TP 先后不可判 | SL-first 保守下界 + 偏差带；1s 采集令未来段精确 |
| 磁带/tick/klines_1s 无界增长 | retention(90/30 天) + 大小封顶 |
| writer 拖累决策 | fail-safe 有界写，异常丢弃不抛 |
| 资金费近似失真 | 标 `funding=approximated`，诚实不假装精度；精确化留后续 |
| 旧 attribution 重算与未来 tape_exact 不一致 | source 标签强制区分，不混用 |
| 红线误用（未来有人拿来做 gate） | 守卫测试 + CLAUDE.md 显式声明 |

## 8. Spec Patch（回写 OpenSpec delta spec）

深度设计新增/细化的验收场景已回写到 delta spec（不在此另起需求）：
- `decision-replay-tape`：llm_output 内联自包含（替代纯 ref）、retention 滚动。
- `counterfactual-pnl`：诚实 gate 三档 + Wilson/bootstrap、funding=approximated 标注。
- `tick-snapshot-capture`：1s 聚合 bar → klines_1s.db、缺 1s 退化 1m。
