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
