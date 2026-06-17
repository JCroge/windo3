## Context

入场 gate(`agents/trading/judge.py`)对干净趋势零开仓的根因已在 explore 阶段定位到两处口径失配:

- **杠杆①（地板选择）**:`_select_rr_floor` 已有 `long_aligned`(choppy/mixed 下 long 趋势对齐 → 1.30 地板)路径,但其判定要求 `sym_dir==bullish AND (htf_bias OR daily_bias == bullish) AND not block_long AND |score|>=45`。实战中 HYPE/UNI 全程走 `default`(1.50)、WLD 仅 6/145 命中 aligned,说明 HTF/日线 bias 未识别出价格上明显的趋势。
- **杠杆②（R:R 口径）**:`_build_plan` 用 `take_profit[0]` 单档算 `effective_rr`,而 executor `_update_trailing` 实际执行 50% @TP1 / 25% @TP2 / 剩余 trailing 的阶梯离场(`executor.py:1354` `pct = 0.5 if partial_tp_1 else 0.25`)。gate 口径系统性低于真实离场策略。

现状数据基线:近三天被拒 1813 个计划,64% rr_below_floor / 30% quality_gate;4 个干净趋势(逆行 0.1–0.3R,峰值 1.9–9.5R)零开仓。

## Goals / Non-Goals

**Goals:**
- 让明确干净的趋势能拿到趋势对齐地板,不被误落 default。
- 让 `effective_rr` 口径对齐 executor 真实阶梯离场,且**乘各档到达概率折扣 + 剩余仓位保守口径**,不注水。
- 两杠杆均可在 `event_backtest` 上对**全样本(含亏单)** A/B,以净 PnL/胜率/MDD delta 背书。

**Non-Goals:**
- 不降低 R:R 地板数值(反事实实验室已证伪;地板值不在本 change 内调整)。
- 不改 executor 的离场比例(50/25/25 视为既定事实,本 change 让 gate 口径去对齐它,而非反过来)。
- 不直接全量上线;config 灰度 + 回测背书优先。

## Decisions

> 以下为高层架构决策与待 brainstorming 锁定的开放项。具体 HOW(概率标定方法、对齐判据补强方式)在 comet-design 的 brainstorming 阶段锁定。

1. **两个 capability 分离**:`trend-aligned-rr-floor`(①)与 `ladder-weighted-rr`(②)各自独立、可单独 A/B,便于隔离归因与按需拆 change。
2. **杠杆①方向**:优先修正/补强趋势对齐判据来源(HTF/日线 bias 为何对干净趋势返回 neutral),而非简单放宽阈值——避免把真 choppy 也放进趋势地板。判据可叠加"低逆行/路径干净度"等客观证据。【开放:补强 bias 信号 vs 增加客观路径证据,brainstorming 定】
3. **杠杆②口径**:`effective_rr = (Σ wᵢ·P(reach tierᵢ)·profitᵢ·notional − cost) / (max_loss + cost)`,wᵢ=[0.5,0.25,0.25] 对齐 executor;剩余 25% trailing 仓位 profit 用保守口径(+1R 锁利或 trailing 期望下界)。【开放:P(reach tierᵢ) 标定方法——历史磁带频率 vs 模型,brainstorming 定】
4. **回测前置**:确认 `event_backtest` 是否已建模阶梯离场;若仅 SL/TP 单档,先补阶梯离场建模,否则无法测出②的真实净效果。这是②的硬前置。
5. **灰度护栏**:新口径经 config 开关,默认走回测/灰度;与反事实实验室口径一致,observability 优先。

## Risks / Trade-offs

- **注水风险(②)**:若 P(reach tierᵢ) 估计偏乐观或剩余仓位记满档,会把虚高 R:R 喂给 gate,自欺。缓解:概率折扣 + 保守剩余口径为硬要求,全样本(含亏单)回测净效果背书。
- **幸存者偏差**:4 个趋势是赢家样本;放宽入场会同时放进同期 461 个 naive SL。缓解:回测必须全分布,不能只看趋势赢家。
- **杠杆①过放宽**:把真 choppy 误授趋势地板 → 引入低质量入场。缓解:对齐判据叠加客观证据,回测验证胜率不被稀释。
- **前置依赖(④)**:event_backtest 若未建模阶梯离场,工作量上浮且是②的阻塞前置;可能触发 change 拆分。
- **概率数据缺失**:per-tier 到达概率无现成数据,需先标定,标定样本量/时效性影响可信度。
