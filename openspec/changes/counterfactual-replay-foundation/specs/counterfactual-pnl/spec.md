## ADDED Requirements

### Requirement: 被拒单反事实 PnL 用真实成本模型
系统 SHALL 用真实成本模型（手续费 + 资金费）计算被拒单假设成交的 USDT 净 PnL，复用 executor 既有 `CostModel`，不重写成本逻辑。

#### Scenario: 净 PnL 扣成本
- **WHEN** 一个被拒单的影子结果被解析
- **THEN** 输出 SHALL 为扣除手续费（与持仓时长相关的资金费，若纳入）后的真实 USDT 净 PnL，而非到价毛%

#### Scenario: 复用 CostModel 不发散
- **WHEN** 成本计算执行
- **THEN** 系统 SHALL 调用 executor 同一 `CostModel`，不存在第二份成本公式实现

#### Scenario: 资金费近似标注
- **WHEN** 计算被拒单净 PnL 纳入资金费
- **THEN** 系统 SHALL 用决策时点 `funding_rate` 当持仓期常数近似，并把结果标注 `funding=approximated`，不假装逐 8h 精确

### Requirement: K 线 SL/TP 触发判定与 SL-first 保守假设
系统 SHALL 用 K 线 high/low 判定被拒单 SL/TP 是否触发；当同一根 K 线同时触及 SL 与 TP 时，SHALL 保守取 SL-first，并将该笔标记为价格精度不确定。

#### Scenario: 单边触发
- **WHEN** 某 K 线仅 high 触及 TP 或仅 low 触及 SL（long 视角）
- **THEN** 系统 SHALL 判定该单边结果，记录触发价与时间

#### Scenario: 同根冲突保守取 SL
- **WHEN** 同一根 K 线 high 触 TP 且 low 触 SL
- **THEN** 系统 SHALL 取 SL-first（保守下界），并把该笔计入偏差带（结果不确定）

### Requirement: 价格精度偏差带量化
系统 SHALL 在汇总输出中量化"因价格精度不可判而保守处理"的样本占比与其对净 PnL 的影响范围（偏差带）。

#### Scenario: 偏差带随报告输出
- **WHEN** 生成被拒单反事实汇总
- **THEN** 报告 SHALL 含 SL-first 保守笔数、占比，以及"若取 TP-first 上界"的 PnL 区间

### Requirement: 数据来源标注不混用
系统 SHALL 标注每条反事实结果的来源（`attribution_reconstructed` 旧数据重算 vs `tape_exact` 磁带精确回放），二者不混用、不互相覆盖。

#### Scenario: 来源可区分
- **WHEN** 汇总同时含旧 `rejected_signal_events.jsonl` 重算与新磁带数据
- **THEN** 每条结果 SHALL 带 `source` 标签，报告可按来源分组

### Requirement: 诚实性 gate — 三档样本 + Wilson/bootstrap 区间
系统 SHALL 经单一报表层函数对所有方向/PnL 结论计算样本量与置信区间：胜率用 Wilson score 区间，净 PnL 用 bootstrap 重采样区间；并按三档样本量分级输出，薄样本拒答。

#### Scenario: 薄样本拒答 (n<30)
- **WHEN** 某分桶（如某 gate × regime）样本量 < `CF_MIN_SAMPLE`（默认 30）
- **THEN** 系统 SHALL 输出 `INSUFFICIENT_SAMPLE — 不准动`，不给净 PnL 方向结论

#### Scenario: 中样本 low_confidence (30≤n<100)
- **WHEN** 样本量在 `CF_MIN_SAMPLE` 与 `CF_LOWCONF_SAMPLE`（默认 100）之间
- **THEN** 输出 SHALL 含 Wilson 胜率区间 + bootstrap 净 PnL 区间，并标 `low_confidence`，不判 actionable

#### Scenario: 足量且区间不跨 0 才 actionable (n≥100)
- **WHEN** 样本量 ≥ `CF_LOWCONF_SAMPLE` 且 bootstrap 净 PnL 区间不跨 0
- **THEN** 输出 SHALL 标 `actionable` 并给方向；若区间跨 0，SHALL NOT 标 actionable

#### Scenario: bootstrap 暴露单笔主导
- **WHEN** 某分桶净 PnL 主要由极少数交易贡献（如单笔 ADA 主导）
- **THEN** bootstrap 区间 SHALL 反映该脆弱性（宽区间/跨 0），不被点估计掩盖

#### Scenario: 单点收口
- **WHEN** 任意报表/汇总路径需要给出统计结论
- **THEN** 其 SHALL 调用同一诚实性 gate 函数，不在调用点重写样本/区间判定
