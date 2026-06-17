---
comet_change: cf-lab-driver-portfolio-param-parity
role: technical-design
canonical_spec: openspec
---

# Design Doc: CF 实验室分析驱动组合参数对齐 live

> OpenSpec 为需求真相源（`openspec/changes/cf-lab-driver-portfolio-param-parity/`）。本文档只承载技术设计。

## 背景

2026-06-17 诊断「CF opens 恒 2」（systematic-debugging，证伪"组合层 slot/EV 瓶颈"假设）时发现一个潜伏保真 gap：反事实实验室的一次性分析驱动 `cf_direction_recommendation.py` / `cf_rr_fidelity_ab.py` 调用 `build_delta_report` / `sweep_knob` / `sweep_grid` 时**不传组合参数**，落到库默认 `daily_pnl_hard_stop=-50` / `initial_equity=1000`，与 live 运行时（`load_config()`：`-300` / `effective_balance_cap=300`）不一致，偏紧 6×。

当前 reject-only 磁带 0 次熔断（诊断实证 `day_halted=0`）→ 对既有结论零影响；但未来磁带 accept 变多、CF 真开仓增加后，`-50` 会比 live 更早熔断、系统性污染 perturbed-vs-baseline 的 PnL/胜率/回撤 delta。属应在影响显现前清掉的保真债。

## 设计决策

### D1. helper 位置 = 驱动本地（不进 `utils/` 库）

每个驱动各自定义 `_live_portfolio_kwargs()` 读 `load_config()` 派生 live 组合参数。

- **取舍**：~8 行在 2 个驱动重复，换取**零库改动** —— 守住「observability 工具不修改 `utils/` 库默认值（其它调用方/测试依赖）」的边界，也不必为一次性需求扩大红线守卫白名单。
- **否决 B（共享 `utils/cf_live_params.py`）**：新增库模块需红线守卫额外白名单，为 2 调用方的一次性概念扩大库面，不划算。
- **否决 C（改库默认值读 config）**：改共享库行为，其它调用方/测试依赖 `-50/1000` 默认，涟漪过大。

### D2. 真相源 = `utils.config_loader.load_config()`

与 `run_agents.py` 同一加载路径（`config.yaml` + env），单一真相源，避免在驱动里硬编码 `-300/300`。派生映射：

| CF kwarg | config key | live 实测值 |
|---|---|---|
| `initial_equity` | `effective_balance_cap`（`or 1000.0` 兜底） | 300.0 |
| `max_slots` | `max_concurrent_positions` | 3 |
| `daily_pnl_hard_stop` | `daily_pnl_hard_stop` | −300.0 |
| `consecutive_loss_limit` | `consecutive_loss_limit` | 3 |

### D3. 注入方式 = `**` 展开进既有 kwarg

`build_delta_report`（`utils/sequential_perturbation.py`）、`sweep_knob`（`utils/knob_sweep.py`）、`sweep_grid`（`utils/joint_knob_sweep.py`）**已声明并透传**这四个 kwarg 到 `run_arm` / `CounterfactualPortfolio`，故驱动只需 `**_live_portfolio_kwargs()`，库签名零改动。

## 边界条件

- `effective_balance_cap` 可为 `None`（未启用逻辑账户）→ `or 1000.0` 兜底保持原量级，不抛错。
- 库默认值（`-50/1000/3/3`）**保持不变**，仅驱动显式覆盖为 live；其它调用方与单测行为不受影响。
- 不动 `cf_replay_driver.py`（L1 driver，不实例化 `CounterfactualPortfolio`）与 `cf_lever2_rejected_ab.py`（走 rejected 前向流，不经组合 sim）。

## 技术风险

- **极低**。仅改 2 个 repo 根一次性分析驱动的入参；库行为不变；`agents/` 生产路径零触碰；红线守卫 `tests/test_cf_red_line_guard.py` 不变（驱动 import `config_loader` 是 prod→驱动方向，不违 observability-only 红线——红线禁的是决策/风控路径**读** CF 产物）。

## 测试策略

observability-only，**无须 `event_backtest`**（不改任何交易决策公式/门）。验证三项：

1. 全量 `python3 -m pytest -q` 维持基线 **1285 passed / 4 deselected**（已过）。
2. 两驱动 `_live_portfolio_kwargs()` 实测返回 live `{initial_equity:300.0, max_slots:3, daily_pnl_hard_stop:-300.0, consecutive_loss_limit:3}`（已验）。
3. 两驱动 `py_compile` OK（已过）；未新增/改动任何 `utils/` 库与红线守卫。

不新增单测：被改对象是一次性分析脚本（无既有单测），新增一个等价于测 `config_loader` 本身，价值低。

## 不做（YAGNI）

- 不改库默认值、不抽共享 util、不动 L1/rejected 驱动、不引入新 config 项。
