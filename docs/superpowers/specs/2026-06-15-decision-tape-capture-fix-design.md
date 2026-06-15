---
comet_change: decision-tape-capture-fix
role: technical-design
canonical_spec: openspec
---

# Decision Tape Capture Fix — Technical Design

> 上游事实源：`openspec/changes/decision-tape-capture-fix/`（proposal / specs / tasks）。本文只做 HOW，不重定义需求。

## 问题根因（已实证）

反事实实验室 L1–L4 用真实磁带（909 条）兑现价值时：L2 终验 `baseline_fidelity=1.0` 虚高、L4 旋钮扫描全程 `div=0/cf_open=0/delta=0` 空转。根因在上游捕获——`judge.py` 两个录制 chokepoint 把决定性输入写死为空：

- reject 路径 `_record_rejected_plan`→`build_bundle(tech_analysis={}, llm_output=None)`（judge.py:3032/3035）。
- accept 路径 `_gate_and_publish_open`→`build_bundle(..., llm_output=None)`（judge.py:1986，tech 已靠 `_symbol_tech_cache` 捕获）。

全部 909 条 `tech_analysis=[]` / `llm_output_inline=null` → `replay_decision` 拿空 tech + 空 llm → `_make_decision` 在"无信号→hold"短路，永远走不到任何 gate。实证：一条录下 `rr_below_floor:1.20<1.50` 的 NEAR 单，回放（floor 1.5 与 1.1 两次）都返回 `action=hold, reject_reason=None`，从未触及 R:R 闸。

## chokepoint 拓扑（为何不能一刀切）

```
_make_decision(symbol, tech)            tech 入参; llm_result 在 ~1218 算出
├─ 直接 reject 点 ×~9 (rr/quality/ev…)  tech + llm_result 在 scope ✓
├─ rule-only open (802/921/1042)        在 llm 之前, llm 合法缺席
├─ 即时 accept → _gate_and_publish_open(symbol, decision, state)   只有 decision+state
│     ├─ 内部 reject (main_slot_full)   无 tech/llm_result
│     └─ accept 录制点 1979
├─ ranked 入队 (1816) rank_candidate['decision']=decision; return   ← 延迟!
└─ _flush_ranked_candidates (2033, 另一函数, 延迟执行)
      ├─ accept → _gate_and_publish_open
      └─ reject (ranking_slot_full) → _record_rejected_plan
```

chokepoint 分两类：直接路径（tech+llm 在 scope）vs `_gate_and_publish_open` 内部点 + 延迟 ranked 路径（只有 decision/state，且延迟执行可能与新决策交错）。

## 方案

### D1 — `_symbol_llm_cache` 镜像 `_symbol_tech_cache`（直接路径，覆盖全部阻断单）

实例级 `self._symbol_llm_cache = {}`，与既有 `self._symbol_tech_cache`（accept 路径已用）对称：

- `_make_decision` 顶部：`self._symbol_llm_cache[symbol] = None`（per-decision reset）。
- `_ask_llm`（~1218）之后：`self._symbol_llm_cache[symbol] = llm_result`。
- symbol 退出清理（~378，tech cache pop 处）：`self._symbol_llm_cache.pop(s, None)`。
- 两个 chokepoint 统一读 cache：`tech_analysis=getattr(self,"_symbol_tech_tape_cache",{}).get(symbol) or {}`、`llm_output=getattr(self,"_symbol_llm_cache",{}).get(symbol)`。

reset、set、read 都发生在**同一次** `_make_decision` 内 → 覆盖所有直接 reject（rr_below_floor / quality_gate / ev_gate = 全部 909 阻断单）+ 即时 accept + `_gate_and_publish_open` 内部 reject。rule-only open 路径因 per-decision reset 取到 None，**诚实**反映"无 LLM 参与"，绝不串上一次 stale LLM。

**D1.1 — tech 必须用专属侧信道 `_symbol_tech_tape_cache`（observability-only 不变量修正，最终审查发现）**：现有 `_symbol_tech_cache` **不是纯侧信道**——它被 live 决策读取（`_regime_manager.update`、`is_probe_short_eligible`、probe-short 流动性 gate）。若 tape/flush 代码写它，会把磁带捕获的（可能 stale 的）tech 灌进 live 决策输入，违反 observability-only。故新增专属 `self._symbol_tech_tape_cache`（镜像 `_symbol_llm_cache`）：`_make_decision` 顶部 `[symbol]=tech` 捕获决策时点 tech、symbol 退出 pop、两个 chokepoint 读它、flush re-prime 写它。`_symbol_tech_cache` 自此**只**由 live 消息处理器写（`= msg['payload']`），tape 代码绝不写。守卫测试 `test_flush_does_not_mutate_live_tech_cache` 锁定 flush 不写 live cache。

**为何选 cache 而非穿透形参**：替代方案是给 `_record_rejected_plan` / `_gate_and_publish_open` 加 `tech`/`llm` 形参并穿透 10+ 调用点——改动面大、触碰更多决策红线行、ranked 路径还需额外持久化。cache 方案 chokepoint 零分叉、不改决策函数签名、复用已验证模式。

### D2 — 延迟 ranked 路径 re-prime（保真补丁）

ranked 候选在 `_make_decision` 入队、在 `_flush_ranked_candidates` 延迟派发。若期间同 symbol 又跑一次 `_make_decision`，cache 被 reset → flush 读到串味 llm。补丁：

- 入队（~1816）：`rank_candidate['llm_output'] = llm_result`、`rank_candidate['tech'] = tech`（此刻在 scope）。
- `_flush_ranked_candidates` 派发每个候选前 re-prime **tape 侧信道**：`self._symbol_llm_cache[symbol] = candidate.get('llm_output')`、`self._symbol_tech_tape_cache[symbol] = candidate.get('tech')`（**绝不写 live `_symbol_tech_cache`**，见 D1.1）。

chokepoint 仍只读 cache、逻辑不分叉；flush 逐候选串行 re-prime，无串味。`_candidate_ranker.enabled=false`（当前默认）时此路不走，补丁是开启时的保真冗余。

### D3 — `replayable` 真实性守卫 + schema v2

`utils/decision_tape.py::build_bundle`：`replayable = (state_snapshot is not None) and bool(tech_analysis)`。旧 909 条 `tech=[]` 自动 `replayable=false`，回放/flip/sweep 按既有 `replayable` 过滤天然排除。`SCHEMA_VERSION` v1→v2 标记自包含。读取端按 `replayable` 过滤，**不**按 `schema_version` 硬断（v2 仅信息标记，旧 v1 仍可解析）。

## 测试策略（端到端 record→replay 是灵魂）

`tests/test_decision_tape_capture.py`：

1. **record→replay 闭环**：构造能产出 `rr_below_floor` 拒单的 MultiJudge（沿用 `test_short_main_path_risk_guard.py` 构造模式），磁带写 tmp。跑真实 `_make_decision` → 读回 bundle → `replay_decision(bundle, baseline_config)` → 断言复现 `reject` 且拒因含 `rr_below_floor`（非短路 `reject_reason=None`）。
2. **perturb 翻转**：同 bundle，`replay_decision(bundle, {'rr_floor_default': <低于其 R:R>})` → 断言翻转为 `open_long/open_short`。直击 L4 空转根因。
3. **replayable 守卫**：tech 非空+有快照→true；tech 空 或 缺快照→false。
4. **决策不变性（红线）**：磁带捕获开/关，`_make_decision` 产出的 action / gate 标签 / plan 一致——证明只改"录什么"。
5. **红线守卫不回归**：`tests/test_cf_red_line_guard.py` 全过（新增 cache 是写侧，合规）。

场景 1/2 回写为 `decision-replay-tape` delta spec 的验收 Scenario（"捕获使回放可达 gate"）。

## 风险 / 取舍

- **[ranked 串味]** → re-prime 补丁（D2）。
- **[磁带体积 +1~3KB/条]** → 既有 90 天 prune 兜底，监控即可。
- **[误改决策逻辑 — 红线最高风险]** → 严格只动 cache 读写 + 两个 build_bundle 调用点 + decision_tape；测试 4 断言决策不变性；红线守卫不回归。
- **[schema bump 破旧读取]** → 读取端按 replayable 过滤而非 schema_version 硬断。

## 迁移 / 生效

1. 改 `decision_tape.py` + `judge.py`，全量 pytest + 红线守卫 + 新同构测试通过。
2. 合并 main → **OS 层重启 live**（per `feedback_runtime_restart_semantics`，/restart 同进程不重 import）。
3. 等新磁带累积 ~1-2 天（~900 条/天）→ 重跑 `cf_direction_recommendation.py` 验证 L2 真实化 + L4 产出推荐或诚实拒答。
4. **回滚**：`decision_tape_enabled=false` 即停写，或 revert，决策行为零变化。

## 已知边界

- 修复只影响新磁带；旧 909 条永久 `replayable=false` 不可回放。
- observability-only write-only，零决策路径变化，不影响 live 交易。
