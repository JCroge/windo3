## ADDED Requirements

### Requirement: 时间序磁带驱动
系统 SHALL 按时间顺序读决策磁带，对每条 record 用扰动 config + 当前 CF 状态注入真实 `_make_decision` 重决策，再按决策结果推进 CF 组合状态。

#### Scenario: 时间序重放
- **WHEN** driver 跑一条扰动序列
- **THEN** 系统 SHALL 按 record timestamp 升序处理，每步用 CF 状态机当前状态（非录下快照）注入 Judge

#### Scenario: 复用真实决策
- **WHEN** 每步重决策
- **THEN** 系统 SHALL 经 L2 `replay_decision`（注入 CF 状态）→ 真实 `_make_decision`，SHALL NOT 另写决策逻辑

#### Scenario: 决策推进 CF 状态
- **WHEN** 扰动决策为开仓且 CF slot/daily-stop 允许
- **THEN** 系统 SHALL 在 CF 状态机开一个 CF 仓并占 slot；为 hold/reject 则不开

### Requirement: 退出推进与到期解析
系统 SHALL 在序列推进中按时间解析到期/触发的 CF 持仓退出，更新 CF 状态。

#### Scenario: 到期 CF 仓解析
- **WHEN** 序列时间推进越过某 CF 仓的 SL/TP/24h 退出点
- **THEN** 系统 SHALL 解析其退出、计净 PnL、释放 slot、喂回反馈

### Requirement: driver observability-only write-only
系统 SHALL 保证序列 driver 为离线工具，反事实消息绝不进真实总线，严禁被任何 gate/veto/halt/rank/daily-stop 读取。

#### Scenario: 不进真实总线
- **WHEN** driver 重决策产生决策 payload
- **THEN** 系统 SHALL 只在 CF 内部消费，SHALL NOT publish 到真实 bus / Reviewer / RiskGuard

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

### Requirement: 两臂以生产 config 基线起步，扰动只覆盖目标旋钮
`build_delta_report`/`run_arm` 的 baseline 臂与 perturbed 臂 SHALL 以 per-record 有效生产 config（`config_snapshot` 或 `production_base_config()` fallback）为基线；perturbed 臂 = 该基线 + 扰动覆盖，扰动 SHALL 只覆盖目标旋钮，SHALL NOT 把其它旋钮重置出生产基线。

#### Scenario: baseline 臂用生产基线
- **WHEN** `run_arm` 以 `config={}`（baseline 臂）运行
- **THEN** 系统 SHALL 把空扰动解释为「生产基线，无覆盖」，即用 per-record 有效生产 config，而非 `_install_config_flags` 的硬默认

#### Scenario: 扰动叠加只覆盖目标旋钮
- **WHEN** perturbed 臂用扰动 `{rr_floor_default: 0.3}` 运行
- **THEN** 其有效 config SHALL 等于生产基线仅把 `rr_floor_default` 覆盖为 0.3，其它旋钮（含 Phase-2 flag）保持生产基线值

#### Scenario: 两臂同基线使 delta 干净
- **WHEN** baseline 与 perturbed 臂跑同一序列
- **THEN** 两臂 SHALL 从同一 per-record 生产基线起步，差异仅来自扰动旋钮，使 delta 不含 config 基线偏差
