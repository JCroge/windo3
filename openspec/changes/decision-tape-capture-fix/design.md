## Context

反事实策略实验室（L1–L4）已建成并归档，但用真实磁带（909 条）兑现价值时发现：**L2 终验 fidelity=1.0 虚高、L4 扫描全程 delta=0 空转**。根因不在实验室机器，而在上游决策磁带捕获——`judge.py` 两个录制 chokepoint 把 `tech_analysis` 与 `llm_output` 写死为空：

```
agents/trading/judge.py
├─ _make_decision(symbol, tech)            tech 入参; llm_result 在 1218 算出
│   ├─ 直接 reject 点 ×~9 → _record_rejected_plan(...)   tech+llm_result 在 scope
│   │     └─ build_bundle(tech_analysis={}, llm_output=None)   ← 写死空 ❌
│   └─ _gate_and_publish_open(symbol, decision, state)   只有 decision+state
│         ├─ 内部 reject 点 (main_slot_full) → _record_rejected_plan(...)  无 tech/llm ❌
│         └─ accept 录制点 1979 → build_bundle(tech=_symbol_tech_cache.get, llm_output=None) ❌
└─ _flush_ranked_candidates → _gate_and_publish_open (2033)   另一函数,无 llm_result
```

关键约束：`judge.py` 是决策红线文件，CLAUDE.md 多条单点收口红线 + "磁带 observability-only write-only"。本 change 只改"录什么"，绝不改"怎么决策"。

## Goals / Non-Goals

**Goals**
- reject + accept 两路 chokepoint 都捕获真实 `tech_analysis` + `llm_output_inline`。
- `replayable` 收紧为真实性守卫；旧空记录自动排除。
- 同构测试证明新磁带可被 `replay_decision` 走到 gate 并复现拒因 / perturb 后翻转。

**Non-Goals**
- 不改任何决策逻辑、gate 阈值、plan、ranking。
- 不回填旧 909 条空记录。
- 不动 `klines_1s.db` prune（独立待办）。

## Decisions

### D1: 用 `_symbol_llm_cache` 镜像现有 `_symbol_tech_cache`，而非穿透参数
chokepoint 分两类：直接 reject 点（tech+llm 在 scope）vs `_gate_and_publish_open` 内部点 + ranked-flush（只有 decision/state）。统一解法是引入实例级 `self._symbol_llm_cache`，与 `self._symbol_tech_cache`（已存在、accept 路径已用）完全对称：两个 chokepoint 都 `cache.get(symbol)` 取值。

- **替代方案 A（穿透形参）**：给 `_record_rejected_plan` / `_gate_and_publish_open` 加 `tech`/`llm_result` 形参，10+ 调用点逐一传。改动面大、触碰更多决策红线行、ranked-flush 还需把 state 持久化到候选——更脆。
- **选 cache 方案理由**：最小红线触碰、复用既有且已验证的模式（tech cache）、两类 chokepoint 一致处理。

### D2: per-decision reset 保证 LLM cache 诚实
`_make_decision` 起点把该 symbol 的 llm cache 置 None，`_ask_llm` 之后写入真实 `llm_result`。rule-only open 路径（LLM 之前的 802/921/1042）因此取到 None，诚实反映"无 LLM 参与"，绝不串到上一次决策的 stale LLM。symbol 退出清理点（378 处 tech cache pop）同步 pop llm cache，避免泄漏。

### D3: `replayable` = 有快照 AND tech 非空 + schema v1→v2
`build_bundle` 把 `replayable` 收紧。旧 909 条空记录 `tech=[]` → `replayable=false`，回放 / �flip / sweep 端自然跳过，不再被"无信号→hold"兜底误判为忠实复现。`SCHEMA_VERSION` v2 让读取端可区分自包含记录与历史空 v1。

### D4: 内联 parsed LLM 输出（非原始 prompt），契约已背书
磁带存的是已 parse 的 `llm_result`（action/confidence/reasoning/key_factors/risk_warnings），CLAUDE.md 红线明确要求"内联存 parsed LLM 输出（self-contained）"。比待办 OPEN 项「llm_audit 脱敏」（针对原始 prompt 日志）敏感度低。不在本 change 引入脱敏开关。

## Risks / Trade-offs

- **[磁带体积增长]** 每条 +1~3KB（现 ~6.5KB/条、~6MB/天，大头是 state_snapshot）→ 90 天 retention prune 已存在（`decision-replay-tape` 既有要求），不无界增长。监控即可。
- **[cache 串扰]** 若多 symbol 决策交错可能取错 llm → 决策按 symbol 串行、且 per-decision reset + 按 symbol key 取值；与既有 tech cache 同等安全。同构测试覆盖。
- **[误改决策逻辑]** 红线最高风险 → 严格只动录制调用点 + cache 读写；`test_cf_red_line_guard.py` 不回归；新增同构测试断言决策 action/gate 标签不因捕获改动而变。
- **[schema bump 破坏旧读取]** → 读取端按 `replayable` 过滤而非 schema_version 硬断；v2 仅作信息标记，旧 v1 仍可被解析（只是 replayable=false）。

## Migration Plan

1. 改 `decision_tape.build_bundle`（replayable 守卫 + schema v2）+ `judge.py`（cache + 两个调用点）。
2. 全量 pytest + 红线守卫 + 新同构测试通过。
3. 合并入 main，OS 层重启 live（per `feedback_runtime_restart_semantics`：/restart 同进程不重 import，新捕获代码必须 OS 重启生效）。
4. 等新磁带累积 ~1-2 天（~900 条/天），重跑 `cf_direction_recommendation.py` 验证 L2 fidelity 真实化、L4 能产出推荐或诚实拒答。
- **回滚**：feature flag `decision_tape_enabled=false` 即停写；或 revert 本 change，决策行为零变化。

## Open Questions

- 无阻塞性未决项。schema_version v2 的读取端兼容已由"按 replayable 过滤"覆盖。
