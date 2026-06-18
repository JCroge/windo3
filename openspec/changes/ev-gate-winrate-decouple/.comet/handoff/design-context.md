# Comet Design Handoff

- Change: ev-gate-winrate-decouple
- Phase: design
- Mode: compact
- Context hash: 47625277c7032dd968480ac96b7087de9b9be5da703224fbc8aad86d59dc748f

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/ev-gate-winrate-decouple/proposal.md

- Source: openspec/changes/ev-gate-winrate-decouple/proposal.md
- Lines: 1-33
- SHA256: b9e3bb9a20e613e7fb09f5cce89f457fb4265257c0b0c44e70a8949561123ea0

```md
# Proposal: 剔除开仓门的胜率因子

## Why

开仓决策的最后闸门是 `agents/trading/judge.py` 的 **EV 门**（`_check_expected_value`）。实测策略衰减时（近 20 笔胜率 25%、PF 0.64），这道门会因为**实际滚动胜率**把开仓拦死。运维诉求：胜率 25% 不应该直接决定能否开仓——开仓应由信号质量与单笔经济性（R:R/成本）决定，而非被近期实现胜率单点否决。

实际胜率通过**三条路径**进入 EV 门并拦截开仓：
1. **胜率硬阈值**（`judge.py:3699`）：`effective_win_rate < 0.4 且 |score| < 70 → 强拒`。
2. **压垮 EV**（`judge.py:3566`）：`EV = p_win × net_profit − (1−p_win) × net_loss`，`p_win` 取自实际滚动胜率（`_get_p_win`），25% 时 EV 近乎必为负，撞 `EV < ev_min_threshold(0.05) → 拒`。
3. **分桶覆盖**（`judge.py:3652-3693`）：分桶 win_rate 再次用实际胜率重算 EV。

只删硬阈值不够——必须同时切断 `p_win` 与实际胜率的耦合，否则 EV 仍被拖负。

## What

引入 config 开关 `ev_winrate_gate_enabled`（默认 `true` = 完全保持现状）。关闭后：
1. EV 公式改用**固定中性 p_win**（`ev_neutral_p_win`，默认 0.55），不再读实际滚动胜率。
2. 跳过胜率<40% 硬阈值（路径 ①）。
3. 跳过分桶 win_rate 覆盖（路径 ③）。
4. **保留** EV 阈值门（路径 c）：用固定 p_win 算出的 EV 继续拦 R:R/成本差的单——经济保护不丢。

config.yaml 设 `ev_winrate_gate_enabled: false` 落地运维诉求。

## Scope

- 改动集中在 `judge.py` 的 EV 门链路 + 配置加载（config_loader.py / config.yaml）+ 测试。
- 开关默认值保持 `true`，不改变任何未显式配置环境的现状行为。

## Non-goals / Out of scope

- 不动 `reviewer.py` 的 win_rate 计算 / decay 检测、`telegram_notifier.py` 展示、backtest/cf_* —— 属监控复盘，与开仓门无关。
- 不动 EV 阈值 `ev_min_threshold`、强信号豁免阈值、R:R 门、评分门 `_compute_score`。
- 不移除 EV 门本身（保留经济门），仅剔除「实际胜率」这一因子。
```

## openspec/changes/ev-gate-winrate-decouple/design.md

- Source: openspec/changes/ev-gate-winrate-decouple/design.md
- Lines: 1-51
- SHA256: f9f3e5bf71e1778818750184717592181d9cd7b036c6c6c47f4d64edf70cd304

```md
# Design (高层): 剔除开仓门的胜率因子

## 架构决策

**单一开关 + 单一注入点**：在 EV 门链路上用 `ev_winrate_gate_enabled` 控制「实际胜率是否参与开仓」。关闭时改用固定中性 `p_win`，保留 EV 阈值这道经济门。开关默认 `true`，向后兼容。

### 为什么用「固定 p_win」而非「停用整个 EV 门」
EV 门同时承担两类拦截：胜率拦截（要剔除）+ R:R/成本经济拦截（要保留）。把 `p_win` 钉死为中性常数，既切断了实际胜率的影响，又让 `EV = p_win·net_profit − (1−p_win)·net_loss` 仍能拦掉赔率太差的单。停用整个门会一并丢掉经济保护，策略衰减期风险更高，故不取。

## 注入点（agents/trading/judge.py）

| 路径 | 位置 | 关闭开关时行为 |
|------|------|----------------|
| ② EV 被胜率压垮 | `_get_p_win()` line 3619 顶部短路 | 返回 `(ev_neutral_p_win, "fixed")`，不读 rolling/bayesian |
| ③ 分桶覆盖 | `_check_expected_value` line 3651 分桶块 | 前置 `ev_winrate_gate_enabled` 条件，整段跳过 |
| ① 胜率硬阈值 | `_check_expected_value` line 3699 | 前置 `ev_winrate_gate_enabled` 条件，跳过 |
| c 经济门 | `_check_expected_value` line 3707 EV 阈值 | **不动**，用固定 p_win 的 EV 继续拦 |

构造函数（line 88 EV 参数块）新增 `_ev_winrate_gate_enabled` / `_ev_neutral_p_win`，沿用 `config.get(..., default) if config else default`。

## 配置流（utils/config_loader.py）

沿用既有四段式（与上个 change `consecutive_loss_limit` 同模式）：
- RISK_DEFAULTS：`ev_winrate_gate_enabled=True`、`ev_neutral_p_win=0.55`
- RANGE_VALIDATORS：`ev_neutral_p_win: (0.0, 1.0)`（布尔不入数值校验）
- env_map：`EV_WINRATE_GATE_ENABLED`(_to_bool)、`EV_NEUTRAL_P_WIN`(float)
- `_load_yaml`：risk 节点映射两键，使 config.yaml 可配
- banner：加「EV 胜率门: 开启/关闭」展示，重启后核对生效

合并优先级不变：RISK_DEFAULTS < config.yaml < 环境变量。

## 数据流（关闭开关后）

```
信号 → _compute_score（不含胜率）→ R:R 门 → EV 门:
   p_win = 0.55 (fixed, 不读实际胜率)
   EV = 0.55·net_profit − 0.45·net_loss
   ├─ 跳过胜率<40%硬阈值
   ├─ 跳过分桶覆盖
   └─ 若 EV < 0.05 → 仍拒（经济门保留）
```

## 不变量

- 开关 `true`（默认）时所有路径与现状逐行一致。
- daily hard stop / regime / short guard / 流动性等其它门不受影响。
- 生效需重启交易进程（Judge 实例化时读配置）。

## delta spec 影响（待 comet-design 确认）

EV 门行为可能由某 capability spec 描述（含 `test_ev_gate.py`、`test_phase2_bucketed_ev.py` 对应验收）。design 阶段需确认是否需要 delta spec 记录「胜率门可关闭」这一新增可配置行为。
```

## openspec/changes/ev-gate-winrate-decouple/tasks.md

- Source: openspec/changes/ev-gate-winrate-decouple/tasks.md
- Lines: 1-9
- SHA256: d3f744622f5419c507b2d230ae676d7082db69e58dd8bf1e716aca39cad88328

```md
# Tasks: 剔除开仓门的胜率因子

- [ ] 1. `judge.py` 构造函数新增 `_ev_winrate_gate_enabled`(默认 True) / `_ev_neutral_p_win`(默认 0.55)
- [ ] 2. `judge.py` `_get_p_win()` 顶部短路：关闭时返回 `(ev_neutral_p_win, "fixed")`
- [ ] 3. `judge.py` `_check_expected_value()`：分桶块 + 胜率<40%硬阈值前置开关条件，关闭时跳过；EV 阈值门不动
- [ ] 4. `utils/config_loader.py`：RISK_DEFAULTS / RANGE_VALIDATORS / env_map / `_load_yaml` / banner 五处接入两个新键
- [ ] 5. `config.yaml` risk 节点新增 `ev_winrate_gate_enabled: false` + `ev_neutral_p_win: 0.55`
- [ ] 6. `test_ev_gate.py` 新增用例：关闭开关时 (a) `_get_p_win()` 返回 `(0.55,"fixed")`；(b) 胜率25%+score<70+合理R:R 的 plan `_check_expected_value` 返回 True；(c) R:R 极差(EV显著负)的 plan 仍返回 False
- [ ] 7. 回归：`pytest test_ev_gate.py test_phase2_bucketed_ev.py test_phase2_confidence_split.py` 全过；`load_config()` 读到 `ev_winrate_gate_enabled=False`
```

## openspec/changes/ev-gate-winrate-decouple/specs/open-gate-ev/spec.md

- Source: openspec/changes/ev-gate-winrate-decouple/specs/open-gate-ev/spec.md
- Lines: 1-31
- SHA256: a8811a00a9b79d06175cd5c5866d7be175734a2e0c81eacdffd0ffd842699476

```md
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
```

