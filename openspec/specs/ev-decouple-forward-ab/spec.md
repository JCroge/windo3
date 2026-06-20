## ADDED Requirements

### Requirement: 解耦放行单分类（gate-toggle 两臂复盘 + baseline 自检）

驱动 SHALL 对决策磁带的每条 `decision=accept` 且 `replayable` 记录跑两条复盘臂——**baseline 臂** `replay(ev_winrate_gate_enabled=False)`（= live 现配置）与**反事实臂** `replay(ev_winrate_gate_enabled=True)`（= 06-18 前旧胜率门）。baseline 臂复盘出的 accept/reject MUST 复现 live record 的 accept（二元类别），否则该条标复盘失真并排除出统计；反事实臂翻成 reject 的记录归类为 **"解耦放行"**（旧胜率门会拒、解耦后才过）。复盘 MUST 复用 `utils/decision_replay.py::replay_decision`（gate 经 perturbation override 切换），MUST NOT 重写门逻辑。

#### Scenario: baseline 自检忠实 → 可分类

- **WHEN** baseline 臂 `replay(ev_winrate_gate_enabled=False)` 复盘出 accept、与 live record 一致
- **THEN** 该条进入分类；反事实臂 reject 则归 "解耦放行"，accept 则归 "双门皆过"

#### Scenario: baseline 自检失真 → 排除

- **WHEN** baseline 臂复盘背离 live record 的 accept/reject 类别（复盘失真）
- **THEN** 该条排除出统计，报表报出失真排除条数（透明）

### Requirement: 前向结算与桶对比（CF 为主、real PnL 交叉）

驱动 SHALL 对 "解耦放行" 与 "双门皆过" 两桶各用 `resolve_counterfactual`+klines **统一 CF 口径**（TP1 保守、含亏单：tp→+tp1_dist/sl_dist、sl→−1、expired→0）结算前向净 R，两桶同口径使系统性 CF 偏差在 delta 抵消。对实际开仓的解耦放行单（经 symbol+ts 模糊 join `live_position_lifecycle.json`）SHALL 报出真实已实现 PnL 作**次要 sanity 交叉**，并标注 join 为模糊匹配、无 request_id。

#### Scenario: 两桶净 R 对比

- **WHEN** 两桶均结算完成
- **THEN** 报出解耦放行桶净 R、双门皆过桶净 R 及其 delta；解耦放行桶净 R 显著低于双门皆过且为负 → 提示解耦在放行亏损单（结论性判据，非自动执行）

#### Scenario: real PnL 交叉验证

- **WHEN** 解耦放行单中有实际开仓且 lifecycle 有已实现 PnL
- **THEN** 报出其真实净 PnL 与 CF 估算对照；join 为 symbol+ts 模糊（无 request_id）须显式标注，pending/external_close 不强行计入

### Requirement: 诚实门与 coverage 透明

驱动 SHALL 对结算结果按信号簇去重（同 symbol 连续重复评估归一簇，同 `cf_lever2_rejected_ab` 做法），并经 `utils/cf_honesty_gate.py::summarize_bucket` 诚实门——薄样本（簇数低于阈值）MUST 拒答而非给结论。klines 覆盖受限（`klines_1s` 仅近 ~数日 ~24 标的）导致无法结算的簇 MUST 跳过并如实报出跳过数。

#### Scenario: 薄样本拒答

- **WHEN** 去重后可结算簇数低于诚实门阈值
- **THEN** 驱动输出 "样本不足、拒答"，不给净 R 结论

#### Scenario: coverage 受限透明

- **WHEN** 部分解耦放行单因 klines 无覆盖无法结算
- **THEN** 报表报出可结算簇数 / 跳过数，不把跳过当作零影响

### Requirement: observability-only write-only 红线

驱动与其输出 SHALL 是 observability-only write-only：MUST NOT 被任何交易决策/风控路径 import 或读取，MUST NOT 下单，MUST NOT 自动修改线上 config（`ev_winrate_gate_enabled` 等），MUST NOT mutate 任何 live 状态。复盘臂复用 `replay_decision` 隔离机器（mock 外部 await、捕获 publish 绝不进真实 bus）。

#### Scenario: 不碰 live

- **WHEN** 驱动运行
- **THEN** 只读磁带/klines/lifecycle + 写报表（stdout 或独立文件），不发任何真实 bus 消息、不下单、不改 config
