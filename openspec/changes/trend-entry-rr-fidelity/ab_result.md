# trend-entry-rr-fidelity 全样本 A/B 结果(CF 重放实验室)

> 2026-06-17。工具:`cf_rr_fidelity_ab.py`(`utils/sequential_perturbation.build_delta_report`,跑真实 judge 决策代码)。磁带:`data/decision_replay_tape.jsonl`,1357 条 v2+tech 可回放。baseline_fidelity = **0.9624**(≥0.85,实验室可信)。

## 原始四臂结果

| 臂 | knobs | divergence | CF开仓(base→pert) | net_pnl delta | win_rate delta | MDD delta |
|---|---|---|---|---|---|---|
| lever1_only | path_evidence_aligned_enabled | **0.0000** | 2→2 | +0.00 | +0.00 | +0.00 |
| lever2_only | ladder_rr_enabled | **0.6563** | 2→2 | +0.00 | +0.00 | +0.00 |
| lever1+2 | both | 0.6563 | 2→2 | +0.00 | +0.00 | +0.00 |

## 诚实诊断(关键:结论是「不可采信」而非「无效」)

### lever1(path-evidence 地板):divergence=0.0 是**真实 null,但目标人群为空**
- 直接核验:磁带 1357 条里,满足 path-evidence 核心条件(bullish dir + strength≥60 + pre12h≥0.03 + range_pos≤0.92)且 **现有 aligned 条件失败(bias 中性)** 的记录 = **0**。
- 即:所有满足路径条件的记录(120 条:UNI/ADA/BCH/NEAR…)**都已带 bullish HTF/日线 bias**,现有 `long_aligned` 分支已授 1.30 地板,lever1 无可加之处。已实测确认 lever1 实现正确(条件满足且 `not aligned` 时确实改 floor),divergence=0.0 不是 bug。
- **但 lever1 的目标人群(干净趋势 + 中性 bias)在本磁带里不存在**。早先诊断的 HYPE/WLD/UNI「中性 bias + 干净趋势被拒」证据在 `rejected_signal_events.jsonl`(另一条 CF 实验室**不回放**的捕获流)。→ **CF 实验室这条磁带无法验证 lever1**。

### lever2(阶梯 effective_rr):active 但 CF 不增仓、delta=0
- divergence=0.656 → lever2 确实在改 effective_rr,翻转 ~66% 决策 gate-label(证明旋钮真生效)。
- 但 CF 开仓恒 2、PnL delta=0 → 翻转的决策没转化为额外开仓。这复现先前 joint-knob-sweep 的结论:被拒决策被**多门过度决定**,改 R:R 门只是 reject→其它 reject 级联。
- **更重要的工具局限**:lever2 的价值本在「阶梯离场吃到 TP2/TP3/trailing」,而 CF 实验室退出建模是**粗粒度 SL/TP/24h、不含阶梯**。即便 lever2 放更多仓进来,CF 也照不出阶梯收益。→ **CF 实验室结构上无法度量 lever2 的真实增益**。

## 结论:A/B **不予采信**(inconclusive),不是绿灯也不是红灯

CF 重放实验室在当前磁带上:
1. **lever1**:目标人群为空(干净趋势+中性 bias 不在本磁带;在 rejected_signal_events 流)→ 未测到。
2. **lever2**:旋钮生效但 CF 退出不建模阶梯 + 开仓被其它门过度决定 → 无法度量真实增益。

**两个杠杆都未被这套 A/B 证伪,也都未被证实。** 因此:
- **两个 config 开关保持默认关**,不进灰度。
- 实现本身安全可留:默认关、开关关闭字节级等价、低 R:R 风控已接线、单测齐全、1281→(Task6 复核)零回退。

## 下一步(忠实验证需要)
- **lever1**:让 A/B 回放 `rejected_signal_events.jsonl`(lever1 目标人群所在),或在录制磁带里确认中性-bias 干净趋势的捕获覆盖。
- **lever2**:需要含 **50/25/25 阶梯+trailing 退出建模**的回测(event_backtest 已建模阶梯,但需把两杠杆按真实 judge 口径接进去;或给 CF 实验室加阶梯退出)。
- 在忠实工具产出「净 PnL delta≥0 且胜率不稀释」前,**不开启灰度**。
