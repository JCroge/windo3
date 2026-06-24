## ADDED Requirements

### Requirement: 两臂复盘量化 TP1 口径地板反事实

驱动 SHALL 对决策磁带 `decision=="accept"` 流做两臂复盘，量化「choppy+neutral 多单要求 TP1 口径 `effective_rr_tp1` ≥ 地板」相对 live 现状（lever2 阶梯口径地板）的反事实差异。baseline 臂 MUST 用 `replay_decision(rec, {"ladder_rr_enabled": True})`（= live 现状），CF 臂 MUST 用 `replay_decision(rec, {"ladder_rr_enabled": False})`（floor gate 改比 TP1 口径）。CF 臂相对 baseline 臂由 accept 翻 reject 的记录 SHALL 归入 `tp1_floor_rejected`（收紧后避开的单），仍 accept 的归入 `survives_tp1_floor`。

#### Scenario: choppy+neutral 多单被 TP1 地板拒掉
- **WHEN** 一条 scope 内 accept 记录 baseline 臂复现 accept，且 CF 臂(ladder off → TP1 口径)翻为 reject
- **THEN** 该记录归入 `tp1_floor_rejected` 桶，计入避开单统计

#### Scenario: 卡 TP1 地板仍过的单
- **WHEN** 一条 scope 内 accept 记录两臂均为 accept
- **THEN** 该记录归入 `survives_tp1_floor` 桶

#### Scenario: 翻转归因纯度（只计 rr_below_floor）
- **WHEN** 一条记录 CF 臂翻为 reject，但 reject 原因不是 `rr_below_floor`
- **THEN** 该记录归入 `other_flip` 桶并在报告中标出，MUST NOT 计入 `tp1_floor_rejected` 或其结算——只有 reject 原因为 `rr_below_floor` 的翻转才归 `tp1_floor_rejected`

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
