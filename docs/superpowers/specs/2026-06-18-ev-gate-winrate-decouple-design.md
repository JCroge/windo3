---
comet_change: ev-gate-winrate-decouple
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-18-ev-gate-winrate-decouple
status: final
---

# 技术设计：剔除开仓门的胜率因子

> 需求事实源为 OpenSpec（proposal.md + specs/open-gate-ev/spec.md）。本文档只做技术设计，不重定义需求。

## 问题

EV 开仓门 `Judge._check_expected_value`（`agents/trading/judge.py`，开仓最后闸门）经三条路径用**实际滚动胜率**拦开仓。策略衰减时（胜率 25%、PF 0.64）开仓被拦死：

1. **胜率硬阈值** `judge.py:3699`：`effective_win_rate < 0.4 且 |score| < 70 → 强拒`
2. **p_win 压垮 EV** `judge.py:3566`：`EV = p_win·net_profit − (1−p_win)·net_loss`，`p_win` 来自 `_get_p_win()` 的实际滚动胜率，25% 时 EV 近乎必负，撞 `EV < ev_min_threshold(0.05) → 拒`（`judge.py:3707`）
3. **分桶覆盖** `judge.py:3652-3693`：分桶 win_rate 再次用实际胜率重算 EV

仅删硬阈值不够——必须同时切断 `p_win` 与实际胜率的耦合。

## 方案：单开关 + 固定中性 p_win，保留经济门

`ev_winrate_gate_enabled`（默认 `true`，逐行保持现状）。关闭后用固定 `ev_neutral_p_win`（默认 0.55）替代实际胜率，跳过胜率硬阈值与分桶覆盖；**保留** EV 阈值经济门。

**为何不停用整个 EV 门**：EV 门同时承担胜率拦截（要剔除）与 R:R/成本经济拦截（要保留）。把 `p_win` 钉为中性常数，既切断实际胜率影响，又让 `EV = 0.55·profit − 0.45·loss` 仍能拦掉赔率太差的单。停用整门会一并丢经济保护，衰减期风险更高。

## 注入点（agents/trading/judge.py）

| 路径 | 位置 | 关闭时行为 |
|------|------|-----------|
| 构造 | line 88 EV 参数块 | 新增 `_ev_winrate_gate_enabled`(默认True)、`_ev_neutral_p_win`(默认0.55)，沿用 `config.get(...) if config else default` |
| ② | `_get_p_win()` line 3619 顶部 | `if not self._ev_winrate_gate_enabled: return float(self._ev_neutral_p_win), "fixed"` |
| ③ | `_check_expected_value` line 3651 分桶块 | 前置 `if self._ev_winrate_gate_enabled and getattr(...):` 整段跳过 |
| ① | `_check_expected_value` line 3699 硬阈值 | 前置 `if self._ev_winrate_gate_enabled and effective_win_rate < 0.4 and ...` |
| c | `_check_expected_value` line 3707 EV 阈值 | **不动**（经济门，用固定 p_win 的 EV 继续拦） |

## 配置流（utils/config_loader.py，四段式，与 consecutive_loss_limit 同模式）

- RISK_DEFAULTS：`ev_winrate_gate_enabled=True`、`ev_neutral_p_win=0.55`
- RANGE_VALIDATORS：`ev_neutral_p_win: (0.0, 1.0)`（布尔不入数值校验）
- env_map：`EV_WINRATE_GATE_ENABLED`(_to_bool)、`EV_NEUTRAL_P_WIN`(float)
- `_load_yaml`：risk 节点映射两键（布尔用 `_to_bool`，p_win 用 `float`）
- banner：加「EV 胜率门: 开启/关闭 (neutral_p_win=…)」，重启核对生效

`config.yaml` risk 节点设 `ev_winrate_gate_enabled: false` + `ev_neutral_p_win: 0.55` 落地诉求。合并优先级不变：defaults < yaml < env。

## 数据流（关闭后）

```
信号 → _compute_score(不含胜率) → R:R门 → EV门:
  p_win = 0.55 (fixed)
  EV = 0.55·net_profit − 0.45·net_loss
  ├ 跳过 胜率<40% 硬阈值
  ├ 跳过 分桶覆盖
  └ EV < 0.05 → 仍拒（经济门）；强信号(|score|≥70 且 EV≥-0.3)豁免不变
```

## 测试策略（test_ev_gate.py）

- 关闭开关：`_get_p_win()` 返回 `(0.55,"fixed")`；胜率25%+score<70+合理R:R → `_check_expected_value` 返回 `True`
- 对照：关闭开关但 R:R 极差（EV 显著负、非强信号）→ 仍 `False`
- 配置：`load_config()`（config.yaml=false）读到 `ev_winrate_gate_enabled=False`；`ev_neutral_p_win` 越界抛 `ConfigError`
- 回归：默认配置下 `test_ev_gate.py`/`test_phase2_bucketed_ev.py`/`test_phase2_confidence_split.py` 原样通过

## 风险与边界

- **强信号豁免不变**：关闭时 EV 仍<0.05 时 `score≥70 且 EV≥-0.3` 豁免照常。
- **新增 `"fixed"` p_win 来源**：经核对无 spec 枚举 p_win_source；决策快照/回放（`judge.py:2381,3065`、`utils/decision_replay.py`）只记录不校验，安全。
- **默认值 True**：未显式配置环境行为零变化。
- **生效需重启**交易进程（Judge 实例化读配置）。
