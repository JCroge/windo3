---
comet_change: fix-short-gate-or-falsy-single-source
role: technical-design
canonical_spec: openspec
---

# 技术设计：短单 gate `or`-falsy 修复 + `_apply_regime_policy` 单点收口归位

> 上游事实源是 OpenSpec 产物（proposal / delta specs）。本文是技术 RFC，描述 HOW。需求口径以 `openspec/changes/fix-short-gate-or-falsy-single-source/specs/` 为准。范围 = P1-02 + P1-03，三处 `or`-falsy 全修，集中在 `agents/trading/judge.py` 单文件。

## 1. 背景与根因（一手代码核对）

第五次审计 P1-02（CONFIRMED 0.97）+ P1-03（P1 红线），同源于短单结构 gate。

短单结构 gate 当前有**三份实现**：

| # | 函数 | 位置 | range_pos 取值 | 0.0 处理 |
|---|------|------|---------------|---------|
| ① | `_classify_short_entry_risk` | judge.py:2620（红线指定"单一收口"） | `float(a or b or 0.5)` | **BUG**：0.0 falsy → 0.5 |
| ② | `_apply_regime_policy` 短单段 | judge.py:2904-2950（第二份内联） | `short_ctx.get(k, 1.0)` | 正确（present 0.0 留 0.0），但默认 1.0 与 ① 发散 |
| ③ | `event_backtest._check_entry_with_regime` | event_backtest.py:396-441 | `float(row.get(k, 0.5))`，row 永不 None（166-167 fillna） | 正确 |

讽刺点：被红线指定为"单一真相源"的 ① 恰是唯一带 bug 的实现；② 反而正确处理 0.0；回测 ③ 也正确。故 **live 主路径 ① 是唯一偏离回测的点**。

故障语义：`position_in_24h_range == 0.0`（价格在 24h 锅底，做空最危险的"追空底部"）经 ① 退化成 0.5 → 0.5 >= `short_live_min_range_pos`(0.45) → `range_position_too_low` gate 失效 → 系统在 24h 最低点放行做空。`pre_12h_return_pct`（2693）同模式。

红线名实不符（P1-03）：CLAUDE.md 明文"不能在 `_apply_regime_policy` 调用点重写 daily_bias/range_pos/pre_move/RSI 判定"，但 ② 正是第二份完整内联实现。

## 2. P1-02 实现：哨兵合并 helper

新增类内 helper（区分 present 0.0 与 absent None）：

```python
def _coalesce_float(self, *vals, default: float) -> float:
    """Return first non-None value as float; only absent (None) falls back to default.
    Unlike `a or b or default`, a present 0.0 is preserved (not treated as falsy)."""
    for v in vals:
        if v is not None:
            return float(v)
    return float(default)
```

应用三处：

- `_classify_short_entry_risk`（2692-2694，P1-02 核心）：
  ```python
  range_pos = self._coalesce_float(
      short_ctx.get('position_in_24h_range'),
      entry_ctx.get('position_in_24h_range'), default=0.5)
  pre_move = self._coalesce_float(
      short_ctx.get('pre_12h_return_pct'),
      entry_ctx.get('pre_12h_return_pct'), default=0.0)
  rsi_val = self._coalesce_float(
      indicators.get('rsi'), momentum.get('rsi'), default=50.0)
  ```
- `_check_entry_position_policy`（2761，long overheat gate，真实 gate，同 bug 类）：`float(ctx.get('position_in_24h_range', 0.5) or 0.5)` → `self._coalesce_float(ctx.get('position_in_24h_range'), default=0.5)`。
- attribution 写点（2359，cosmetic 一致性）：`entry_range_pos_24h` / `entry_pre_12h_return_pct` 改用同 helper。

语义差异：旧 `0.0 or 0.5 == 0.5`；新 present `0.0` → `0.0`。非零值零行为变化。

## 3. P1-03 实现：`_apply_regime_policy` delegate + 保留 probe 外壳

两函数对 `daily_bias != 'bearish'` 行为不同：① 立即 reject `daily_bearish_required`（无 probe）；② 先试 probe 再决定。delegate 必须保留外壳。替换 2904-2950 内联实现为：

```python
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

**正确性论证**：
- ① 在 `daily_bias != bearish` 时立即返回 `daily_bearish_required`，从不评估结构 gate；现状 ② 在 probe 成功路由后也跳过结构 gate（结构 gate 在 `elif daily_bias==bearish` 分支）→ 映射一致。
- 其它结构 reason（range/pre_move/rsi/score/htf）仅在 `daily_bias==bearish` 时由 ① 评估并返回 → 外壳直接透传拒单，与现状 `elif` 分支等价。
- `llm_result` 必须传入 ① 以保留 `llm_short_reversal_risk` 归因。

**行为变化（即修复目标）**：② 的 source 从"仅 short_ctx、默认 1.0"变为 ① 的"short_ctx OR entry_ctx、哨兵默认 0.5"。0.45 阈值附近：缺失 key 时旧 1.0/新 0.5 都 ≥0.45 → 无变化；present 0.0 时旧 ② 已正确（0.0<0.45 拒）、新同样拒 → 一致。净效果：① 修好后 ② 也享有正确的 0.0 处理。

## 4. attribution 不回归（P1-03 约束）

`_apply_short_gate_attribution` 在 accept/reject 两路径继续写 `short_gate_version` / `short_gate_decision` / `short_gate_reason` / `llm_short_reversal_risk`，delegate 后 reason/decision 取自 short_gate dict，不得丢失。

## 5. 测试策略

`tests/test_short_main_path_risk_guard.py`（短单 gate 既有 home）扩：
- present `range_position_24h=0.0` + bearish daily + 非 probe → `range_position_too_low`（P1-02 核心回归）。
- absent range metric → ① 与 ② 用同一默认（默认值一致性）。
- delegate parity：构造若干 tech，断言 `_apply_regime_policy` 与直接调 `_classify_short_entry_risk` 的拒单 reason 一致。
- probe 外壳：`daily_bias=bullish` + probe 条件满足 → delegate 后仍 `_route_to_probe`（不拒）。
- attribution：delegate 后 reject/accept 仍含四字段。
- 既有 14 case 保持全绿。

## 6. 同构核对（CLAUDE.md 红线）

`event_backtest.py` 短单 gate（396-441）已用 `.get(..., 0.5)` 正确处理 0.0 且为单份实现。P1-02 让 live 向回测对齐、P1-03 是 live 侧两份合一——**回测决策路径无需改动**。在 tasks 记录此结论满足红线"修改 Judge 公式必须同步事件回测或补同构测试"。

## 7. 风险与回归

- **R1 probe 外壳错位**：delegate 后若 `daily_bearish_required` 未走 probe 分支即回归 → 由"delegate 后 probe 路由仍生效"用例坐实。
- **R2 deferred 连带**：deferred 三路径（792/911/1032/1529）已直接调 ①，canonical 修复后行为同步改变（present 0.0 正确拒）——期望的 parity，由 range_pos=0.0 回归覆盖。
- **R3 blast radius**：集中在 judge.py 短单/long-overheat gate 取值与 regime 短单段，无执行/风控/订单路径改动。
- 回归：全量 `python3 -m pytest -q` 须全绿（基线 1066 + 新增用例后上调）；`compileall agents utils` 通过。
- 不回归红线：短单 gate 单点收口（本 change 正是让该红线名实相符）、`RSI <= 30` 三处硬阈值（853/978/1404）不动、`_apply_short_gate_attribution` 四字段不回归。

## 8. Spec Patch（已回写 delta spec）

`short-main-path-risk-guard` MODIFIED "Route-Consistent Short Risk Gate"：新增 (a) present-0.0-不合并、(b) absent-用统一默认、(c) `_apply_regime_policy` 必须 delegate 三条约束 + 4 个 Scenario（price-at-24h-low / absent-shared-default / regime-delegates / attribution-preserved）。
