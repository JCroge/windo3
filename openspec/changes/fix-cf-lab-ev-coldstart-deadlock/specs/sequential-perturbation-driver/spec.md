## ADDED Requirements

### Requirement: CF EV 状态暖启动播种(破冷启动死锁)
序列驱动 SHALL 在序列起点用录制的滚动胜率把 CF 的 rolling 窗口暖启动播种,使 CF EV gate 起步即贴近 live 决策时的真实胜率,而非冷启动 bayesian 先验导致拒所有开仓的死锁。

#### Scenario: 用录制滚动率播种窗口
- **WHEN** `_seed_cf_prior` 在序列起点初始化 CF
- **THEN** 系统 SHALL 用第一条 record 录制的 `_recent_win_rate`(磁带窗口前真实滚动胜率)等价填满 CF 的 rolling 窗口(按比例的 win/loss 合成条目),使起步 `_recent_win_rate` 等于该录制率

#### Scenario: 合成种子被 CF 真实结果挤出
- **WHEN** CF 自身结算累计达窗口长
- **THEN** rolling 窗口 SHALL 100% 由 CF 自身结果构成(合成种子已 FIFO 挤出),级联真实;合成种子 SHALL NOT 人为抬高 baseline_fidelity

#### Scenario: 两臂共享同一播种
- **WHEN** baseline 臂与 perturbed 臂分别跑序列
- **THEN** 两臂 SHALL 从同一播种起步,各自用自身 CF 结果累计,使 delta 干净(系统性偏差在两臂抵消)
