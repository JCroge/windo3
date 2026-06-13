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
