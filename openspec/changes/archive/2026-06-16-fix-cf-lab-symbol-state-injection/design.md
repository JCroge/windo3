# Design (high-level) — fix-cf-lab-symbol-state-injection

> OpenSpec 高层草图；详细 RFC + 方案权衡定在 comet-design 的 Superpowers Design Doc。

## 问题边界（100% 坐实）

```
record(录制 _symbol_state = {last_tech, trend_streak, last_open_time, ...})
  │  直接 L2 replay(录制快照)        → rr_below_floor   ✅ 0.914
  │
  └─ run_arm: _inject_cf_state → cf.to_snapshot()._symbol_state = {}（清空）
        │
        ▼  replay → 信号强度路径读不到 trend_streak/last_tech → "信号强度不足" → hold_other  ❌ 0.798
```
override 还原 `_symbol_state` → 90/90 残差全复现。

## 字段分类（决定 overlay 边界）

| 字段 | 性质 | 处理 |
|---|---|---|
| `last_tech` / `trend_streak` / `last_decision_time` | 市场决策输入(CF 无法重建) | **还原录制值** |
| `last_open_time` / `last_force_close_time` | position-outcome(依赖 CF 自身开仓) | baseline 用录制值；perturbed 臂 CF 开过该 symbol 则 overlay CF 值 |

## 候选方案（comet-design 定夺）

- **A. `_inject_cf_state` 以录制 `_symbol_state` 为基**，CF 仅对自己 `_open` 里的 symbol overlay position-outcome 字段。最小、直接、baseline 100% 复现，perturbed 级联保留。
- **B. `cf.to_snapshot` 接收录制 `_symbol_state` 作种**，内部合并 CF position 事件。封装在 CF 侧，但 to_snapshot 需新增入参。
- 共同约束：还原的是市场决策输入（非交易结果累计）——不违反 L3b "绝不注入 reality 演化计数"；position-outcome 字段仍由 CF 自累计保级联。

## 不变量 / 红线

- observability-only write-only；红线守卫 `tests/test_cf_red_line_guard.py` 维持。
- 不改 live Judge 决策逻辑、不改生产 config、不改 choppy 地板 1.50、无需 event_backtest。
- perturbed 臂级联真实性不被削弱（position-outcome 字段仍随 CF 自身开仓演化）。
