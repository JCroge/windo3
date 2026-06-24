# Verify 报告: cf-choppy-neutral-tp1-floor-ab

- **Change**: cf-choppy-neutral-tp1-floor-ab
- **Design Doc**: docs/superpowers/specs/2026-06-24-cf-choppy-neutral-tp1-floor-ab-design.md
- **类型**: observability-only write-only CF lab 驱动（零 live 改动）
- **日期**: 2026-06-24

## 1. 实现核对（delta spec 6 requirement 全覆盖）

| Requirement | 落地 | 验收 |
|---|---|---|
| 两臂复盘（ladder toggle） | `classify_accepts` baseline=replay(LADDER_ON) / CF=replay(LADDER_OFF) | spec reviewer ✅ + 4 classify 测试 |
| baseline 复现自检闸 | baseline 非 accept → mismatch 排除 | `test_classify_baseline_mismatch_excluded` |
| scope 主桶+mixed 旁路 | `scope_filter` 录值 regime+neutral+long；main 跑 choppy+mixed | `test_scope_filter_*` |
| 统一 CF 结算 + 诚实门 | resolve_counterfactual+klines TP1 保守；`_plan` 传 entry_price/created_at（非 entry_ref）；min_sample=30 | `test_extract_settle_fields_contract` + `test_settle_clusters_real_resolve`（不 mock resolve） |
| 翻转纯度（只计 rr_below_floor） | 非地板翻转 → `other_flip` 不结算 | `test_classify_other_flip_excluded` |
| observability 红线 | 6 决策/风控路径禁 import | `test_decision_paths_do_not_read_choppy_tp1_floor_ab` |

- 全量基线：**1430 passed / 0 failed / 4 deselected**（1416 → 1430，+13 驱动测试 +1 红线）。compileall 通过。
- code review：APPROVED（2 Minor 已修：async def 测试避 Py3.9 loop 污染 + 删测试死字段）。

## 2. 真跑结论（核心交付）

`python3 cf_choppy_neutral_tp1_floor_ab.py`（磁带 213 replayable accept）：

### 主桶 choppy+neutral
- scope accept **195** → baseline 自检忠实 **86** / 失真排除 **109**（自检闸工作；横跨纪元复盘发散，与 ev-decouple 同类 fidelity 限）。
- **84/86（98%）忠实 accept 在 TP1≥地板下翻 reject，拒因全 `rr_below_floor`**；仅 **2** 卡 TP1 仍过；非地板翻转 0。
- `tp1_floor_rejected`（避开）桶：簇去重 21，可结算 21（tp=1 / sl=12 / expired=8），**含亏单净 R = −10.50 over 21 簇 = −0.500 R/簇**。
- `survives_tp1_floor`（保留）桶：仅 1 簇、expired。

### 旁路 mixed+neutral
- scope accept **0** —— 磁带中无 mixed+neutral 多单，**该原型在磁带内纯 choppy**。

### 诚实门裁定
- 两桶均 **INSUFFICIENT_SAMPLE**（避开桶 n=13 < 30；保留桶 n=0）。

## 3. 裁定（诚实）

**方向强烈、统计未达门。** 收紧判据（`tp1_floor_rejected` 净 R/簇 << 0 且诚实门通过 → 收紧 +EV）**因诚实门 INSUFFICIENT_SAMPLE 未满足**，按 lab 纪律 **只作 suggestive，不下「收紧 +EV」定论、不改 config、不上 live**。

但三条独立证据同向：
1. choppy+neutral 多单 **98% 只靠 lever2 阶梯抬过地板**（84/86），TP1 口径几乎全不达 1.50。
2. 避开桶 CF 结算 **8% 胜率（1 tp / 12 sl）、−0.50 R/簇**。
3. 与实盘「边缘60」深查（18% 胜率、−2.58U/单）**独立互证**负期望。

## 4. 下一步

- 常驻 harness 数据累积（choppy+neutral accept 攒到忠实样本 ≥30）后**重跑本驱动**，诚实门通过即可下定论。
- 若届时坐实收紧 +EV：是否对 choppy+neutral 上 TP1 口径地板 live gate **须另起 change**（改 `_select_rr_floor`/`_compute_ladder_rr` 红线，须 event_backtest 或 rejected 流 A/B）。本 change 只量化、不改 live。
- 旁路 mixed 当前空桶，待 mixed+neutral 多单进磁带后自动纳入。

## 5. 分支

build 完成于 `change/cf-choppy-neutral-tp1-floor-ab`，待 comet-verify 收尾处理。
