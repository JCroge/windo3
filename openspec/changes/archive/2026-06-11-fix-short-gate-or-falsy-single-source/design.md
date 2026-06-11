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
# allowed → 落到下游 RR floor
```

正确性论证：
- canonical 在 `daily_bias != bearish` 时立即返回 `daily_bearish_required`，从不评估结构 gate；现状 regime 在 probe 成功路由后也跳过结构 gate（结构 gate 在 `elif daily_bias==bearish` 分支）→ 映射一致。
- 其它结构 reason（range/pre_move/rsi/score/htf）仅在 `daily_bias==bearish` 时由 canonical 评估并返回 → 外壳直接透传拒单，与现状 `elif` 分支等价。
- `llm_result` 必须传入 canonical 以保留 `llm_short_reversal_risk` 归因；`_apply_short_gate_attribution` 仍按 short_gate dict 写四字段。

行为变化（即修复目标）：source 从"仅 `short_ctx`，默认 1.0"变为 canonical 的"`short_ctx` OR `entry_ctx`，哨兵默认 0.5"。在 0.45 阈值附近：缺失 key 时旧 1.0/新 0.5 都 ≥0.45 → 无变化；present 0.0 时旧 regime 已正确（0.0<0.45 拒），新同样拒 → 一致。净效果是 canonical 被修好后 regime 也享有正确的 0.0 处理。

### D3：attribution 字段（P1-03 约束）

`_apply_short_gate_attribution` 在 accept/reject 两路径继续写 `short_gate_version/short_gate_decision/short_gate_reason/llm_short_reversal_risk`，delegate 后 reason/decision 取自 short_gate dict，不得丢失。

## Risks / Trade-offs

- **R1 probe 外壳语义错位**：delegate 后若 `daily_bearish_required` 未走 probe 分支会回归。→ 由"delegate 后 probe 路由仍生效"用例坐实。
- **R2 deferred 路径连带影响**：deferred 三路径已直接调 `_classify_short_entry_risk`，canonical 修复后它们行为同步改变（present 0.0 正确拒）——这是期望的 parity，由 range_pos=0.0 回归覆盖。
- **R3 blast radius**：集中在 judge.py 短单/long-overheat gate 取值与 regime 短单段，无执行/风控/订单路径改动。

## Migration / Isomorphism（CLAUDE.md 红线）

`event_backtest.py` 短单 gate（396-441）已用 `.get(..., 0.5)` 正确处理 0.0 且为单份实现。P1-02 让 live 向回测对齐、P1-03 是 live 侧两份合一——**回测决策路径无需改动**。在 tasks 记录此结论满足红线"修改 Judge 公式必须同步事件回测或补同构测试"。

## Test Strategy

`tests/test_short_main_path_risk_guard.py`（短单 gate 既有 home）扩：
- present `range_position_24h=0.0` + bearish daily + 非 probe → `range_position_too_low`（P1-02 核心回归）。
- absent range metric → canonical 与 regime 用同一默认（D2 一致性）。
- delegate parity：构造若干 tech，断言 `_apply_regime_policy` 与直接调 `_classify_short_entry_risk` 的拒单 reason 一致。
- probe 外壳：`daily_bias=bullish` + probe 条件满足 → delegate 后仍 `_route_to_probe`（不拒）。
- attribution：delegate 后 reject/accept 仍含四字段。
- 既有 14 case 保持全绿。
