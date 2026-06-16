## ADDED Requirements

### Requirement: 注入保留录制的 per-symbol 决策输入上下文
`_inject_cf_state` 注入回放状态时 SHALL 保留录制快照的 `_symbol_state`（per-symbol 市场决策输入上下文，如 `trend_streak`/`last_tech`/`last_decision_time`），SHALL NOT 清空为 `{}` 致 Judge 信号强度路径读不到上下文而误判信号不足。还原的是市场决策输入（非 reality 的 EV/胜率交易结果累计），不触 "绝不 per-record 注入 reality 演化计数" 反模式。

#### Scenario: 注入保留录制 _symbol_state
- **WHEN** `_inject_cf_state` 构造回放状态快照
- **THEN** 其 SHALL 以录制快照的 `_symbol_state` 填充（镜像 `_regime_manager` 透传），而非 `cf.to_snapshot()` 的空 `{}`

#### Scenario: baseline 臂忠实复现
- **WHEN** 零扰动 baseline 臂回放含 `trend_streak`/`last_tech` 的录制记录
- **THEN** 其 gate SHALL 与录制一致（不再因空 `_symbol_state` 退化为 hold_other），sequential baseline_fidelity 显著高于清空时（0.798 → ~0.91）

#### Scenario: 不改 EV/cooldown 战绩累计
- **WHEN** 还原 `_symbol_state` 决策输入字段
- **THEN** CF 的 EV gate / cooldown 战绩累计语义 SHALL 不变（仍由 `_seed_cf_prior` + CF 自累计驱动），perturbed 臂级联不被还原市场上下文削弱
