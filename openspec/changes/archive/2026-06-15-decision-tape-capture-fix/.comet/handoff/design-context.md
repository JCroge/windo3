# Comet Design Handoff

- Change: decision-tape-capture-fix
- Phase: design
- Mode: compact
- Context hash: c31f764e02c1def24dade491283724b02b5091abb5e0f82cc6e622be5afbe9d3

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/decision-tape-capture-fix/proposal.md

- Source: openspec/changes/decision-tape-capture-fix/proposal.md
- Lines: 1-32
- SHA256: fd9605323f08c97added55a80b120cd55ceb28a540d846db3171cc9d2780f5e6

```md
## Why

决策磁带的两个录制 chokepoint 把决定性输入**写死为空**，使全部 909 条已落盘记录 `tech_analysis=[]` / `llm_output_inline=null`——直接违反 `decision-replay-tape` 既有契约（spec 要求"tech_analysis 9 维全量快照"+"内联存储 parsed LLM 输出"）。后果：确定性回放 harness `replay_decision` 拿到空 tech + 空 LLM，`_make_decision` 在"无信号→hold"处短路，永远走不到任何 gate；导致 L2 终验 `baseline_fidelity=1.0` 虚高（仅 reject 大类碰巧匹配，未验证 gate 路径），L4 旋钮扫描（`rr_floor_default` 1.50→1.20、`min_confidence` 60→40）全程 `div=0/cf_open=0/delta=0` 空转。**这是反事实策略实验室 L2/L3/L4 无法产出任何方向推荐的根因。**

## What Changes

- **修复 reject 路径捕获**：`agents/trading/judge.py` 的 `_record_rejected_plan`→`build_bundle` 不再传 `tech_analysis={}` / `llm_output=None`，改为捕获真实决策输入。
- **修复 accept 路径捕获**：`_gate_and_publish_open` 处 `build_bundle` 不再传 `llm_output=None`，补齐 LLM 输出。
- **引入 `_symbol_llm_cache`**：镜像现有 `self._symbol_tech_cache` 模式，在 `_make_decision` 起点 reset、`_ask_llm` 之后写入、symbol 退出时 pop。两个 chokepoint 统一从 cache 按 symbol 取，**无需给 `_record_rejected_plan` / `_gate_and_publish_open` 加形参或穿透 10+ 调用点**。rule-only open 路径（LLM 之前）因 per-decision reset 取到 None，诚实反映"无 LLM 参与"。
- **`replayable` 真实性守卫**：`utils/decision_tape.py::build_bundle` 把 `replayable` 收紧为 `state_snapshot is not None AND bool(tech_analysis)`；旧 909 条空记录自然标 `replayable=false`，回放/报表自动跳过。`SCHEMA_VERSION` v1→v2 标记记录真正 self-contained。
- **旧数据不动**：保留 `data/decision_replay_tape.jsonl` 历史，不删不清。

非目标（Non-goals）：
- **绝不**改任何决策逻辑、gate 阈值、plan 计算、ranking——只改"录什么"，不改"怎么决策"。
- 不回填旧 909 条空记录（输入已不可追溯，永久不可回放）。
- 不改 `klines_1s.db` prune（独立的待办项，不在本 change 范围）。

## Capabilities

### New Capabilities
（无）

### Modified Capabilities
- `decision-replay-tape`: 强化捕获契约——`tech_analysis` 与 `llm_output_inline` 必须反映真实决策输入（现有 accept/reject 落盘场景被空数据 vacuously 满足）；新增 `replayable` 标志真实性约束（仅当输入完整才标可回放）。

## Impact

- **代码**：`agents/trading/judge.py`（新增 `_symbol_llm_cache` + reset/set/pop + 两个 `build_bundle` 调用点改为 cache 取值）、`utils/decision_tape.py`（`replayable` 守卫 + schema bump）。
- **测试**：新增同构测试（构造带 tech+llm 的新 bundle，验证 `replay_decision` 能走到 gate：`rr_below_floor` 记录回放复现拒因，且 perturb `rr_floor_default` 后翻转 accept）；`tests/test_cf_red_line_guard.py` 不回归。
- **数据契约**：磁带新记录 schema v2（含真实 tech + llm）；旧 v1 空记录标 `replayable=false`。
- **运行影响**：observability-only write-only，**零决策路径变化**，不影响 live 交易。磁带每条约 +1~3KB（现 ~6.5KB/条、~6MB/天），90 天 prune 已存在。
- **时序**：修复只影响新磁带；需等新磁带累积（~900 条/天，1-2 天）才能重跑 L2 终验 + L4 方向推荐。
```

## openspec/changes/decision-tape-capture-fix/design.md

- Source: openspec/changes/decision-tape-capture-fix/design.md
- Lines: 1-64
- SHA256: 65f97f641791b3b023a9c53ea361ab92d8acad078bb01d0bf7244c47c1a5dacd

```md
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
```

## openspec/changes/decision-tape-capture-fix/tasks.md

- Source: openspec/changes/decision-tape-capture-fix/tasks.md
- Lines: 1-30
- SHA256: fcf37be521d4e0d9e187db47d00d9043f23a54964c2c2bd3d2c2e3317e3aa08a

```md
## 1. 测试先行（同构 + 红线）

- [ ] 1.1 新增 `tests/test_decision_tape_capture.py`：构造一条带真实 `tech_analysis` + `llm_output_inline` + state_snapshot 的 bundle，断言 `replay_decision` 能走到 gate 并复现拒因（如 `rr_below_floor`），而非"无信号→hold"短路
- [ ] 1.2 同测试加 perturb 用例：同一记录 perturb `rr_floor_default` 至低于其 R:R 后，`replay_decision` 翻转为 accept（验证捕获使旋钮可生效）
- [ ] 1.3 加 `replayable` 真实性用例：tech 非空+有快照→replayable=true；tech 空 或 缺快照→replayable=false
- [ ] 1.4 确认 `tests/test_cf_red_line_guard.py` 现有断言不回归（决策/风控路径仍不读 CF 产物）

## 2. decision_tape.py — replayable 守卫 + schema

- [ ] 2.1 `build_bundle` 把 `replayable` 收紧为 `state_snapshot is not None and bool(tech_analysis)`
- [ ] 2.2 `SCHEMA_VERSION` v1→v2，标记自包含记录

## 3. judge.py — LLM cache 捕获（核心，绝不动决策逻辑）

- [ ] 3.1 `__init__` 新增 `self._symbol_llm_cache = {}`（紧邻 `self._symbol_tech_cache`）
- [ ] 3.2 `_make_decision` 起点把该 symbol 的 `_symbol_llm_cache` 置 None（per-decision reset）
- [ ] 3.3 `llm_result = await self._ask_llm(...)`（~1218）之后写入 `self._symbol_llm_cache[symbol] = llm_result`
- [ ] 3.4 symbol 退出清理点（~378，tech cache pop 处）同步 `self._symbol_llm_cache.pop(s, None)`
- [ ] 3.5 accept 录制点（~1979）：`llm_output=self._symbol_llm_cache.get(symbol)`（tech 维持 `_symbol_tech_cache.get(symbol) or {}`）
- [ ] 3.6 reject 录制点（`_record_rejected_plan`，~3028）：`tech_analysis=self._symbol_tech_cache.get(symbol) or {}`、`llm_output=self._symbol_llm_cache.get(symbol)`

## 4. 验证与回归

- [ ] 4.1 跑 `python3 -m pytest -q`，确认基线在 1223 之上 +新测试，无回归
- [ ] 4.2 静态确认无决策路径行为变化：diff 仅触碰 cache 读写 + 两个 build_bundle 调用点 + decision_tape；gate/plan/ranking 逻辑零改动
- [ ] 4.3 编译检查 `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q agents/trading/judge.py utils/decision_tape.py`

## 5. 收尾说明（非代码）

- [ ] 5.1 在 verify 报告记录：修复只影响新磁带，旧 909 条永久 replayable=false；需等新磁带累积 1-2 天后重跑 `cf_direction_recommendation.py` 验证 L2 真实化 + L4 推荐
```

## openspec/changes/decision-tape-capture-fix/specs/decision-replay-tape/spec.md

- Source: openspec/changes/decision-tape-capture-fix/specs/decision-replay-tape/spec.md
- Lines: 1-52
- SHA256: 4781bc2919370b3dcd64843d93319b1c3086feb5eb88286d99519eb247688a8b

```md
## MODIFIED Requirements

### Requirement: 决策点磁带落盘
系统 SHALL 在 Judge 每次开仓决策点（包括 accept 与 reject）原子追加一条 `decision_replay_record` 到独立磁带文件，捕获足以未来忠实回放的完整输入与输出 bundle。`tech_analysis` 与 `llm_output_inline` 字段 SHALL 反映该决策**实际使用的输入**——禁止以空字典 / null 占位写入；当决策实际有 tech 信号或 LLM 参与时，对应字段 MUST 非空。

#### Scenario: 开仓 accept 落磁带
- **WHEN** Judge 发布 `trade_decision.v2` 且 action 为 open_long/open_short
- **THEN** 磁带追加一条记录，含 `request_id`、`timestamp`、`symbol`、`decision="accept"`、`tech_analysis` 9 维全量快照（取自该 symbol 决策时的真实 tech，非空占位）、`price_at_decision`、`regime_state`、`llm_output_inline`（LLM 参与时为真实 parsed 输出）、`llm_audit_ref`、`trade_decision_output`（plan + attribution）

#### Scenario: 拒单也落磁带
- **WHEN** Judge 拒绝一个开仓计划（任一 gate 拦截）
- **THEN** 磁带追加一条记录，`decision="reject"`，含同样的真实输入 bundle（`tech_analysis` 取自该 symbol 决策时的真实 tech，`llm_output_inline` 取自该决策的真实 parsed LLM 输出）加 `reject_reason` 与拒单 attribution

#### Scenario: 捕获使回放复现拒因
- **WHEN** 一条因 `rr_below_floor` / `quality_gate` / `ev_gate` 等 gate 拒单的记录被 `replay_decision` 以原 baseline config 回放
- **THEN** 回放 SHALL 凭记录内真实 `tech_analysis` + `llm_output_inline` 走到对应 gate 并复现该拒因（reject 且拒因匹配），而非在"无信号→hold"处提前短路得到 `reject_reason=null`

#### Scenario: 捕获使旋钮扰动可翻转
- **WHEN** 一条因 `rr_below_floor` 拒单的记录被 `replay_decision` 以 perturbed config（`rr_floor_default` 降至低于该记录 R:R）回放
- **THEN** 回放 SHALL 翻转为开仓决策（action 为 open_long/open_short），证明捕获使非 LLM 旋钮在回放中确实生效

#### Scenario: 原子写不污染主链路
- **WHEN** 磁带 writer 写入失败或抛异常
- **THEN** 异常 SHALL NOT 传播进 Judge 决策路径，记录 fail-safe 丢弃并计数告警，决策正常继续

### Requirement: 磁带 LLM 输出自包含
系统 SHALL 在磁带中内联存储 parsed LLM 输出（action/confidence/reasoning/key_factors/risk_warnings），使磁带自包含、不依赖 `logs/llm_audit_*.jsonl` 存活；`llm_audit_ref` 作为 7 天内可取原始 prompt 的 best-effort 指针。当某决策由 LLM 参与产生时，`llm_output_inline` MUST 为该次调用的真实 parsed 输出，SHALL NOT 写 null 占位。

#### Scenario: 内联输出抗 llm_audit 过期
- **WHEN** 一条 accept/reject 记录由 LLM 参与决策，且其后 llm_audit 文件已过 7 天保留期被清理
- **THEN** 磁带内 `llm_output_inline` SHALL 仍可被回放读取到当时 LLM 输出，无需 llm_audit

#### Scenario: 规则降级无 LLM
- **WHEN** 决策由规则引擎降级产生（LLM 不可用），或开仓走 LLM 之前的 rule-only 路径
- **THEN** `llm_output_inline` SHALL 为 null（诚实反映该决策无 LLM 参与），记录照常落带

## ADDED Requirements

### Requirement: replayable 标志真实性
系统 SHALL 仅在记录捕获了足以回放的完整输入时才标 `replayable=true`；`replayable` MUST 同时要求存在决策前状态快照与非空 `tech_analysis`。回放与报表读取端 SHALL 跳过 `replayable=false` 记录，不对其做"无信号→hold"兜底而误判为忠实复现。

#### Scenario: 输入完整才可回放
- **WHEN** `build_bundle` 构建一条记录，且 `state_snapshot_before_decision` 非 null 且 `tech_analysis` 非空
- **THEN** `replayable` SHALL 为 true

#### Scenario: 缺输入标不可回放
- **WHEN** 一条记录缺状态快照，或 `tech_analysis` 为空（含历史 v1 空记录）
- **THEN** `replayable` SHALL 为 false，回放 / 扰动 / 扫描端 SHALL 将其排除出统计，不计入复现率或翻转率

#### Scenario: schema 版本标记自包含
- **WHEN** 落盘一条捕获了真实 tech + llm 的新记录
- **THEN** 其 `schema_version` SHALL 标记为新版本（v2），使读取端可区分自包含记录与历史空 v1 记录
```

