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
