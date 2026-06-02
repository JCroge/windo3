---
comet_change: entry-drift-hybrid-policy
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-02-entry-drift-hybrid-policy
status: final
---

# Entry Drift Hybrid Policy — Technical Design

## 背景

5/30 XLM 实盘案例：Judge 在 03:48:08 看到 price=0.2179 算出 plan（entry≈0.2179, SL=0.2125, TP=[0.2312, ...], R:R=2.19, lev=10x）。03:48:18 plan SELECTED；03:48:19 限价单挂出，但 executor 入口的 `fetch_ticker` 已经把 `current_price` 刷成 0.2337，line 2203-2205 的"偏离 2% 重新校准"把 `limit_price` 改成 0.2334；30 秒等不到成交，03:48:50 fallback 市价 @ 0.2336（较 plan entry 涨 7.2%）；03:48:53 `partial_tp_1` 立即触发——因为 `position.take_profit_levels[0] = 0.2312 < entry = 0.2336`，仓刚开就被 TP1 扫掉一半。整笔 XLM 行情 0.2179 → 0.2401（10.2%），10x 杠杆只赚 +0.78 USDT。

根因三层：
1. **plan stale 容忍度过宽**：plan.entry 与 live_price 漂移 7.2% 时系统只校准 limit_price，不复检"该不该入场"。既有的 fallback 0.5% abandon 检查（`executor.py:2259`）用的 `current_price` 是入参（已被 line 1974 `fetch_ticker` 覆盖），从设计上检测不到 plan 与现价的 stale 距离。
2. **plan 字段缺锚点**：Judge `_build_plan` 把 price 入参用完即丢，没落 `entry_ref`，下游想"按比例重算 SL/TP"无锚点。
3. **partial_tp_1 双源真相**：position 同时落 `take_profit`（被 line 1991-1997 机械修正成 entry+3%）与 `take_profit_levels`（plan 原始 TP 列表），partial_tp_1 判定走后者，方向修正完全是空的。

OpenSpec proposal（`openspec/changes/entry-drift-hybrid-policy/proposal.md`）已锁定 Why / What。本设计文档只回答 HOW。

## 总览

```
                     ENTRY DRIFT HYBRID POLICY
                     ════════════════════════════

Judge._build_plan ──► plan{entry_ref, sl_pct, tp_pct, ...}
                              │
                              ▼
              executor.open_position_with_plan
                              │
                       fetch_ticker → live_price
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  Gate 1: _classify_entry_drift(plan,    │
        │                                live_price)│
        └─────────────────────────────────────────┘
                              │
        ┌──────────┬──────────┼──────────┬─────────────┐
        ▼          ▼          ▼          ▼             ▼
      accept    small      medium     recalc_fail   abandon
       │         │           │           │             │
       │         ▼           ▼           ▼             ▼
       │    _recompute_plan_for_drift  reject       reject
       │    (deepcopy + sl_pct/tp_pct  reason=      reason=
       │     同比例平移 to new_entry)  drift_rr_    drift_too_
       │         │                     floor_fail   large
       │         │ (medium floor +0.20)
       │         ▼
       └───► 限价挂单
                │
                ▼ 30s timeout
        ┌─────────────────────────────────────────┐
        │  Gate 2: _classify_entry_drift(orig_plan,│
        │                                fallback_p)│   ← 始终原 plan
        └─────────────────────────────────────────┘
                │
              accept→市价  其他→reject

落库：_set_position_tp(position, tp_first, tp_levels)  ← 唯一收口
读时双保险：_update_trailing partial_tp_1 触发前 assert
违反 → halt symbol + risk_alert.tp_invariant_breach

观测：
  - execution_result.v2.attribution.entry_drift{band, drift_pct, decision, recompute_reason}
  - data/<ns_>live_order_events.jsonl: event=entry_drift_decision
  - risk_alert.type ∈ {entry_drift_abandoned, entry_drift_rr_fail,
                       plan_missing_entry_ref, tp_invariant_breach}
    全进 critical_types
```

## 关键决策

### D1：plan 字段缺失时 fail-safe accept（A + 强可观测）

executor 拿到缺 `entry_ref` / `sl_pct` / `tp_pct` 任一字段的 plan 时，drift gate 直接返回 `DriftDecision(band='accept', decision='accept', drift_pct=0.0)` 进入既有路径，并发一次 `risk_alert.type=plan_missing_entry_ref`（critical_types）。

**取舍**：
- 备选 B（用 live_price 当 entry_ref 即时回填）形式上跑了 gate 但 drift 必为 0%，欺骗性，污染切片统计。
- 备选 C（强制 reject）合并日 Judge 没重启会全停。

**选 A 的理由**：合并日实盘不全停 + 告警过渡期立即可见。Judge 升级完成后告警自然消失；24h 仍在告警说明还有老进程没重启。

### D2：medium band R:R floor 加成 +0.20（绝对加成）

medium band（2% < drift ≤ 5%）重算后 R:R 复检，floor 在 plan.attribution 里 Judge 决策时的 floor 基础上 **+0.20**。例：原 floor=2.00 → 2.20；原 floor=1.30（long_aligned_low_rr）→ 1.50；原 floor=2.50 → 2.70。

**取舍**：
- 备选 B（× 1.10 比例加成）对低 floor 收紧不够（1.30 → 1.43）。
- 备选 D（不加成）等于把"该不该开"完全交给 Judge 当时基于 `entry_ref` 的 floor，但 drift 5% 时市场状态可能已变。

**选 +0.20 的理由**：drift 是绝对量百分比，floor 加成绝对量更直观对应——drift 每涨 1%，可以理解为"额外要求 0.05 R:R buffer"，medium band 中位约 3.5% drift → 加成 0.18 ≈ 0.20。R:R Floor Policy 现 5 个 policy 标签 floor 范围 1.30 ~ 2.50，+0.20 收紧后仍允许 long_aligned 类深度回调多头入场，但不再放最贪心的。

### D3：invariant 违反 = halt symbol + 写时收口

落库点统一 `position.take_profit == position.take_profit_levels[0]`，由单一 setter 函数 `_set_position_tp(position, tp_first, tp_levels)` 保证。读时双保险：`_update_trailing` partial_tp_1 / partial_tp_2 触发前 assert。违反 → `_halt_symbol(symbol, reason='tp_invariant_breach')` + `risk_alert.type=tp_invariant_breach`（critical_types）。

**取舍**：
- 备选 B（log only）违反就违反了，无人查告警 noise，invariant 实质失效。
- 备选 C（自动修正）违反原因被掩盖，根本问题被静默"修好"。

**选 A + D 的理由**：本 change 核心保护就是杜绝双源真相再次悄悄回潮，halt 代价小于静默退化。写时收口让所有未来新代码点必须过 setter，读时 assert 兜底防漏。

### D4：Gate 2 基准始终用原 plan.entry_ref

Gate 2 在限价单 30s 超时降级 fallback 市价前再跑一次 `_classify_entry_drift`，输入的 plan **始终是原 plan（带原 entry_ref）**，不是 Gate 1 重算后的 plan。

**理由**：drift 累计基准是 Judge 决策时点。如果 Gate 2 用 Gate 1 重算后的 plan，等于把"小漂 + 小漂"变成两次 small band 都通过，规避 medium/abandon。Gate 2 累计 drift 必须从 Judge 决策时点起算。

### D5：_recompute_plan_for_drift 用 deepcopy + 加字段（不用 diff）

```python
new_plan = copy.deepcopy(plan)
new_plan['stop_loss'] = new_entry * (1 - plan['sl_pct']) if side == 'long' else new_entry * (1 + plan['sl_pct'])
new_plan['take_profit'] = [new_entry * (1 ± tp_pct[i]) for i in range(len(tp_pct))]
new_plan['recompute_reason'] = 'drift_small' | 'drift_medium'
new_plan['original_entry_ref'] = plan['entry_ref']
new_plan['recomputed_entry'] = new_entry
new_plan['recomputed_sl'] = new_plan['stop_loss']
new_plan['recomputed_tp'] = new_plan['take_profit']
new_plan['rr_floor_used'] = floor + (0.20 if drift_band == 'medium' else 0.0)
new_plan['rr_actual_after_recompute'] = ...
# entry_ref 字段保留原值不动，drift gate 始终基于 Judge 决策时点
```

**理由**：执行链下游已有数十处 `plan.get('xxx')` 引用，diff 模式要给每个引用点加 fallback，太脆弱。deepcopy + 原地改让所有下游引用透明地拿到新值。

## 函数契约

### `_classify_entry_drift(plan, live_price) -> DriftDecision`

```python
@dataclass(frozen=True)
class DriftDecision:
    band: Literal['accept', 'small', 'medium', 'abandon']
    drift_pct: float                    # 相对 plan.entry_ref 的漂移
    decision: Literal['accept', 'recalc_pass', 'recalc_fail', 'abandon']
    reason: Optional[str]               # 'drift_too_large' / 'drift_rr_floor_fail' / None
    new_plan: Optional[dict]            # decision='recalc_pass' 时填，否则 None
    rr_actual: Optional[float]
    rr_floor_used: Optional[float]
```

行为：
- plan 缺 `entry_ref` / `sl_pct` / `tp_pct` 任一 → `DriftDecision(band='accept', decision='accept', drift_pct=0.0, ...)`，**额外触发** `risk_alert.plan_missing_entry_ref`
- `drift = abs(live_price - plan['entry_ref']) / plan['entry_ref']`
- `drift ≤ 0.005` → accept
- `0.005 < drift ≤ 0.02` → 调 `_recompute_plan_for_drift(plan, live_price, 'small')`，floor 加成 0.0
- `0.02 < drift ≤ 0.05` → 调 `_recompute_plan_for_drift(plan, live_price, 'medium')`，floor 加成 +0.20
- `drift > 0.05` → abandon，reason='drift_too_large'
- 重算 R:R 不过 floor → recalc_fail，reason='drift_rr_floor_fail'

边界包含规则：每个边界值（0.005 / 0.02 / 0.05）划入下一档之前的档位（即 0.005 仍 accept，0.02 仍 small，0.05 仍 medium）。

### `_recompute_plan_for_drift(plan, new_entry, drift_band) -> Optional[dict]`

```python
def _recompute_plan_for_drift(
    plan: dict,
    new_entry: float,
    drift_band: Literal['small', 'medium'],
) -> Optional[dict]:
    """按 plan.sl_pct / tp_pct 同比例平移 SL/TP 到 new_entry。
    medium band floor 加成 +0.20。
    R:R 复检不过返回 None。
    """
```

实现：
1. `new_plan = copy.deepcopy(plan)`
2. `side = plan['side']`
3. 按 `sl_pct` 平移 SL：long → `new_entry * (1 - sl_pct)`；short → `new_entry * (1 + sl_pct)`
4. 按 `tp_pct[i]` 平移每档 TP
5. 复检 R:R：`tp_dist = abs(tp[0] - new_entry) / new_entry`；`sl_dist = sl_pct`；`rr = tp_dist / sl_dist`
6. `floor = plan['attribution'].get('rr_floor', 2.0) + (0.20 if drift_band == 'medium' else 0.0)`
7. `rr < floor` → 返回 None
8. 否则填 `recompute_reason` / `original_entry_ref` / `recomputed_entry` / `recomputed_sl` / `recomputed_tp` / `rr_floor_used` / `rr_actual_after_recompute`，返回 new_plan

### `_set_position_tp(position, tp_first, tp_levels) -> None`

```python
def _set_position_tp(
    position: dict,
    tp_first: float,
    tp_levels: List[float],
) -> None:
    """唯一 TP 字段写入收口。保证 take_profit == take_profit_levels[0]。
    所有给 position dict 赋 TP 字段的代码点必须调用此函数。
    """
    assert tp_levels, "tp_levels must be non-empty"
    assert tp_first == tp_levels[0], f"tp_first {tp_first} must equal tp_levels[0] {tp_levels[0]}"
    position['take_profit'] = tp_first
    position['take_profit_levels'] = list(tp_levels)
```

调用点：
- `executor.py:2114-2134` 的 position 构造（替换原 `'take_profit': tp_first, 'take_profit_levels': take_profit`）
- `_apply_recomputed_plan_to_position`（重算后写入新 TP）
- 未来任何新增写 TP 的代码点

### `_apply_recomputed_plan_to_position(position, recomputed_plan) -> None`

```python
def _apply_recomputed_plan_to_position(position: dict, recomputed_plan: dict) -> None:
    """drift gate recalc_pass 后，把重算结果落到落库前的 position 字段上。"""
    position['stop_loss'] = recomputed_plan['stop_loss']
    position['original_sl'] = recomputed_plan['stop_loss']
    _set_position_tp(position, recomputed_plan['take_profit'][0], recomputed_plan['take_profit'])
```

## Gate 接入位置

### Gate 1：`open_position_with_plan` 入口

修改 `executor.py:1974` 之后：

```python
ticker = self.exchange.fetch_ticker(symbol)
current_price = ticker['last']

# === Gate 1: Drift Classification ===
drift_decision = self._classify_entry_drift(plan, current_price)
self._record_drift_decision(symbol, drift_decision, gate='gate_1')  # jsonl + attribution

if drift_decision.decision == 'abandon':
    return self._reject_with_reason(symbol, plan, 'drift_too_large', drift_decision)
if drift_decision.decision == 'recalc_fail':
    return self._reject_with_reason(symbol, plan, 'drift_rr_floor_fail', drift_decision)
if drift_decision.decision == 'recalc_pass':
    plan = drift_decision.new_plan
    current_price = drift_decision.recomputed_entry  # 重算后用新 entry 推后续逻辑
    stop_loss = plan['stop_loss']
    take_profit = plan['take_profit']
# accept 路径：plan/current_price 不变

# 既有 SL/TP 方向校验保留（D6: 改为 invariant 断言）
```

### Gate 2：`_execute_limit_order` fallback 前

修改 `executor.py:2257` 之后：

```python
ticker = self.exchange.fetch_ticker(symbol)
new_price = ticker['last']

# === Gate 2: Drift Classification (基准始终原 plan.entry_ref) ===
drift_decision = self._classify_entry_drift(orig_plan, new_price)  # orig_plan 由调用方传入
self._record_drift_decision(symbol, drift_decision, gate='gate_2')

if drift_decision.decision in ('abandon', 'recalc_fail'):
    return None  # 上游已 reject，保护单也不挂

# accept / recalc_pass 都走市价
if drift_decision.decision == 'recalc_pass':
    # Gate 2 重算的 plan 直接挂 attach algo（SL/TP 用新值）
    fallback_attach = self._build_attach_algo_from_tp_sl(
        self._build_tp_sl_params(side, drift_decision.new_plan['stop_loss'],
                                  drift_decision.new_plan['take_profit'][0],
                                  sl_clord_id=...)
    )
else:
    fallback_attach = self._build_attach_algo_from_tp_sl(tp_sl_params)
```

调用方 `open_position_with_plan` 把原 plan（不是 Gate 1 重算后的）显式传入 `_execute_limit_order`，确保 Gate 2 基准纯净。

## 删除清单

| 位置 | 原代码 | 删除原因 |
|------|--------|---------|
| `executor.py:1991-1997` | TP 方向修正机械加 ±3% | drift gate 通过后 TP 在正确一侧由重算保证；不通过的 reject，不会到这里 |
| `executor.py:2203-2205` | "限价单偏离 2% 重新校准" | 取代为 Gate 1 显式判定；不再静默改 limit_price |
| `executor.py:2259-2262` | fallback 7.2% abandon 检查（实际从未触发） | 取代为 Gate 2 |

## SL 方向修正改为 invariant 断言

`executor.py:1983-1988` 的 SL 方向修正保留代码但改为 invariant：

```python
if side == 'short' and stop_loss <= current_price:
    raise AssertionError(
        f"SL invariant breach (short): SL={stop_loss} <= entry={current_price}. "
        f"Drift gate should have rejected this case."
    )
elif side == 'long' and stop_loss >= current_price:
    raise AssertionError(...)
```

drift gate 通过后 SL 一定在正确一侧。如果 invariant 失败，说明上游有 bug，fail-closed 比静默"修正"更安全。assert 失败 → halt symbol + `risk_alert.type=sl_invariant_breach`。

## Judge plan 字段扩展

`agents/trading/judge.py:_build_plan` 修改：

```python
sl_pct = abs(price - stop_loss) / price
tp_pct = [abs(tp - price) / price for tp in take_profit]

return {
    "side": "long" if is_long else "short",
    "entry_ref": price_round(price),     # NEW
    "sl_pct": round(sl_pct, 6),          # NEW
    "tp_pct": [round(p, 6) for p in tp_pct],  # NEW
    "entry_zone": [...],
    "stop_loss": price_round(stop_loss),
    "take_profit": [...],
    # ... 其他既有字段
}
```

`event_backtest.py` 兼容性：plan 缺 `entry_ref` 时走 fail-safe accept 路径（D1），不破坏历史回放。

## 可观测性

### attribution 嵌套

`execution_result.v2.attribution.entry_drift`：

```python
{
    "band": "accept" | "small" | "medium" | "abandon",
    "drift_pct": 0.072,
    "decision": "accept" | "recalc_pass" | "recalc_fail" | "abandon",
    "recompute_reason": "drift_small" | "drift_medium" | None,
    "rr_actual": 2.05,           # 重算后 R:R（accept 路径为 None）
    "rr_floor_used": 2.20,       # 复检 floor（含 medium 加成）
    "gate": "gate_1" | "gate_2",
}
```

### jsonl 事件

`data/<ns_>live_order_events.jsonl` 写入：

```json
{
    "event": "entry_drift_decision",
    "ts": 1717228800.123,
    "symbol": "XLM-USDT",
    "side": "long",
    "request_id": "req-abc123",
    "gate": "gate_1",
    "plan_entry_ref": 0.2179,
    "live_price": 0.2336,
    "drift_pct": 0.0720,
    "band": "abandon",
    "decision": "abandon",
    "reason": "drift_too_large",
    "rr_floor_used": null,
    "rr_actual": null
}
```

### risk_alert 新枚举

加入 `agents/trading/executor.py` `critical_types` 集合：

- `entry_drift_abandoned`：drift > 5% 触发 abandon
- `entry_drift_rr_fail`：重算后 R:R 不过 floor
- `plan_missing_entry_ref`：plan 缺新字段，fail-safe accept
- `tp_invariant_breach`：take_profit ≠ take_profit_levels[0]，halt symbol
- `sl_invariant_breach`：SL 落错一侧，halt symbol

Telegram 文案模板：
```
[entry_drift_abandoned] XLM-USDT
plan.entry_ref=0.2179 → live=0.2336 (drift=7.2%)
gate=gate_1, decision=abandon
策略已放弃此次入场。
```

## 阈值常量

`executor.py` 文件级常量：

```python
ENTRY_DRIFT_ACCEPT_PCT = 0.005   # 0.5%
ENTRY_DRIFT_SMALL_PCT  = 0.02    # 2%
ENTRY_DRIFT_LARGE_PCT  = 0.05    # 5%
ENTRY_DRIFT_MEDIUM_FLOOR_BUMP = 0.20
```

不进 yaml 配置（避免运行期改阈值绕过单测）。后续 Reviewer 切片观察后调参时改常量重测。

## 测试策略

### 新增 `tests/test_entry_drift_hybrid_policy.py`

预估 30~35 case，分组：

**Drift band 边界（8 case）**：
- drift=0.49% → accept
- drift=0.50% → accept（边界包含）
- drift=0.51% → recalc_small
- drift=2.00% → recalc_small（边界包含）
- drift=2.01% → recalc_medium
- drift=5.00% → recalc_medium（边界包含）
- drift=5.01% → abandon
- **drift=7.20% → abandon（5/30 XLM 真实复盘）**

**Recalc R:R 复检（10 case）**：
- small band recalc R:R=2.05 floor=2.00 → accept
- small band recalc R:R=1.85 floor=2.00 → recalc_fail
- medium band recalc R:R=2.10 floor=2.00 → recalc_fail（加成 +0.20 → 2.20）
- medium band recalc R:R=2.30 floor=2.00 → accept（2.30 > 2.20）
- 各 policy_label（probe / long_bullish_low_rr / long_aligned_low_rr / short_bullish_strong / default）floor 各跑一次

**Gate 2 累加（2 case）**：
- Gate 1 通过 small（drift=1%），30s 后 Gate 2 累计 drift=4% → recalc_medium
- Gate 1 通过 small（drift=1%），30s 后 Gate 2 累计 drift=6% → abandon

**Plan 字段缺失 fail-safe（4 case）**：
- 缺 entry_ref → DriftDecision(band='accept', drift_pct=0.0) + risk_alert.plan_missing_entry_ref
- 有 entry_ref 缺 sl_pct → 同上
- 有 entry_ref 缺 tp_pct → 同上
- 完整字段 → 正常跑 gate

**partial_tp_1 双源真相 invariant（4 case）**：
- 通过 _set_position_tp 写入 → 两字段一致
- _set_position_tp 输入 tp_first ≠ tp_levels[0] → AssertionError
- 旁路写入 position['take_profit']=X → partial_tp_1 触发前 assert 失败 → halt symbol + risk_alert.tp_invariant_breach
- recompute 后调 _set_position_tp → 两字段同步刷新

**Attribution & jsonl（3 case）**：
- execution_result.v2.attribution.entry_drift 字段完整
- jsonl entry_drift_decision 事件完整
- gate 字段正确（gate_1 / gate_2）

**Reject reason（2 case）**：
- drift_too_large → execution_result.v2 status=rejected reason=drift_too_large
- drift_rr_floor_fail → reason=drift_rr_floor_fail

### event_backtest.py 兼容回归（1 case）

- mock 老 plan（无 entry_ref/sl_pct/tp_pct）跑 _execute_limit_order → 行为等价于现行实现（fail-safe accept 路径）

### baseline 升级路径

921 → ~955。测试通过后更新 `CLAUDE.md` "当前事实" 段。新增 jsonl 事件 `entry_drift_decision` 不破坏既有 reconciler / Reviewer 解析。

### OKX testnet 冒烟（验收时跑）

- 真实 testnet small drift recalc 通过的开仓
- 真实 testnet abandon 的开仓（mock 价格漂移）
- attribution 字段在真实 execution_result.v2 上正确落地

## 风险与边界

1. **Judge 与 executor 必须同步部署**：否则 executor 拿到老 plan 全部走 fail-safe accept，行为退化但不破坏。运维 SOP 必须保证两个进程同步重启。
2. **阈值首版**：0.5% / 2% / 5% / +0.20 是基于 5/30 XLM 案例 + ATR 经验拍的，未做大规模 backtest。Reviewer 后续切片观察后调参；改常量重测即可。
3. **5/30 XLM 验证用例必须真实复现 abandon**：测试用例必须用真实日志数值（entry_ref=0.2179, live=0.2336, drift=7.2%）跑 Gate 1，确认返回 abandon。
4. **Gate 2 与限价校准的取舍**：现行 line 2203-2205 校准在 limit 单挂出前，本设计在 Gate 1 已经做完 drift 判定，limit_price 直接用 `live_price * (0.999 if long else 1.001)`，不再二次校准。这意味着如果 Gate 1 是 recalc_pass，limit_price 会基于 `recomputed_entry` 而非原 entry_zone 中点——这是预期行为（重算后 entry zone 也应该平移）。
5. **idempotency 窗口与 reject**：Gate 1/2 reject 时 `clord_id` 已被 mark（line 1962）。10s 内不会重发同 symbol 同 side 的开仓请求，符合现行幂等语义。
6. **Reviewer 切片**：本 change 只打开 attribution + jsonl 数据通道，不实现 Reviewer 侧的"重算入场胜率"切片。后续在 Reviewer 中按 `attribution.entry_drift.decision` 分组统计胜率即可。

## Spec Patch 回写

无新增验收场景需要回写到 OpenSpec delta spec。proposal 已覆盖所有 What。delta spec 在下一阶段（specs 阶段）正式落地，本 design 文档不重复 spec 内容。

## 不在本 change 范围

- Reviewer 侧"重算入场胜率"切片（数据通道已打开，分析另立 change）
- Judge 端 plan stale 后是否重新出 plan（本 change 不动 Judge bus 行为，executor 单边修复）
- 阈值动态化 / yaml 配置（首版为常量，避免运行期改阈值绕过单测）
- 其他 Gate（如 plan 与 trade_decision 之间的 stale）暂不引入
