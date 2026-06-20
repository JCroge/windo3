---
comet_change: ev-decouple-forward-ab
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-20-ev-decouple-forward-ab
status: final
---

# 胜率解耦放行单前向期望复核（技术设计）

> 需求事实源为 OpenSpec delta spec `openspec/changes/ev-decouple-forward-ab/specs/ev-decouple-forward-ab/spec.md`。本文档只描述 HOW。

## 1. 目标与背景

`ev-gate-winrate-decouple`（2026-06-18）让 EV 门 `ev_winrate_gate_enabled=false` 时用固定 `p_win=0.55`、跳过胜率<40% 硬阈值。复盘最近 8 笔开仓全是 neutral 趋势 + 勉强压地板 R:R~1.5 的边缘单，实盘净亏 ~−16U。需量化「只因解耦才过门」的单的前向期望。

**explore 实测**（磁带 gate-toggle 复盘）：64 条 replayable accept → baseline 自检 52 忠实 / 12 失真 → **36 解耦放行（旧胜率门会 ev_gate 拒，占忠实的 69%）**。

新增纯 observability 驱动 `cf_ev_decouple_ab.py`，零库改动，不改 live。

## 2. 架构：分类头 + 结算半身

```
┌─ 分类头(新) ─────────────────────────────────────────┐
│ 磁带 accept 流(replayable)                            │
│   ├ baseline 臂 replay(ev_winrate_gate_enabled=False) │ = live 现配置
│   │    复现 live accept? 否→失真排除                  │
│   └ 反事实臂 replay(ev_winrate_gate_enabled=True)     │ = 旧胜率门
│        翻 reject? 是→"解耦放行" 否→"双门皆过"          │
└──────────────────────────────────────────────────────┘
              │ 两桶
┌─ 结算半身(复用 cf_lever2_rejected_ab) ───────────────┐
│ 簇去重(symbol,side; >1h 新簇; 取最早代表)             │
│ resolve_counterfactual+klines → TP1 保守 R(含亏单)    │
│ 两桶各净 R → delta                                    │
└──────────────────────────────────────────────────────┘
              │
   cf_honesty_gate 诚实门(min_sample=30) → 大概率拒答
   + real PnL(symbol+ts 模糊 join) sanity 交叉
```

驱动结构镜像 `cf_lever2_rejected_ab.py`：那个驱动用纯公式（ladder_rr）判 flip + 结算半身；本驱动把 flip 判定换成 replay gate-toggle，结算半身原样复用。

## 3. 分类头（gate-toggle 两臂复盘）

```python
GATE_OFF = {"ev_winrate_gate_enabled": False}   # = live 现配置(baseline 自检锚)
GATE_ON  = {"ev_winrate_gate_enabled": True}    # = 06-18 前旧胜率门(反事实)

def _is_accept(a): return a in ("open_long", "open_short")

# 对每条 decision=accept 且 replayable 的磁带记录:
baseline = await replay_decision(rec, GATE_OFF)
if not _is_accept(baseline.get("action")):    # 复盘失真 → 排除
    mismatch += 1; continue
cf = await replay_decision(rec, GATE_ON)
bucket = "decouple_admitted" if not _is_accept(cf.get("action")) else "both_pass"
```

- `replay_decision` 经 `perturbation` 顶层 override 切 gate（`_resolve_effective_config` 四层合并，`_EPOCH_FALLBACK` 已含 `ev_winrate_gate_enabled`），不重写门逻辑。
- baseline 自检复用 `fix-shadow-logger-replay-baseline-parity` 确立的「replay 现配置必须复现 live accept」二元闸，挡掉 ~23% 复盘失真。
- 解耦放行的反事实拒因应为 `ev_gate`（实测 36/36 全 ev_gate）；记录拒因分布以防混入其它门。

## 4. 结算半身（复用 cf_lever2_rejected_ab）

对两桶各自：

```python
# 簇去重: by (symbol, side), 排序 created, it.created - last > 3600 为新簇, 取代表
# 每簇代表:
bars = load_bars(KL1, sym, created) or load_bars(KL, sym, created)
if not bars: nodata += 1; continue
res = resolve_counterfactual(rec_plan, bars, source="tape")
r = (tp1_dist/sl_dist) if res.outcome=="tp" else (-1.0 if res.outcome=="sl" else 0.0)
```

- `tp1_dist`/`sl_dist` 从 plan 的 entry/sl/tp1 推（同 rejected_ab）。
- 两桶同口径 → 系统性 CF 偏差在 `净R_decouple − 净R_both` 的 delta 抵消。
- klines 无覆盖簇跳过并计数（coverage 受限如实报，不外推）。

## 5. 诚实门（领先裁定）

```python
from utils.cf_honesty_gate import summarize_bucket
# 每桶: wins=tp_n, losses=sl_n, net_usdt_samples=[每簇 R*名义?] 或 R 序列
verdict = summarize_bucket(wins=tp_n, losses=sl_n, net_usdt_samples=R_list,
                           min_sample=30, lowconf_sample=100)
```

- **min_sample=30 不下调**（项目反过拟合纪律）。解耦放行簇去重后预计 ~6–10 → **诚实门几乎必然拒答**。
- 报表**领先显示诚实门裁定**；两桶 raw 净 R 仅作 `suggestive`、显式标注「低于诚实门、不作结论」。
- 判据「解耦放行净 R << 双门皆过且 <0 → 提示解耦放行亏损单」**仅在诚实门通过时触发**。

## 6. real PnL 模糊 join（次要 sanity）

- 解耦放行的实际开仓单：同 `symbol`+`side`，`opened_at ∈ [decision_ts, decision_ts+600s]` 取最近一条 lifecycle，报真实 `total_realized_pnl`。
- 显式标注：模糊 join（lifecycle 无 request_id）、accept≠开仓（限价未成/slot 可能无对应仓）、`pending`/`external_close` 不计入。
- 仅作 CF 估算的 sanity 交叉，不作主判据。

## 7. 边界条件

| 情形 | 处理 |
|---|---|
| baseline 臂复盘失真（≠live accept） | 排除，计 mismatch 数 |
| 反事实拒因非 ev_gate | 记入拒因分布，仍归"解耦放行"但标注 |
| klines 无覆盖 | 跳过该簇，计 nodata 数，报表透明 |
| 去重簇 < min_sample | 诚实门拒答，raw 数仅 suggestive |
| real PnL pending/external_close | 不计入 sanity 交叉 |
| 磁带 accept 不 replayable | 跳过（同 explore 口径） |

## 8. 输出价值定位（重要）

本 change **当下不给红绿灯**：样本太薄、诚实门拒答。交付的是：
1. **常驻测量 harness**——数据累积后重跑，样本够即自动给结论。
2. **当下诚实读数**——69% accept 解耦放行（决策级，已实测）+ 两桶 raw 净 R（suggestive）。

符合「证据足够再决定回滚/约束」的原意。回滚/约束是**另起 change**，不在本范围。

## 9. 测试策略

`tests/test_cf_ev_decouple_ab.py`：
1. 分类：构造 accept 磁带记录，monkeypatch `replay_decision` 使 gate-on→reject → 归 decouple_admitted；gate-on→accept → 归 both_pass。
2. baseline 自检：baseline 臂 monkeypatch 返回 hold（≠live accept）→ 该条排除、计 mismatch。
3. 簇去重：同 symbol/side 间隔 <1h 归一簇、>1h 两簇。
4. 诚实门：薄样本（簇<30）→ 裁定拒答、不输出 net R 结论。
5. 红线守卫 `tests/test_cf_red_line_guard.py` 扩展：决策/风控路径禁 import/读 `cf_ev_decouple_ab`。

全量回归零退化。

## 10. 红线（不变）

- observability-only write-only：输出严禁交易决策/风控路径消费、绝不下单、绝不自动改 config（`ev_winrate_gate_enabled` 等）、绝不 mutate live。
- 复盘臂复用 `replay_decision` 隔离机器（publish 绝不进真实 bus、`MultiJudge.__new__` 不碰 live 实例）。

## 11. 非目标

- 不改 `open-gate-ev` 门逻辑、不回滚/约束解耦（证据足够后另起 change）。
- 不下调诚实门阈值凑结论。
- 不解决 klines coverage 受限（如实报跳过数）。
- 不追求 real PnL 精确归因（无 request_id，模糊 join 仅 sanity）。
