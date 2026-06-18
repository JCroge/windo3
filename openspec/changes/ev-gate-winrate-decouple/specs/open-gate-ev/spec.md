## ADDED Requirements

### Requirement: EV 开仓门胜率因子可关闭

EV 开仓门（`Judge._check_expected_value`，开仓决策的最后闸门）SHALL 提供配置开关 `ev_winrate_gate_enabled`（默认 `true`），控制**实际滚动胜率**是否参与开仓准入。开关 MUST 默认开启，保持既有行为逐行不变。

开关**开启**时，门按既有逻辑用实际滚动胜率派生 `p_win`（rolling / bayesian），并施加胜率硬阈值与分桶覆盖。

开关**关闭**时，门 MUST 用固定中性胜率 `ev_neutral_p_win`（默认 0.55）替代实际胜率进入 EV 公式，并 MUST 跳过胜率硬阈值与分桶 win_rate 覆盖；但 MUST 保留 EV 阈值这道经济门（用固定 `p_win` 算出的 EV 仍按 `ev_min_threshold` 拦截 R:R/成本不达标的单）。

两个配置键 MUST 可经 `config.yaml` 的 `risk` 节点、环境变量与默认值三级注入，`ev_neutral_p_win` 取值范围 MUST 校验在 `(0.0, 1.0)`。

#### Scenario: 开关开启保持现状（默认）

- **WHEN** `ev_winrate_gate_enabled` 为 `true`（默认），近期实际胜率为 25%（< 40%）且信号 `score < 70`
- **THEN** `_check_expected_value` 按胜率硬阈值强拒开仓，行为与改动前一致

#### Scenario: 开关关闭后低胜率不拦开仓

- **WHEN** `ev_winrate_gate_enabled` 为 `false`，近期实际胜率为 25%，信号 `score < 70`，且计划 R:R 合理（用固定 `ev_neutral_p_win` 算出的 EV ≥ `ev_min_threshold`）
- **THEN** `_get_p_win()` 返回 `(ev_neutral_p_win, "fixed")`，跳过胜率硬阈值与分桶覆盖，`_check_expected_value` 返回 `True`（放行开仓）

#### Scenario: 开关关闭仍保留经济门

- **WHEN** `ev_winrate_gate_enabled` 为 `false`，但计划 R:R/成本太差，使固定 `ev_neutral_p_win` 算出的 EV < `ev_min_threshold` 且信号非强信号（`|score| < ev_strong_signal_threshold`）
- **THEN** `_check_expected_value` 返回 `False`（经济门仍拦截亏损期望的单）

#### Scenario: 配置三级注入与校验

- **WHEN** 经 `config.yaml` risk 节点设置 `ev_winrate_gate_enabled: false`、`ev_neutral_p_win: 0.55`
- **THEN** `load_config()` 返回的配置中两键生效；`ev_neutral_p_win` 越界（≤0 或 ≥1）时 MUST 抛 `ConfigError`
