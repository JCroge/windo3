## Why

深查近期「边缘60」亏损单（13/13 已结算，均 PnL −2.58U）证明它们是同一原型：**choppy 体制 + neutral 趋势 + effective_rr 1.51–1.65 贴 1.50 地板**，而真实 TP1 口径 `effective_rr_tp1` 全部落在 1.28–1.40（< 1.50）。它们能进场，靠 lever2 阶梯口径把 effRR 抬过地板 + ev-decouple 把 p_win 钉 0.55 联合放行。我们需要量化：**若对 choppy+neutral 多单改用 TP1 口径地板（要求 `effective_rr_tp1 ≥ 地板`），会拒掉多少单、净 PnL delta 多少**——在动 live 之前先拿到可信的反事实证据（符合「先仪表化再动旋钮」纪律）。

## What Changes

- 新增 observability-only 反事实驱动 `cf_choppy_neutral_tp1_floor_ab.py`，对决策磁带 `decision=accept` 流做两臂复盘：
  - **baseline 臂** = `replay(ladder_rr_enabled=True)`（= live 现状，lever2 默认开；自检复现 live accept）
  - **CF 臂** = `replay(ladder_rr_enabled=False)`（floor gate 改比 TP1 口径 effRR = 「choppy+neutral 卡 TP1≥地板」）
  - accept→reject 翻转单 = 「TP1 地板会拒掉」的单（= 收紧后会避开的单）
- 预过滤 scope：**主桶 = `regime_state==choppy` AND `trend.direction==neutral` 的 `open_long`**；**旁路桶 = `mixed`+neutral 多单**作对照。
- baseline 复现自检闸：baseline 臂 accept/reject 与磁带录值不一致 → 标 `baseline_mismatch` 排除（对齐 ev-decouple / perturbation_replay）。
- 两桶（`tp1_floor_rejected` 避开的单 / `survives_tp1_floor` 卡 TP1 仍过的单）统一 `resolve_counterfactual` + klines 结算 TP1 保守净 R，`cf_honesty_gate.summarize_bucket(min_sample=30 不下调)` 薄样本拒答。
- 解读：`tp1_floor_rejected` 桶净 R << 0 且诚实门通过 → 收紧对此原型 +EV（避开负期望单）；real PnL 模糊 join lifecycle 作次要 sanity。
- 扩展 `tests/test_cf_red_line_guard.py`：决策/风控路径禁 import 新驱动（新增 `test_decision_paths_do_not_read_choppy_tp1_floor_ab`）。

## Capabilities

### New Capabilities
- `cf-choppy-neutral-tp1-floor-ab`: choppy+neutral 多单 TP1 口径地板的反事实 A/B 量化能力——两臂复盘（ladder toggle）+ baseline 自检 + 统一 CF 结算 + 诚实门，observability-only write-only，绝不下单/改 config。

### Modified Capabilities
<!-- 无 spec-level 行为变更：不改 judge/executor/任何 live 决策路径；仅新增离线分析驱动 + 守卫测试。 -->

## Impact

- **新增文件**：`cf_choppy_neutral_tp1_floor_ab.py`（repo 根，复用 `utils/decision_replay.replay_decision` / `utils/counterfactual_pnl.resolve_counterfactual` / `utils/cf_honesty_gate.summarize_bucket`）。
- **修改文件**：`tests/test_cf_red_line_guard.py`（+1 禁读断言）。
- **零 live 改动**：不碰 `judge.py` / `executor.py` / config.yaml / 任何决策或风控路径；不改 `_select_rr_floor` / `_compute_ladder_rr`。
- **数据依赖**：读 `data/decision_replay_tape.jsonl`（accept 流）/ `data/klines_1s.db` + `data/klines.db`（结算）/ `data/live_position_lifecycle.json`（sanity join）。
- **已知 fidelity 边界**：klines_1s 仅覆盖近 ~数日 ~数十标的，更早磁带无覆盖簇跳过并计数；choppy+neutral 子样本可能薄 → 诚实门可能 INSUFFICIENT_SAMPLE（如实报，净 R 仅 suggestive）。
