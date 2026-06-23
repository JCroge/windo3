## ADDED Requirements

### Requirement: 历史 OHLC 幂等抓取
系统 SHALL 提供带分页的历史 OHLC 抓取器,支持多币种、多周期(至少 1d 与 4h),并以 `UNIQUE(symbol,interval,open_time)` 落入 `data/klines.db`,重复运行 MUST 幂等(不产生重复行)。

#### Scenario: 抓取日线并落库
- **WHEN** 对给定币种列表以 interval=1d 运行抓取器
- **THEN** 系统分页拉取全部可得历史(单次上限 1000 根)并写入 `data/klines.db`,interval 字段记为 `1d`

#### Scenario: 重复运行幂等
- **WHEN** 对同一币种/周期/时间范围再次运行抓取器
- **THEN** 已存在的 (symbol,interval,open_time) 行不被重复插入,行数不增加

#### Scenario: 短历史币标注
- **WHEN** 某币种可得历史不足(如新上市)
- **THEN** 系统按实际可得根数落库,不报错、不补造数据,并在汇总中标注其历史长度

### Requirement: 预登记标准形态库
系统 SHALL 提供一个手写的标准蜡烛形态识别库,形态集与每形态的方向假设(看涨/看跌/中性)在测试前预登记并固定;形态判定阈值 MUST 为固定常量,运行期 SHALL NOT 依据收益数据调参。

#### Scenario: 识别预登记形态
- **WHEN** 对一段 OHLC 序列调用形态库
- **THEN** 返回该序列上命中的预登记形态及其预登记方向(如 Hammer→看涨、Shooting Star→看跌、Rising Three Methods→看涨延续)

#### Scenario: 阈值不可调
- **WHEN** 在回测流程中运行形态识别
- **THEN** 形态定义阈值取自固定常量,流程 SHALL NOT 提供按结果调阈值的路径

### Requirement: 真实成本 ATR 退出回测
系统 SHALL 对每个形态信号以 ATR-based 退出(SL=entry∓1.5×ATR(14)、TP=entry±3.0×ATR、最长持仓 10 日)经 `resolve_counterfactual` 用真实 CostModel 与 K 线路径结算净收益;同根 SL/TP 冲突 MUST 取 SL-first 保守。

#### Scenario: 结算单个形态信号
- **WHEN** 在某日线 bar 命中一个看涨形态
- **THEN** 系统以该 bar 收盘为 entry、按 ATR 设 SL/TP,沿后续 K 线路径结算 outcome(tp/sl/expired)与净 R

#### Scenario: 簇去重
- **WHEN** 同一 symbol+方向在簇窗口内多次命中
- **THEN** 仅取一次进入样本,避免重叠样本自相关夸大显著性

### Requirement: 样本外三分与多重比较校正
系统 SHALL 将样本按时间切为 train(2023-24)/val(2025)/test(2026) 三段分别统计,并对所测形态数施加多重比较校正(Bonferroni 或 FDR);一个(形态×上下文)桶 SHALL 仅在三段同号、且校正后仍显著、且过诚实门时才被判为可信 edge。

#### Scenario: 三段同号校验
- **WHEN** 某形态在 train 为正、val 为负
- **THEN** 该形态判为不稳健,不计入可信 edge

#### Scenario: 校正后显著
- **WHEN** 测试 N 个形态且某形态名义 p 显著但未过 Bonferroni/FDR 校正
- **THEN** 该形态判为不显著(探索性),不计入可信 edge

### Requirement: 诚实加权(过关才非零)
系统 SHALL 按 `weight = max(0, OOS 样本外净 R/笔)` 赋权,且仅当该形态同时通过 诚实门(n≥30 且净 PnL CI 不跨 0)、校正后显著、三段同号时权重才非零,否则权重 MUST 为 0;权重 SHALL 为验证输出,SHALL NOT 由人工直觉设定。

#### Scenario: 不过关形态权重为零
- **WHEN** 某形态样本 n<30 或 CI 跨 0
- **THEN** 其权重被置为 0,不进入最终方向信号

### Requirement: Observability-only 红线
本能力的所有产物(抓取数据外的研究输出、形态信号、edge 报告)SHALL 为 observability-only;任何交易决策/风控路径(judge/executor/risk_guard/reviewer/position_analyst)MUST NOT import 或读取本研究模块/产物。

#### Scenario: 红线守卫测试
- **WHEN** 运行 `tests/test_cf_red_line_guard.py`
- **THEN** 存在断言验证决策/风控路径源码未 import 形态研究模块,违反则测试失败
