# Comet Design Handoff

- Change: cf-lab-driver-portfolio-param-parity
- Phase: design
- Mode: compact
- Context hash: 9630220f9caceb59c5b78e95403f533cded71a167f6fd79c008582366c153091

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/cf-lab-driver-portfolio-param-parity/proposal.md

- Source: openspec/changes/cf-lab-driver-portfolio-param-parity/proposal.md
- Lines: 1-49
- SHA256: a2b4d9afc7c48c13a8837a0b6bd14a05499cce44dd5fd746d2b38c36e24bea39

```md
# Proposal: cf-lab-driver-portfolio-param-parity

## Why

2026-06-17 诊断「CF opens 恒 2」（证伪"组合层 slot/EV 瓶颈"假设）时发现一个潜伏的保真 gap：

反事实实验室的一次性分析驱动 `cf_direction_recommendation.py` 与 `cf_rr_fidelity_ab.py` 调用
`build_delta_report` / `sweep_knob` / `sweep_grid` 时**不传组合参数**，于是落到库函数默认值
`daily_pnl_hard_stop=-50` / `initial_equity=1000`。而 live 运行时实际值为：

| 参数 | 库默认（驱动现用） | live 实际（`load_config()`） | 来源 |
|---|---|---|---|
| `daily_pnl_hard_stop` | **-50** | **-300** | `config.yaml: risk.max_daily_loss=300` |
| `initial_equity` | **1000** | **300** | `.env: EFFECTIVE_BALANCE_CAP=300` |
| `max_slots` | 3 | 3 | `max_concurrent_positions` 默认 3（已一致） |
| `consecutive_loss_limit` | 3 | 3 | 默认 3（已一致） |

`daily_pnl_hard_stop` 偏紧 6×。当前 reject-only 磁带 0 次熔断（本次诊断 `day_halted=0` 实证），
故对既有结论无影响；但未来磁带 accept 变多、CF 真开仓增加后，−50 会比 live 更早熔断、
系统性污染 perturbed-vs-baseline 的 PnL/胜率/回撤 delta。属应在影响显现前修掉的保真债。

## What

驱动层从 `utils.config_loader.load_config()` 读 live 组合参数，显式传入 `build_delta_report` /
`sweep_knob` / `sweep_grid` 的既有 kwarg（这些函数已接受并透传到 `run_arm` / `CounterfactualPortfolio`）。

- `initial_equity = cfg['effective_balance_cap'] or 1000.0`
- `max_slots = cfg['max_concurrent_positions']`
- `daily_pnl_hard_stop = cfg['daily_pnl_hard_stop']`
- `consecutive_loss_limit = cfg['consecutive_loss_limit']`

## Scope

- **改**：仅 repo 根的 2 个一次性分析驱动 `cf_direction_recommendation.py`、`cf_rr_fidelity_ab.py`。
- **不改**：`utils/` 库（`sequential_perturbation.py` / `knob_sweep.py` / `joint_knob_sweep.py` /
  `cf_portfolio.py`）的签名与默认值保持不变；`agents/` 生产路径零触碰；红线守卫
  `tests/test_cf_red_line_guard.py` 不变。
- **性质**：observability-only。无须 `event_backtest`（不改任何交易决策公式/门）。

## Capabilities

无。本变更不新增/修改任何 capability，不产生 delta spec —— 仅对齐分析驱动的入参，
库的可信结论合约与红线不变。

## Impact

- 对当前诊断结论**零影响**（已实证 0 熔断；本次三臂 baseline/-50 与 -300 逐字节相同）。
- 对未来重跑：CF 熔断行为与 live 一致，delta 不再被 −50 提前熔断污染。
- 回归风险极低：仅改驱动入参，库行为不变；全量 pytest 应保持基线绿。
```

## openspec/changes/cf-lab-driver-portfolio-param-parity/design.md

- Source: openspec/changes/cf-lab-driver-portfolio-param-parity/design.md
- Lines: 1-44
- SHA256: 4d94bfcb8b23cb51e9a83270ff784ba146abb1c9992b022b2bdb684291bdfcf5

```md
# Design: cf-lab-driver-portfolio-param-parity

Tweak 级实现说明（无方案对比 —— 修法唯一且机械）。

## 实现

两个驱动各自顶部加一个本地 helper，从 `load_config()` 派生 live 组合 kwarg，传入所有
portfolio-sim 调用点。**驱动本地，不进 `utils/` 库**（保持"零库改动"）。

```python
from utils.config_loader import load_config

def _live_portfolio_kwargs():
    """从 live config 派生 CF 组合参数，对齐 run_agents 运行时（observability-only）。"""
    cfg = load_config()
    return {
        "initial_equity": cfg.get("effective_balance_cap") or 1000.0,
        "max_slots": cfg.get("max_concurrent_positions", 3),
        "daily_pnl_hard_stop": cfg.get("daily_pnl_hard_stop", -50.0),
        "consecutive_loss_limit": cfg.get("consecutive_loss_limit", 3),
    }
```

调用点改造（`**_live_portfolio_kwargs()` 展开）：

- `cf_direction_recommendation.py`：`build_delta_report` 探针、两处 `sweep_knob`、`sweep_grid`。
- `cf_rr_fidelity_ab.py`：其 `build_delta_report` 调用。

`sweep_knob` / `sweep_grid` / `build_delta_report` 已声明并透传
`initial_equity` / `max_slots` / `daily_pnl_hard_stop` / `consecutive_loss_limit` 四个 kwarg
（见 `utils/knob_sweep.py:7-18`、`utils/joint_knob_sweep.py:10-17`、
`utils/sequential_perturbation.py:131-136`），故无需改库签名。

## 边界与降级

- `effective_balance_cap` 可能为 `None`（未启用逻辑账户）→ `or 1000.0` 兜底，保持原行为量级。
- `load_config()` 读 `config.yaml` + env，驱动从 repo 根运行，与 `run_agents.py` 同源 → 单一真相源。
- 不引入新 config 项、不改任何默认值；库默认值仍是 −50/1000，仅驱动显式覆盖为 live。

## 不做

- 不改 `utils/` 库默认值（其它调用方/测试依赖；改默认会有更大涟漪）。
- 不动 `cf_replay_driver.py`（L1 driver，不实例化 `CounterfactualPortfolio`，不涉本参数）。
- 不动 `cf_lever2_rejected_ab.py`（走 rejected 前向流，不经组合 sim）。
```

## openspec/changes/cf-lab-driver-portfolio-param-parity/tasks.md

- Source: openspec/changes/cf-lab-driver-portfolio-param-parity/tasks.md
- Lines: 1-10
- SHA256: 9a2edf3c21f39b4144afc16a311b6661560f6b771cac0fcb54d99e16d7e241c8

```md
# Tasks: cf-lab-driver-portfolio-param-parity

- [x] 1. `cf_direction_recommendation.py`：加 `_live_portfolio_kwargs()`（从 `load_config()` 派生
  `initial_equity`/`max_slots`/`daily_pnl_hard_stop`/`consecutive_loss_limit`），用 `**_live_portfolio_kwargs()`
  展开到 baseline 探针 `build_delta_report`、两处 `sweep_knob`、`sweep_grid` 调用点。
  → 实测 helper 返回 `{initial_equity:300.0, max_slots:3, daily_pnl_hard_stop:-300.0, consecutive_loss_limit:3}`。
- [x] 2. `cf_rr_fidelity_ab.py`：同样加/复用 `_live_portfolio_kwargs()`，展开到其 `build_delta_report` 调用点。
- [x] 3. 验证：全量 `python3 -m pytest -q` → **1285 passed / 4 deselected / 1 warning（183.85s）基线绿**；
  helper 实测返回 live `daily_pnl_hard_stop=-300.0`/`initial_equity=300.0`/`max_slots=3`/`consecutive_loss_limit=3`；
  两驱动 `py_compile` OK；未新增/改动任何 `utils/` 库与红线守卫（仅驱动加 `load_config` import 是 prod→驱动方向，不违 observability 红线）。
```

