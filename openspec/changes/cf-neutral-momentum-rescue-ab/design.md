## Context

体制空仓硬门 `Judge._classify_regime_flat_gate`(2026-06-25)的 path_evidence 救援阀门设计意图是救回"被误标 neutral 但真在走上行"的趋势单,实际**双重失效**:

- `_compute_directional_evidence` 的 `path_evidence_raw` 硬要求 `sym_dir=='bullish'`;
- 同时要求 `trend.strength >= 60`,而 `tech_analyst.py:192-200` 决定 `direction=='neutral'` 的 strength 封顶 ~50(neutral 凑不齐三周期共振 +20)→ `strength>=60` 是 `bullish` 的隐式代理。

上线以来 20 次拦截全 `direction=='neutral'`,阀门从未触发。本 change 不修门,先**测量**"放回这类 neutral 多单"在 choppy/mixed 体制下的前向反事实期望,判定救援是否有 edge。属本项目 "先测量后改 live" 惯例的测量步,与 `cf-choppy-neutral-tp1-floor-ab` 同性质。

**约束**:observability-only;`tests/test_cf_red_line_guard.py` 守卫决策/风控路径禁 import CF/测量产物;复用既有 `resolve_counterfactual` + `cf_honesty_gate`;决策磁带 `decision_replay_tape.jsonl` + `klines_1s.db` 为只读数据源。

## Goals / Non-Goals

**Goals:**
- 新增 `cf_neutral_momentum_rescue_ab.py` 驱动,量化救援候选单的前向反事实净 R/簇,诚实门裁定。
- 用**方向无关**信号(daily/htf bias + 真实 12h 涨幅 + 区间位置)定义救援候选谓词,绕开被误标的 1h direction 与 strength 代理。
- 产出可信结论:救援是否有 edge → 决定是否起后续改门 change。

**Non-Goals:**
- 不改 `_classify_regime_flat_gate` / `_compute_directional_evidence` / `_has_directional_thesis`。
- 不改 `_select_rr_floor` 的 `path_evidence_raw`(floor-grant 消费,须零回归)。
- 不碰 live / lever2 / ev 解耦 / 短单门 / TechAnalyst direction·strength 标注。
- 不下单、不改 config、不自动改任何阈值。

## Decisions

**D1: 数据源用决策磁带 + resolve_counterfactual(而非 event_backtest 为主)。**
理由:磁带记录的是 live 真实决策时点的 tech 快照(direction/daily_bias/htf_bias/pre_12h_return_pct/position_in_24h_range 全有,已实证可读),保真度高于 event_backtest 的历史重算;且镜像现有 `cf_choppy_neutral_tp1_floor_ab.py` / `cf_ev_decouple_ab.py` 基础设施,复用 `resolve_counterfactual` + klines TP1 保守结算。event_backtest 作为样本不足时的交叉验证补充(design 阶段定)。
*备选*:event_backtest 为主——历史样本大但 regime/entry_context 保真低,且无法对齐 live 体制判定。否决为主、保留为辅。

**D2: 救援候选谓词全部方向无关。**
`effective_regime ∈ {choppy, mixed}` AND `direction=='neutral'` AND `(daily_bias=='bullish' OR higher_tf_bias=='bullish')` AND `pre_12h_return_pct >= 阈值` AND `position_in_24h_range <= 阈值`。
理由:阀门失效的根因正是依赖了 direction 与其代理 strength;救援证据必须建立在不受 1h direction 标签污染的客观信号上。`strength` 明确**不用**。
*备选*:沿用 strength>=60——已证明对 neutral 恒不触发,无意义。否决。

**D3: 阈值参数化、不写死、不调 live。**
`pre_12h_return_pct` / `position_in_24h_range` 阈值作驱动参数(可与 `path_evidence_raw` 现值 0.03 / 0.92 对齐作基准),报多组取值的敏感性,不挑一个塞回代码。

**D4: 诚实门 min_sample=30 不下调,薄样本 INSUFFICIENT 拒答。**
与所有 cf 驱动一致;suggestive 读数只记录方向、不作改门依据。

## Risks / Trade-offs

- [磁带样本不足,诚实门拒答] → 接受;记录 suggestive 方向 + 装周更 cron 累积(同 `cf-choppy-neutral-tp1-floor-ab`),或 event_backtest 交叉验证补样本(design 阶段评估)。
- [CF 结算口径乐观高估救援 edge] → 用 TP1 保守口径 + SL-first 同根冲突,与现有驱动一致;两桶同口径,系统性偏差在对比中抵消。
- [谓词选得太宽/太窄,结论失真] → 报谓词命中数 + 阈值敏感性多组,而非单点;命中数过低显式标注。
- [被误读为"已支持放回 neutral 多单"] → proposal/design/README 显式声明 observability-only、不改门;红线守卫测试机器拦截 import。
- [klines_1s 覆盖有限(近数日/数十标的)] → 无覆盖簇跳过并计数,与现有驱动一致。

## Migration Plan

无 live 部署。新增驱动 + 测试,`pytest` 全绿即可。运行驱动产出报告供人审。无回滚需求(不改任何运行时行为)。

## Open Questions

- 救援桶的"参照桶"取什么最有说服力:同体制下已被放行的 aligned 多单?还是"满足谓词但 pre12h 不达标"的近邻单?→ comet-design 阶段定。
- 是否需要配套 forward-shadow recorder(若磁带历史命中过少)→ 视 design 阶段命中数评估,默认先只跑磁带回放。
