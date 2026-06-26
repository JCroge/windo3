---
comet_change: cf-neutral-momentum-rescue-ab
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-26-cf-neutral-momentum-rescue-ab
status: final
---

# cf-neutral-momentum-rescue-ab — 技术设计

> 需求事实源:`openspec/changes/cf-neutral-momentum-rescue-ab/`(proposal / design / specs)。本文只记 HOW。

## 背景与根因(确认)

体制空仓硬门 `Judge._classify_regime_flat_gate`(2026-06-25)的 path_evidence "救趋势" 阀门**双重失效**:

1. `_compute_directional_evidence` 的 `path_evidence_raw` 硬要求 `sym_dir=='bullish'`(`judge.py:2636`)。
2. 同时要求 `trend.strength >= 60`,而 `tech_analyst.py:192-200` 决定 `direction=='neutral'` 的 strength 计算为 `50 - |trend_pct-0.5|*60`(封顶 ~50),且 neutral 方向凑不齐三周期共振 +20(`tech_analyst.py:240-249`)→ **neutral 标的 strength 结构上到不了 60**。故 `strength>=60` 是 `sym_dir=='bullish'` 的隐式代理。

实证(决策磁带):上线以来 20 次 flat-gate 拦截**全部** `direction=='neutral'`,救援阀门从未触发。本 change 不改门,先**测量**放回这类 neutral 多单是救真趋势还是放假突破。

## Goals / Non-Goals

**Goals**:新增 observability-only 驱动 `cf_neutral_momentum_rescue_ab.py`,以**信号口径**量化"被误标 neutral 但有客观上行动量"的 choppy/mixed 多单候选的前向反事实净 R,A(谓词命中)vs B(对照)对比判别力,诚实门裁定。

**Non-Goals**:不改 `_classify_regime_flat_gate`/`_compute_directional_evidence`/`_has_directional_thesis`/`_select_rr_floor`;不碰 live/lever2/ev 解耦/短单门/TechAnalyst 标注;不下单、不改 config。

## 关键设计决策

### D1:信号口径,不用 replay-toggle 部署口径

**问题**:reject 记录不携带 plan(`trade_decision_output` 仅 reject_reason/attribution)。两条结算路:
- **A 部署口径**:`replay_decision` 用现有 `regime_flat_gate_enabled` toggle(`decision_replay.py:235` 已原生支持),门关后翻 accept 取生成的真实 plan。**否决为主**:实跑验证撞上已知"过度确定"——单关 flat gate,候选仍被 rr_below_floor/quality_gate/range_position 联合拒(实跑 TRUMP FLAT_ON/OFF 皆 hold;memory「accept 恒 21」「CF opens 恒 2」佐证),样本注定 < 30。
- **B 信号口径(采用)**:对 tape 字段直接筛 population + 合成标准化退出结算,**不实例化 Judge**,更纯的 observability,样本大,直接回答信号 edge。

### D2:population = choppy/mixed + direction==neutral 全体

不限于 flat-gate-rejected(那会因过度确定缩样本)。accept+reject 皆纳入,均按假设做多 + 同一合成退出结算,口径一致。

### D3:谓词方向无关 + A/B 判别对照

- A 桶:`(daily_bias=='bullish' OR higher_tf_bias=='bullish')` AND `pre_12h_return_pct >= pre12h_min` AND `position_in_24h_range <= range_pos_max`。
- B 桶:同 population 不命中 A。
- **MUST NOT 引用 `trend.strength`**(代理根因)。
- 判据:A 净 R 显著正 + B 不显著正 → 谓词有判别力;否则救援无 edge。

### D4:标准化合成退出 = 策略典型几何

`entry = price_at_decision`,`side=long`,`sl/tp` 从磁带 choppy-long **accept 流**取 median `sl_dist`/`tp ladder`(让合成单 R-multiple 与真实交易可比)。A、B 同几何。报 ≥2 组退出假设(策略中位 / 固定 R:R=1.5 / 更紧 SL)+ 阈值网格 `pre12h_min∈{0.02,0.03,0.05}×range_pos_max∈{0.85,0.92}` 敏感性,不写死单点。

### D5:复用既有结算栈

`resolve_counterfactual`(TP1 保守、SL-first 同根)+ `klines_1s.db` + 簇去重(symbol,side,>1h)+ `cf_honesty_gate.summarize_bucket(min_sample=30 不下调)`。镜像 `cf_choppy_neutral_tp1_floor_ab.py` ~80% 结构。CF 契约传 `entry_price`/`created_at`/`side`/`stop_loss`/`take_profit`(非 `entry_ref`)。

## 数据流

```
decision_replay_tape.jsonl (replayable)
  → population: regime∈{choppy,mixed} & direction==neutral
  → 分桶 A(谓词命中)/ B(对照)
  → 合成退出(entry=price_at_decision, sl/tp=策略中位几何)
  → resolve_counterfactual + klines_1s (TP1 保守, SL-first)
  → 簇去重(symbol,side,>1h)
  → summarize_bucket(min_sample=30) × {A,B} × 阈值网格 × 退出假设
  → 报告: A vs B 净R/簇 + 命中数 + 敏感表 + 诚实门裁定 + 结论建议
```

## 风险 / 取舍

- [合成退出非策略实盘 plan] → 用策略典型几何 + 多组假设;signal 口径本就该标准化退出测信号而非退出策略,且 A/B 同口径偏差抵消。
- [klines_1s 覆盖有限(近数日/数十标的)] → 无覆盖簇跳过并计数。
- [被误读为支持放回 neutral 多单] → observability-only + 红线守卫 import 拦截 + 报告显式声明。
- [谓词阈值挑得巧合致 A 假正] → 报阈值网格敏感性 + B 对照判别,孤立尖峰不采信。
- [population 含 accept(已开单)混入] → accept 极少且同一合成退出口径处理,不引入偏差;报命中数透明。

## 测试策略

谓词单测(命中/bearish 不进/趋势体制不进/daily+htf 皆非 bullish 不进/不读 strength)· 合成退出几何(sl_dist≤0 跳过)· CF 契约字段 · 诚实门薄样本拒答 · 红线守卫 import 断言 · 全量 `pytest -q` 绿(基线 1460 + 新增)。

## 部署 / 回滚

无 live 部署。新增驱动 + 测试,`compileall` + `pytest` 绿即可。无运行时行为变更,无回滚需求。
