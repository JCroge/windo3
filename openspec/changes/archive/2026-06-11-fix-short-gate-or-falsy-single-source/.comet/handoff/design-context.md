# Comet Design Handoff

- Change: fix-short-gate-or-falsy-single-source
- Phase: design
- Mode: compact
- Context hash: ab43687f7e82749166b12f7df91a749c69275a1875d0123f06b7c0ec6eeedf60

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/fix-short-gate-or-falsy-single-source/proposal.md

- Source: openspec/changes/fix-short-gate-or-falsy-single-source/proposal.md
- Lines: 1-32
- SHA256: 09d1d906fa41ec80bfb009c427d19eb5500a4ad64d071b1233398fcf76861e56

```md
## Why

第五次系统性审计（`docs/generated_reports/系统性审计报告_20260610_第五次.md`）确认两条同源的短单 gate 缺陷，均经一手代码核对：

- **P1-02（CONFIRMED 0.97）**：`Judge._classify_short_entry_risk`（`agents/trading/judge.py:2692-2694`）用 `float(a or b or default)` 取关键指标。当 `position_in_24h_range == 0.0`（价格恰在 24h 锅底，做空最危险的"追空底部"场景）时，`0.0` 是 falsy → 被合并成默认 `0.5` → `range_position_too_low` gate 失效，系统在 24h 最低点放行做空。`pre_12h_return_pct`（2693）同模式。
- **P1-03（P1 红线，名实不符）**：`Judge._apply_regime_policy`（`agents/trading/judge.py:2914-2950`）内联了短单结构 gate 的**第二份完整实现**，没有委托给被 CLAUDE.md 红线指定为"单一收口"的 `_classify_short_entry_risk`；且 `position_in_24h_range` 缺失默认值在三处发散（`_classify_short_entry_risk`→0.5、`_apply_regime_policy`→1.0、`_check_entry_position_policy`→0.5）。当前阈值下未触发实际分歧，但属脆性约定：改一处阈值语义忘了另一处即发散。CLAUDE.md 红线明文"不能在 `_apply_regime_policy` 调用点重写 daily_bias/range_pos/pre_move/RSI 判定"，此红线当前不成立。

讽刺点：被红线指定为"单一真相源"的 `_classify_short_entry_risk` 恰恰是唯一带 bug 的实现；`_apply_regime_policy` 用 `.get(k, 1.0)` 反而正确处理 0.0；`event_backtest._check_entry_with_regime`（`event_backtest.py:396-441`）的第三份短单 gate 用 `float(row.get(..., 0.5))` 且 row 永不为 None，也正确处理 0.0。故 live 主路径是唯一偏离回测的点——P1-02 修复让 live 向既有正确的回测对齐。

## What Changes

- **P1-02（核心修复）**：`_classify_short_entry_risk` 的指标提取从 `or`-falsy 合并改为显式 None 哨兵合并——区分"present 的 0.0"与"absent"，present 的 `0.0` 必须原样保留进 gate 判定。覆盖 `position_in_24h_range`、`pre_12h_return_pct`（及同模式 `rsi`）。引入极小的 `_coalesce_float(*vals, default)` helper 作为统一合并入口。
- **P1-03（红线归位）**：`_apply_regime_policy` 短单结构段改为 **delegate 到 `_classify_short_entry_risk`**，删除第二份内联实现，统一缺失默认值；**保留 probe 路由外壳**——当 delegate 返回 `daily_bearish_required` 时由外壳决定 `probe_short` 路由或拒单，其它结构性 reason 直接透传拒单。`_apply_short_gate_attribution` 四字段（`short_gate_version/short_gate_decision/short_gate_reason/llm_short_reversal_risk`）在 accept/reject 两路径继续写入。
- **范围内的兄弟 `or`-falsy 点**：`_check_entry_position_policy`（`judge.py:2761`，long overheat gate，真实 gate）一并改用 `_coalesce_float`，消除同类 latent bug 并统一默认值；纯 attribution 写点（`judge.py:2359`）作为 cosmetic 一并改用同 helper 保持一致。
- **测试**：新增 `range_position_24h=0.0` 短单回归（锅底必须 `range_position_too_low` 拒单）；`_apply_regime_policy` delegate 后与 `_classify_short_entry_risk` 同结果的 parity 用例；probe 路由外壳在 delegate 后仍生效的用例；既有 `tests/test_short_main_path_risk_guard.py` 14 case 必须保持全绿。
- **同构核对**：`event_backtest.py` 短单 gate 已正确处理 0.0 且为单份实现，P1-02 是让 live 对齐回测、P1-03 是 live 侧两份合一——回测无需改动，在 tasks/design 记录此结论以满足 CLAUDE.md 红线。

## Capabilities

### New Capabilities
（无）

### Modified Capabilities
- `short-main-path-risk-guard`：强化 "Route-Consistent Short Risk Gate" 需求——(a) 短单 gate 必须把 `position_in_24h_range=0.0`（真实 24h 锅底）当作 present 的 0.0 评估，不得合并为中性默认；(b) `_apply_regime_policy` 必须委托 `_classify_short_entry_risk` 作为唯一短单结构 gate 实现，缺失指标默认值必须跨所有调用方一致，禁止第二份内联实现。

## Impact

- **代码**：
  - `agents/trading/judge.py`：新增 `_coalesce_float` helper；`_classify_short_entry_risk`（2692-2694）改哨兵合并；`_apply_regime_policy`（2914-2950）短单段改 delegate + 保留 probe 外壳；`_check_entry_position_policy`（2761）与 attribution 写点（2359）改用 helper。
- **测试**：扩展 `tests/test_short_main_path_risk_guard.py`（range_pos=0.0 回归 + delegate parity + probe 外壳）。
- **不影响**：`RSI <= 30` 三处硬阈值（`judge.py:853/978/1404`）独立保留不动；probe 路由语义不变；LLM 反转风险只收紧不单独 veto；`event_backtest.py` 决策路径（已正确，单份实现）。
- **风险红线**：修改 Judge 决策路径，必须保持短单 gate 单点收口红线（本 change 正是让该红线名实相符）、`_apply_short_gate_attribution` 四字段不回归；基线当前 `1066 passed`，变更后须全绿。
```

## openspec/changes/fix-short-gate-or-falsy-single-source/design.md

- Source: openspec/changes/fix-short-gate-or-falsy-single-source/design.md
- Lines: 1-113
- SHA256: e724e723545c948ec53a3997a4e6a8c99e829fe35d8a8005ef7f3b526e40f5d5

[TRUNCATED]

```md
## Context

第五次审计 P1-02 + P1-03，同源于短单结构 gate。一手代码核对（`agents/trading/judge.py`）：

- `_classify_short_entry_risk`（2620-2727）是 CLAUDE.md 红线指定的"单一收口"，但 2692-2694 用 `float(a or b or default)`，`0.0` falsy 退化。
- `_apply_regime_policy`（2853 起，短单段 2904-2950）是短单结构 gate 的第二份内联实现，含 `probe_short` 路由外壳，`range_pos` 默认 `1.0`（与 canonical 的 `0.5` 发散）。
- `event_backtest._check_entry_with_regime`（396-441）第三份 gate，`float(row.get(..., 0.5))`，row 的 `position_in_24h_range` 在 166-167 已 `fillna(0.5)` 永不为 None，故 0.0 正确保留——回测一直正确。

结论：live 主路径 `_classify_short_entry_risk` 是唯一偏离回测的点。

## Goals / Non-Goals

**Goals**
- P1-02：present 的 `range_pos=0.0` 必须原样进 gate → 锅底正确 `range_position_too_low` 拒单。
- P1-03：`_apply_regime_policy` 短单段委托 `_classify_short_entry_risk`，消除第二份实现 + 统一默认值，让 CLAUDE.md 红线名实相符。

**Non-Goals**
- 不动 `RSI <= 30` 三处硬阈值（853/978/1404）。
- 不改 probe 路由语义、不改 LLM 反转风险归因语义。
- 不改 `event_backtest.py` 决策路径（已正确，单份）。
- 不重构 deferred 三路径（15m/pullback/chase）——它们已委托 `_classify_short_entry_risk`（792/911/1032/1529），随 canonical 修复自动受益。

## Decisions

### D1：`_coalesce_float` 哨兵合并 helper（P1-02）

新增模块级或类内 helper：

```python
def _coalesce_float(*vals, default: float) -> float:
    for v in vals:
        if v is not None:
            return float(v)
    return float(default)
```

`_classify_short_entry_risk` 2692-2694 改：

```python
range_pos = self._coalesce_float(
    short_ctx.get('position_in_24h_range'),
    entry_ctx.get('position_in_24h_range'),
    default=0.5,
)
pre_move = self._coalesce_float(
    short_ctx.get('pre_12h_return_pct'),
    entry_ctx.get('pre_12h_return_pct'),
    default=0.0,
)
rsi_val = self._coalesce_float(
    indicators.get('rsi'), momentum.get('rsi'), default=50.0,
)
```

语义差异：旧 `0.0 or 0.5 == 0.5`；新 present `0.0` → `0.0`。对非零值零行为变化。

同 helper 复用到 `_check_entry_position_policy:2761`（long overheat gate，真实 gate，同 bug 类）与 attribution 写点 `judge.py:2359`（cosmetic）。

### D2：`_apply_regime_policy` 委托 + 保留 probe 外壳（P1-03）

两函数对 `daily_bias != 'bearish'` 行为不同：canonical 立即 reject `daily_bearish_required`；regime 先试 probe 再决定。delegate 必须保留外壳：

```python
# _apply_regime_policy 短单结构段（替换 2904-2950 内联实现）
short_gate = self._classify_short_entry_risk(symbol, action, plan, tech, score, llm_result)
if not short_gate['allowed']:
    reason = short_gate['reason']
    if reason == 'daily_bearish_required':
        confirm_15m = entry_timing.get('tf_15m_confirm_short', False)
        rr_val = plan.get('effective_risk_reward_ratio', plan.get('risk_reward_ratio', 0))
        probe_ok, _ = self._can_route_probe_short(symbol, score, confirm_15m, rr_val)
        if probe_ok:
            self._route_to_probe(plan, symbol)
            # 不 reject，落到下游 RR floor（与现状一致：probe 路由后跳过结构 gate）
        else:
            self._record_rejected_plan(symbol, action, plan, score, 60, 'daily_bearish_required')
            return 'daily_bearish_required'
    else:
        self._record_rejected_plan(symbol, action, plan, score, 60, reason)
        return reason
```

Full source: openspec/changes/fix-short-gate-or-falsy-single-source/design.md

## openspec/changes/fix-short-gate-or-falsy-single-source/tasks.md

- Source: openspec/changes/fix-short-gate-or-falsy-single-source/tasks.md
- Lines: 1-30
- SHA256: 7d6adf87388d448318b44fba5a9ceb6b7ea515764136a34eba4c05ea36511c6c

```md
# Tasks: fix-short-gate-or-falsy-single-source

## P1-02：`or`-falsy → 哨兵合并
- [ ] 新增 `_coalesce_float(*vals, default)` helper（区分 present 0.0 与 absent None）
- [ ] `_classify_short_entry_risk`（judge.py:2692-2694）`range_pos/pre_move/rsi_val` 改用 `_coalesce_float`
- [ ] `_check_entry_position_policy`（judge.py:2761）long overheat range_pos 改用 `_coalesce_float`（同 bug 类，真实 gate）
- [ ] attribution 写点（judge.py:2359 entry_range_pos_24h / entry_pre_12h_return_pct）改用 `_coalesce_float`（cosmetic 一致性）

## P1-03：`_apply_regime_policy` delegate + 保留 probe 外壳
- [ ] `_apply_regime_policy` 短单结构段（judge.py:2904-2950）改 delegate 到 `_classify_short_entry_risk`，删第二份内联实现
- [ ] 保留 `daily_bearish_required` 的 probe 路由外壳（probe_ok → `_route_to_probe` 不拒；否则拒单）
- [ ] 其它结构 reason（range/pre_move/rsi/score/htf）直接透传拒单
- [ ] `llm_result` 传入 delegate；`_apply_short_gate_attribution` 四字段在 accept/reject 两路径不回归

## 测试（tests/test_short_main_path_risk_guard.py）
- [ ] present `range_position_24h=0.0` + bearish + 非 probe → `range_position_too_low`（P1-02 核心回归）
- [ ] absent range metric → canonical 与 regime 用同一默认（默认值一致性）
- [ ] delegate parity：`_apply_regime_policy` 与直接 `_classify_short_entry_risk` 拒单 reason 一致
- [ ] probe 外壳：bullish daily + probe 条件满足 → delegate 后仍 `_route_to_probe`（不拒）
- [ ] attribution：delegate 后 reject/accept 仍含 `short_gate_version/short_gate_decision/short_gate_reason/llm_short_reversal_risk`
- [ ] 既有 14 case 保持全绿

## 同构与回归（CLAUDE.md 红线）
- [ ] 记录 `event_backtest.py` 短单 gate（396-441）已用 `.get(..., 0.5)` 正确处理 0.0 且单份实现 → P1-02 是 live 向回测对齐、P1-03 是 live 两份合一，回测决策路径无需改动
- [ ] 全量 `python3 -m pytest -q` 须 `1066+ passed`（新增用例后基线上调）
- [ ] `compileall agents utils` 通过

## 收尾
- [ ] 更新 CLAUDE.md "当前事实" + `docs/to-do-list.md` 关闭 P1-02/P1-03（引用第五次审计报告）
- [ ] delta spec 同步至 master（归档阶段）
```

## openspec/changes/fix-short-gate-or-falsy-single-source/specs/short-main-path-risk-guard/spec.md

- Source: openspec/changes/fix-short-gate-or-falsy-single-source/specs/short-main-path-risk-guard/spec.md
- Lines: 1-39
- SHA256: 3e466ec3a5171fcec63924f13a17fe3a12da806f58319fd6ee85ae666601e8b3

```md
## MODIFIED Requirements

### Requirement: Route-Consistent Short Risk Gate

The Judge SHALL evaluate main-path and deferred-path `open_short` candidates with the same side-aware short risk gate before publishing an executable short decision. A candidate that fails the gate SHALL NOT be published as `main_direct` `open_short`.

The short risk gate SHALL treat a present `position_in_24h_range` value of `0.0` (price at the true 24-hour low) as the literal value `0.0`, NOT coalesce it into a neutral default. Metric extraction SHALL distinguish a present zero from an absent value: only an absent (None) metric MAY fall back to its configured default.

The Judge SHALL implement the short structural gate (daily-bias / range-position / pre-move / RSI / score / higher-timeframe-votes) in exactly one function, `_classify_short_entry_risk`. `_apply_regime_policy` SHALL delegate to `_classify_short_entry_risk` rather than re-implement the gate inline, while retaining its `probe_short` routing shell. Missing-metric default values SHALL be identical across all callers of the gate.

#### Scenario: Main path rejects bullish daily short
- **WHEN** a main-path `ma_aligned_short` candidate has `symbol_daily_bias=bullish` and is not eligible for `probe_short`
- **THEN** Judge SHALL publish `hold` instead of `open_short`
- **AND** the rejection reason SHALL include `daily_bearish_required`

#### Scenario: Deferred path matches main path rejection
- **WHEN** the same `open_short` candidate is evaluated through the deferred entry route
- **THEN** Judge SHALL produce the same short gate rejection class as the main path
- **AND** no route SHALL bypass the daily/range/pre-move/score short gate semantics

#### Scenario: Price at 24h low is rejected, not coalesced
- **WHEN** an `open_short` candidate has `position_in_24h_range=0.0` (a present value, price at the 24h bottom), `daily_bias=bearish`, and is not a probe
- **AND** `0.0 < short_live_min_range_pos`
- **THEN** Judge SHALL reject the candidate with reason `range_position_too_low`
- **AND** the gate SHALL NOT substitute a neutral default (e.g. 0.5) for the present `0.0`

#### Scenario: Absent range metric falls back to a single shared default
- **WHEN** an `open_short` candidate has no `position_in_24h_range` in either `short_context` or `entry_context`
- **THEN** the gate SHALL use the same configured default value regardless of whether the candidate is evaluated via `_classify_short_entry_risk` or `_apply_regime_policy`

#### Scenario: Regime policy delegates to the single gate implementation
- **WHEN** `_apply_regime_policy` evaluates an `open_short` candidate's structural risk
- **THEN** it SHALL obtain the gate outcome from `_classify_short_entry_risk`
- **AND** it SHALL NOT contain a second inline evaluation of the daily-bias / range / pre-move / RSI / score / htf conditions
- **AND** when the gate outcome is `daily_bearish_required`, the existing `probe_short` routing shell SHALL still decide between routing to a probe and rejecting

#### Scenario: Short gate attribution preserved after delegation
- **WHEN** `_apply_regime_policy` accepts or rejects an `open_short` candidate through the delegated gate
- **THEN** attribution SHALL still include `short_gate_version`, `short_gate_decision`, `short_gate_reason`, and `llm_short_reversal_risk`
```

