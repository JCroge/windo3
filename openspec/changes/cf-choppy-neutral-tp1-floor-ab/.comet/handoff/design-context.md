# Comet Design Handoff

- Change: cf-choppy-neutral-tp1-floor-ab
- Phase: design
- Mode: compact
- Context hash: ea86685cbea2cc54614f3dd0ddfee901dfb06ab4975d6e8b44516d831b7fcac0

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/cf-choppy-neutral-tp1-floor-ab/proposal.md

- Source: openspec/changes/cf-choppy-neutral-tp1-floor-ab/proposal.md
- Lines: 1-31
- SHA256: 852168f5bd4acfc0092a0849c7caffc78961bed7d6391f0ee0486b61196e90bc

```md
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
```

## openspec/changes/cf-choppy-neutral-tp1-floor-ab/design.md

- Source: openspec/changes/cf-choppy-neutral-tp1-floor-ab/design.md
- Lines: 1-43
- SHA256: 5ae861450bbe6f3d89be03eedd4e45815097ad11322efb589deec5496cd5801b

```md
## Context

`ev-gate-winrate-decouple`(06-18) + `trend-entry-levers-default-on`(06-17 lever2 阶梯口径默认开)上线后，衰减期放行了一批边缘多单。深查 13 笔已结算「边缘60」单实证：**13/13 = choppy 体制 + neutral 趋势（强度 22–48 弱）+ `effective_risk_reward_ratio` 1.51–1.65 贴 1.50 地板**，但 `effective_rr_tp1` 全部 1.28–1.40（< 1.50），靠 lever2 阶梯口径抬过地板进场。均 PnL −2.58U（赢小亏大）。

机制已在 judge.py 验证：`_build_plan`(3690) → `_effective_rr_for_plan`(3682) 在 `_ladder_rr_enabled=False` 时返回 TP1-only 口径；floor gate(1483)读 `plan['effective_risk_reward_ratio']` 比地板。`replay_decision` 真实重跑 `_make_decision`/`_build_plan`，`_install_config_flags`(decision_replay.py:233)接受 `ladder_rr_enabled` override。因此对 choppy+neutral 多单 toggle `ladder_rr_enabled` 即可干净复现「TP1 口径地板」反事实。

现有姊妹 driver：`cf_ev_decouple_ab.py`(accept 流 + 胜率门 toggle)、`cf_lever2_rejected_ab.py`(reject 流 + 解析式 ladder 翻转，反方向)。本驱动是 accept 流 + ladder toggle + **体制条件 scoping**，新角度、不重复。

## Goals / Non-Goals

**Goals:**
- 量化「choppy+neutral 多单卡 TP1≥地板」对决策磁带的反事实净 PnL delta：拒掉多少、避开的单净 R 几何。
- 严格镜像 `cf_ev_decouple_ab.py` 的两臂复盘 + baseline 复现自检闸 + 统一 CF 结算(TP1 保守) + 诚实门(min_sample=30 不下调)。
- 主桶 choppy+neutral，旁路 mixed+neutral 对照（用户选定 scope）。
- observability-only write-only，红线守卫扩展，绝不下单/改 config。

**Non-Goals:**
- 不改任何 live 决策/风控路径（judge/executor/config.yaml/`_select_rr_floor`/`_compute_ladder_rr` 全部不动）。
- 不实现「条件化 TP1 地板」的 live gate——本 change 只量化「what-if」，是否上 live 由后续 change 据本结论另议。
- 不下调诚实门、不据薄样本下结论。
- 不回填历史、不改决策磁带 schema。

## Decisions

1. **反事实经 `ladder_rr_enabled` toggle 实现，不另写门逻辑**：复用 lever2 既有开关 = 最小失真、与 live 代码零发散。`LADDER_ON={"ladder_rr_enabled": True}`(baseline 自检锚=live 现状) vs `LADDER_OFF={"ladder_rr_enabled": False}`(CF=TP1 地板)。

2. **scope 预过滤在分类前**：主桶 `regime_state=="choppy" AND tech_analysis.trend.direction=="neutral" AND decision 录值 action ∈ open_long`；旁路桶把 `choppy`→`mixed`。过滤用磁带录值（`regime_state` 顶层 + `tech_analysis.trend.direction`），不依赖 replay 输出。

3. **两臂分类 + baseline 自检闸**（镜像 ev-decouple `classify_accepts`）：对每条 scope 内 accept，先 `replay(LADDER_ON)`，若非 accept → `baseline_mismatch` 排除；再 `replay(LADDER_OFF)`，翻 reject → `tp1_floor_rejected`（避开桶），仍 accept → `survives_tp1_floor`（保留桶）。

4. **结算复用 ev-decouple 的 helper 形态**：`extract_settle_fields`(传 resolve 所需 `entry_price`/`created_at`/`side`/`stop_loss`/`take_profit`，**非原始 plan 的 `entry_ref`**——ev-decouple 的 Critical 教训)、`dedup_clusters`(symbol+side >1h 簇去重)、`settle_clusters`(klines_1s→klines fallback，TP1 保守 R：tp→+tp1/sl，sl→−1，expired→0)、`bucket_verdict`(min_sample=30)。

5. **delta 解读判据**：`tp1_floor_rejected` 桶净 R/簇 << 0 且**两桶诚实门均通过**时，才裁定「收紧对此原型 +EV」。否则薄样本只报 suggestive。real PnL 模糊 join lifecycle(matched only) 作次要 sanity。

6. **driver 命名 `cf_choppy_neutral_tp1_floor_ab.py`**（repo 根，与姊妹 driver 同目录同前缀）。

## Risks / Trade-offs

- **样本薄**：choppy+neutral 多单是 213 accept 的子集，主桶可能 < 30 → 诚实门 INSUFFICIENT_SAMPLE。可接受（如实报，mixed 旁路补样本，常驻数据累积后重跑）。
- **klines 覆盖受限**：klines_1s 近 ~数日 ~数十标的，更早簇无覆盖被跳过并计数——与姊妹 driver 同限，已如实标注。
- **toggle 副作用**：`ladder_rr_enabled=False` 在 replay 中也会让该决策的 sizing 走 TP1 口径——但低 R:R 缩仓本就用 `effective_rr_tp1`(fix-lever2-low-rr-sizing-tp1)，且本驱动只看 accept/reject 翻转与结算，sizing 不影响结论。
- **over-determination**：若某单同时被其它门(quality_gate/range_pos)在 baseline 就拦，baseline 自检会判非 accept 排除——不会误计入翻转。
- **观测非因果**：driver 只量化「若当时卡 TP1 地板的反事实结果」，不预测施加该门后市场/组合的级联——与全部 CF lab 产物同性质，诚实门 + baseline 自检是护栏。
```

## openspec/changes/cf-choppy-neutral-tp1-floor-ab/tasks.md

- Source: openspec/changes/cf-choppy-neutral-tp1-floor-ab/tasks.md
- Lines: 1-29
- SHA256: 9b1ddec5d455a903ebffe91ff51fe7358cfdd7c265b18fd63573a6045147baf6

```md
# Tasks: cf-choppy-neutral-tp1-floor-ab

## 1. 驱动骨架与加载

- [ ] 1.1 新建 `cf_choppy_neutral_tp1_floor_ab.py`（repo 根），module docstring 标 observability-only write-only + 红线，常量 `LADDER_ON={"ladder_rr_enabled": True}` / `LADDER_OFF={"ladder_rr_enabled": False}`、`TAPE`/`KL1`/`KL`/`LIFECYCLE` 路径。
- [ ] 1.2 `load_tape_accepts()`：读 `decision_replay_tape.jsonl`，过滤 `decision=="accept" AND replayable AND state_snapshot_before_decision`（镜像 ev-decouple）。
- [ ] 1.3 `scope_filter(records, regime)`：按 `regime_state==regime AND tech_analysis.trend.direction=="neutral" AND` 录值 action 为 open_long 过滤；主桶 regime=choppy，旁路 regime=mixed。

## 2. 两臂分类与自检闸

- [ ] 2.1 `classify_accepts(records, replay_fn=replay_decision)`：每条先 `replay(LADDER_ON)`，非 accept→`baseline_mismatch` 排除；再 `replay(LADDER_OFF)`，翻 reject→`tp1_floor_rejected`，仍 accept→`survives_tp1_floor`；返回三类 + mismatch 计数 + 翻转拒因 Counter。
- [ ] 2.2 `_reject_reason(decision)`：从 CF 臂 reject 决策取 blocked_by/reject_reason 首段（镜像 ev-decouple），确认翻转主因是 rr_below_floor。

## 3. 结算与诚实门（复用 ev-decouple helper 形态）

- [ ] 3.1 `extract_settle_fields(rec)`：从 plan 提 `side`/`entry_ref`/`stop_loss`/`take_profit` 算 `_sl_dist`/`_tp1_dist`，`_plan` 传 `entry_price`/`created_at`/`side`/`stop_loss`/`take_profit`（**非 `entry_ref`**，ev-decouple Critical 教训）；缺字段或非正距返回 None。
- [ ] 3.2 `dedup_clusters` / `load_bars`(klines_1s→klines fallback) / `settle_clusters`(TP1 保守 R) / `bucket_verdict`(min_sample=30 不下调) / `fuzzy_join_real_pnl`(matched only)。
- [ ] 3.3 `main()`：主桶 + mixed 旁路各跑 classify→settle→verdict，打印两桶（`tp1_floor_rejected` / `survives_tp1_floor`）簇数/结算/净 R/簇/诚实门裁定 + 解读判据注脚 + klines 覆盖限制注脚。

## 4. 红线守卫与测试

- [ ] 4.1 扩展 `tests/test_cf_red_line_guard.py`：新增 `test_decision_paths_do_not_read_choppy_tp1_floor_ab`，断言决策/风控模块源码不含 `cf_choppy_neutral_tp1_floor_ab`。
- [ ] 4.2 新增驱动单测（镜像 ev-decouple 测试）：`classify_accepts` 用 mock replay_fn 验证翻转/自检闸/mismatch 排除；`extract_settle_fields` 验证传 `entry_price`/`created_at` 而非 `entry_ref`（不全 mock resolve，集成 sanity）；scope_filter 验证 choppy/mixed+neutral 过滤。
- [ ] 4.3 全量 `python3 -m pytest -q` 绿（基线 1416 + 新测试），`compileall` 通过。

## 5. 真跑与结论

- [ ] 5.1 `python3 cf_choppy_neutral_tp1_floor_ab.py` 真跑，记录主桶/旁路两桶净 R/簇 + 诚实门裁定 + 翻转单数。
- [ ] 5.2 结论写入 verify 报告：是否「收紧对 choppy+neutral +EV」（仅诚实门通过时下结论），样本薄则标 suggestive + 常驻累积重跑；real PnL sanity join 对照。**不改 config、不上 live**——是否上 live 由后续 change 另议。
```

## openspec/changes/cf-choppy-neutral-tp1-floor-ab/specs/cf-choppy-neutral-tp1-floor-ab/spec.md

- Source: openspec/changes/cf-choppy-neutral-tp1-floor-ab/specs/cf-choppy-neutral-tp1-floor-ab/spec.md
- Lines: 1-61
- SHA256: 57b3828227bd44224a145621b6cafe29ef8eea43a4fc18c0a4d45b81ba113f44

```md
## ADDED Requirements

### Requirement: 两臂复盘量化 TP1 口径地板反事实

驱动 SHALL 对决策磁带 `decision=="accept"` 流做两臂复盘，量化「choppy+neutral 多单要求 TP1 口径 `effective_rr_tp1` ≥ 地板」相对 live 现状（lever2 阶梯口径地板）的反事实差异。baseline 臂 MUST 用 `replay_decision(rec, {"ladder_rr_enabled": True})`（= live 现状），CF 臂 MUST 用 `replay_decision(rec, {"ladder_rr_enabled": False})`（floor gate 改比 TP1 口径）。CF 臂相对 baseline 臂由 accept 翻 reject 的记录 SHALL 归入 `tp1_floor_rejected`（收紧后避开的单），仍 accept 的归入 `survives_tp1_floor`。

#### Scenario: choppy+neutral 多单被 TP1 地板拒掉
- **WHEN** 一条 scope 内 accept 记录 baseline 臂复现 accept，且 CF 臂(ladder off → TP1 口径)翻为 reject
- **THEN** 该记录归入 `tp1_floor_rejected` 桶，计入避开单统计

#### Scenario: 卡 TP1 地板仍过的单
- **WHEN** 一条 scope 内 accept 记录两臂均为 accept
- **THEN** 该记录归入 `survives_tp1_floor` 桶

### Requirement: baseline 复现自检闸

驱动 SHALL 对每条记录先验证 baseline 臂复现录值 accept；baseline 臂复盘结果非 accept 的记录 MUST 标 `baseline_mismatch` 并排除出翻转统计，不得计入任何结算桶。

#### Scenario: 复盘失真排除
- **WHEN** baseline 臂 `replay(ladder_rr_enabled=True)` 返回的 action 不是 open_long/open_short
- **THEN** 该记录计入 `baseline_mismatch` 计数并跳过，不进入 `tp1_floor_rejected`/`survives_tp1_floor`

### Requirement: scope 预过滤（主桶 + mixed 旁路）

驱动 SHALL 用磁带录值预过滤 scope，主桶 MUST 为 `regime_state=="choppy" AND tech_analysis.trend.direction=="neutral" AND` 录值 action 为 `open_long`；并 SHALL 额外报 `mixed`+neutral 多单旁路桶作对照。过滤 MUST 基于磁带录值，不依赖 replay 输出。

#### Scenario: 主桶过滤
- **WHEN** 加载磁带 accept 记录
- **THEN** 主桶只含 regime=choppy、trend.direction=neutral、action=open_long 的记录

#### Scenario: mixed 旁路对照
- **WHEN** 运行驱动
- **THEN** 输出含一个 regime=mixed+neutral 多单的旁路桶统计，与主桶并列展示

### Requirement: 统一 CF 结算与诚实门

两桶 SHALL 经 `resolve_counterfactual` + klines（`klines_1s.db` 优先、`klines.db` fallback）统一结算 TP1 保守净 R（tp→`+tp1_dist/sl_dist`、sl→`−1`、expired→`0`），结算字段 MUST 传 `resolve_counterfactual` 所需的 `entry_price`/`created_at`/`side`/`stop_loss`/`take_profit`（非原始 plan 的 `entry_ref`）。簇去重 MUST 按 (symbol, side) >1h 间隔取最早代表。诚实裁定 MUST 用 `cf_honesty_gate.summarize_bucket(min_sample=30)`，不得下调样本门槛。

#### Scenario: TP1 保守结算
- **WHEN** 一个簇代表用 klines resolve 出 outcome=tp
- **THEN** 该簇计 `+tp1_dist/sl_dist` R（不计阶梯 TP2/TP3 增益）

#### Scenario: 薄样本拒答
- **WHEN** `tp1_floor_rejected` 桶可结算簇 < 30
- **THEN** 诚实门返回 INSUFFICIENT_SAMPLE，净 R 仅作 suggestive，不下「收紧 +EV」结论

#### Scenario: 结算字段契约
- **WHEN** 从 accept 记录提取结算字段
- **THEN** 传入 resolve 的 dict 含 `entry_price`/`created_at`/`side`/`stop_loss`/`take_profit`，不传原始 plan 的 `entry_ref` 键

### Requirement: observability-only write-only 红线

驱动 SHALL 为 observability-only write-only：MUST NOT 下单、改 config、mutate live Judge/portfolio/cooldown/daily-stop 或 publish 真实总线。任何交易决策/风控路径（judge/executor/portfolio_risk_guard/reviewer/position_analyst）MUST NOT import 本驱动。

#### Scenario: 禁读守卫
- **WHEN** 运行 `tests/test_cf_red_line_guard.py`
- **THEN** 新增断言 `test_decision_paths_do_not_read_choppy_tp1_floor_ab` 验证决策/风控模块源码不含 `cf_choppy_neutral_tp1_floor_ab`，全部 PASS

#### Scenario: 不改 live
- **WHEN** 运行驱动
- **THEN** 不产生任何下单、不写 config.yaml、不修改任何 live 状态文件
```

