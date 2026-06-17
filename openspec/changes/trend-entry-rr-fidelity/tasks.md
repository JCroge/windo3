<!-- 范围:① P1 客观路径证据 + ② v1 保守先验阶梯加权。P2(bias 根治)/v2(频率校准)拆出本 change。 -->

## 0. 前置(已基本厘清,留作核对)

- [x] 0.1 核对 `event_backtest` 阶梯建模与 executor 差异:回测为 50%@TP1+trailing,executor 为 50/25/25;登记 TP2 折进 trailing 的小保真差,本期接受
- [x] 0.2 确认入场前可得特征清单(pre_12h_return / 近窗回撤 / position_in_24h_range / tech.trend),供①客观证据使用,确保无前视

## 1. 杠杆① trend-aligned-rr-floor(P1 客观路径证据)

- [x] 1.1 定义客观路径证据判据(近窗方向一致性 + 近窗浅回撤≤k·ATR + 延展未过热),全部用入场前数据
- [x] 1.2 在 `judge.py:_select_rr_floor` 给 long_aligned 加 `OR 客观证据` 分支,授 1.30 级地板,rr_policy='long_aligned_path_evidence',记命中证据项
- [x] 1.3 加 config 开关 `path_evidence_aligned_enabled`(默认关)+ 阈值参数
- [x] 1.4 单元测试:干净趋势授对齐地板 / 真 choppy 不误授 / 开关关闭行为不变 / **反前视断言**

## 2. 杠杆② ladder-weighted-rr(v1 保守先验)

- [x] 2.1 实现阶梯加权 effective_rr:w=[.5,.25,.25]、P=[1.0,.5,.25] 保守先验、剩余档 +1R 锁利保守口径、成本扣法不变、缺档归一化
- [x] 2.2 决策记录并存 `effective_rr`(旧)与 `effective_rr_ladder`(新)+ 所用概率,可观测
- [x] 2.3 加 config 开关 `ladder_rr_enabled`(默认关),关闭回退 TP1 口径
- [x] 2.4 单元测试:阶梯加权≥TP1口径 / 远档低概率不注水 / 剩余保守 / 开关回退 / 缺档归一化

## 3. 全样本 A/B 与背书

- [x] 3.1 杠杆① 在 event_backtest 全样本(含亏单)A/B,产出净 PnL/胜率/MDD delta
- [x] 3.2 杠杆② 在 event_backtest 全样本(含亏单)A/B,产出 delta
- [x] 3.3 ①+② 合并 A/B,确认净 PnL 改善且胜率不被低质量入场显著稀释,形成背书结论

## 3b. CF 实验室 A/B(已做,结论 inconclusive)

- [x] 3b.1 CF 重放四臂 A/B(decision_replay_tape);新 flag 注入 _install_config_flags
- [x] 3b.2 诚实诊断:lever1 目标人群空、lever2 CF 退出不建模阶梯 → 不予采信,见 ab_result.md

## 5. 忠实 harness 扩展(用户授权扩范围)

- [x] 5.1 lever1 埋点:`_record_rejected_plan` → ledger record 加 `tech_context`(direction/strength/htf_bias/daily_bias/entry_context),observability-only/additive/fail-safe
- [x] 5.2 埋点单测:被拒记录含 tech_context;缺 tech 缓存 fail-safe 空 dict 不破记录
- [x] 5.3 lever2 忠实 A/B 驱动 `cf_lever2_rejected_ab.py`:回放 rejected_signal_events,重算 ladder effective_rr → 判定过/不过 reject_reason 隐含地板 → 趋势簇去重 → resolve_counterfactual+klines 出含亏单 CF 净 PnL
- [x] 5.4 跑并把结论写入 ab_result.md(lever2 段);按净期望决 lever2 灰度

## 4. 灰度与收尾

- [ ] 4.1 按背书结论配置 config 灰度(默认关或小灰度),不直接全量
- [ ] 4.2 全量回归测试零回退;更新相关文档/记忆
- [ ] 4.3 登记后续拆出 change:① P2 bias 上游根治、① lever1 A/B(待埋点数据)、② v2 到达概率频率校准
