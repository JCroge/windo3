---
comet_change: trend-entry-rr-fidelity
role: technical-design
canonical_spec: openspec
---

# trend-entry-rr-fidelity 技术设计

> 需求事实源 = OpenSpec delta spec(`specs/trend-aligned-rr-floor`、`specs/ladder-weighted-rr`)。本文档只描述 HOW,不重定义 WHAT。

## 范围(本 change)

- 杠杆① **P1**:`_select_rr_floor` 增加「客观路径证据」OR 分支,使干净趋势拿到对齐地板。
- 杠杆② **v1**:`_build_plan` 的 `effective_rr` 改阶梯加权 + 保守固定先验概率。
- 全样本(含亏单)A/B + config 灰度。

**拆出本 change**:① P2(bias 上游根治)、② v2(到达概率频率校准)各起新 change。

## 架构

两个杠杆都在 `agents/trading/judge.py`,各自独立 config 开关,互不依赖,可分别 A/B。

```
_build_plan(tech, action, price, confidence, score)
  ├─ 杠杆②: effective_rr 计算 ← 阶梯加权(开关 ladder_rr_enabled)
  └─ _select_rr_floor(action, plan, tech, score)
       └─ 杠杆①: long_aligned 判定增 OR「客观路径证据」分支(开关 path_evidence_aligned_enabled)
```

## 杠杆① — 客观路径证据(P1)

现有 `long_aligned` 条件:
`sym_dir==bullish AND (htf_bias OR daily_bias == bullish) AND not block_long AND |score|>=min_deferred_score`

改为 `(原 bias 条件) OR (客观路径证据)`。客观路径证据 **MUST 全部使用入场前数据(禁前视)**:

| 证据项 | 来源(事前) | 含义 |
|--------|------------|------|
| 近窗方向一致性 | 近 N 根净收益(如 `pre_12h_return` 同号且幅度≥阈值) | 趋势方向明确 |
| 近窗浅回撤 | 近窗最大回撤 ≤ k·ATR | 路径干净,非深震荡 |
| 延展未过热 | `position_in_24h_range` 不在极端追高区 | 防接力顶部 |

满足 → 授 `rr_floor_long_aligned_choppy`(1.30 级),`rr_policy='long_aligned_path_evidence'`,`rr_floor_reason` 记命中证据项。
真 choppy(方向反复 / 深回撤)不满足 → 仍落 default(1.50)。

对称 short 逻辑预留接口,本期可只启用 long(short 趋势盈利样本薄,见记忆 cf 诊断)。

阈值(N、幅度、k、过热区)走 config,默认值在实现时按历史分布标定一个保守起点,最终由回测确定。

## 杠杆② — 阶梯加权 effective_rr(v1)

现状(`judge.py:3429-3433`)只用 `take_profit[0]`。改为按 executor 真实阶梯(50/25/25)**离场比例**加权(Option B,定稿):

```
w = [0.50, 0.25, 0.25]             # 对齐 executor.py:1354 的真实离场比例
# 各档盈利距离(剩余 trailing 档保守封顶 +1R,不记最远档满额)
dist_i = tp_dist_i                 for i in {TP1, TP2}
dist_3 = min(tp_dist_3, sl_dist)   # 剩余档至多记 +1R 锁利
exp_profit   = Σ w_i * notional * dist_i
effective_rr = (exp_profit - total_cost) / (gross_loss + total_cost)   # 成本扣法不变
```

**关键修正(2026-06-17 build 期)**:初版叠加 P(reach tierᵢ)=[1.0,0.5,0.25] 概率折扣,实测把 HYPE effective_rr 从 1.14 **反向压到 0.86**——因为只缩了收益分子,却保留满仓风险分母(阶梯化后 TP1 平 50%+SL 移保本,真实风险已降)。旧 TP1-only 公式本就隐含"TP1 必达(P=1)",故 v1 **不再单独加概率折扣**,与旧口径同假设,只把离场分布从"虚构 100%@TP1"修正为真实 50/25/25 + 剩余封顶 +1R。HYPE:1.14→**1.34**(过经杠杆①的 1.30 地板)。相干的"概率折扣 + 风险分母同步降低"口径需真实到达概率,拆入 v2。

- 缺档(TP 不足 3 档)→ 权重归一化到现有档,缺失档贡献 0。
- config 开关 `ladder_rr_enabled` 关闭 → 回退现有 TP1-only。
- 决策记录同时写 `effective_rr`(旧)与 `effective_rr_ladder`(新),便于回测对照与可观测。

## 回测与测试策略

**验证工具(build 期 pivot,定稿)**:用 **CF 重放实验室**(`utils/sequential_perturbation` / `utils/knob_sweep`,跑真实 judge 决策代码,把两个 flag 当旋钮)做 A/B,而非 `event_backtest`。原因:`event_backtest` 是策略的**简化重实现**(自算 floor/score),要 A/B 两个杠杆得在里面重写一遍、与真 judge 口径可能发散;CF 实验室直接行使上线代码,最忠实。**关键前置**:新 flag 须注入 `utils/decision_replay.py::_install_config_flags`,否则 replay 用 `getattr` 兜底致旋钮无效、A/B 假阴性。

- **CF 重放四臂 A/B**(`cf_rr_fidelity_ab.py`):baseline vs ①、②、①+②,回放 `decision_replay_tape`。**结果 inconclusive**:lever1 目标人群(干净趋势+中性 bias)在该磁带为空;lever2 旋钮生效(divergence 0.66)但 CF 退出建模粗(无阶梯)+ 组合 slot/EV 只开 2 仓 → delta 0。见 `ab_result.md`。
- **lever2 忠实 A/B**(`cf_lever2_rejected_ab.py`):在 lever2 真实目标人群所在的 `rejected_signal_events` 流上,反推成本率算 ladder effective_rr 判 flip → 趋势簇去重 → klines `resolve_counterfactual` 含亏单结算。**结果:81% 翻转、72 可结算簇胜率 53.8%、含亏单净 +0.21R/簇(保守 TP1 口径,真实阶梯更高)——正面但样本薄(13% 覆盖/近 3 天),够小额灰度不够全量,本期保持默认关。**
- **lever1 验证**:两条流均无法忠实验证(磁带无目标人群;rejected 流原缺 tech 输入)→ 本期只加 `tech_context` 埋点,实际 A/B 拆新 change 待数据累积。
- **单元测试**:
  - ①:干净趋势授对齐地板 / 真 choppy 不误授 / 开关关闭行为不变 / 反前视 / 低 R:R 风控接线(`low_rr_policies` 含新 policy)。
  - ②:阶梯加权 ≥ **真实 TP1-only 口径**(各档正贡献,守不反向)/ 剩余封顶 +1R / 开关回退 / 缺档归一化 / 旋钮注入生效。
  - 埋点:被拒记录含 `tech_context` / 缺 tech fail-safe 空 dict。

## Spec Patch(已回写 OpenSpec delta spec)

- `ladder-weighted-rr`:「各档到达概率」放宽为 v1 允许**保守固定先验**(文档化、可辩护、MUST NOT 全=1),v2 频率校准拆出本 change。
- `trend-aligned-rr-floor`:补 scenario 明确**客观证据为入场前数据、禁前视**。

## 风险 / 取舍

- 路径证据阈值过松 → 放进低质量入场:全样本回测胜率守门。
- v1 保守先验偏严 → 仍漏部分机会:可接受,v2 校准回补。
- 仅 judge.py 改动 + config 灰度,不动 executor 离场比例、不降地板数值、不直接全量上线。
