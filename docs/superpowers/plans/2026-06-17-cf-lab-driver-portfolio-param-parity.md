---
change: cf-lab-driver-portfolio-param-parity
design-doc: docs/superpowers/specs/2026-06-17-cf-lab-driver-portfolio-param-parity-design.md
base-ref: a4192aa77c5f05a0c9075f28695176a806533fec
archived-with: 2026-06-17-cf-lab-driver-portfolio-param-parity
---

# CF 实验室驱动组合参数对齐 live 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans。本计划任务已在 tweak 阶段直接实现并测试绿（升级 full 后正式化记录），checkbox 标记实际完成状态。

**Goal:** 让两个 CF 分析驱动把 live 组合风控参数（−300/300/3/3）显式传入组合模拟器，消除默认 −50/1000 带来的潜伏保真 gap。

**Architecture:** 驱动本地 helper `_live_portfolio_kwargs()` 从 `utils.config_loader.load_config()` 派生 live 参数，`**` 展开进 `build_delta_report`/`sweep_knob`/`sweep_grid` 既有 kwarg。库签名与默认值零改动（详见 Design Doc D1–D3）。

**Tech Stack:** Python 3.9、`utils.config_loader`、既有 L3b/L4 反事实实验室模块。

archived-with: 2026-06-17-cf-lab-driver-portfolio-param-parity
---

### Task 1: `cf_direction_recommendation.py` 注入 live 组合参数

**Files:**
- Modify: `cf_direction_recommendation.py`（imports + helper + main 4 个调用点）

- [x] **Step 1: 加 import + helper**

```python
from utils.config_loader import load_config

def _live_portfolio_kwargs():
    cfg = load_config()
    return {
        "initial_equity": cfg.get("effective_balance_cap") or 1000.0,
        "max_slots": cfg.get("max_concurrent_positions", 3),
        "daily_pnl_hard_stop": cfg.get("daily_pnl_hard_stop", -50.0),
        "consecutive_loss_limit": cfg.get("consecutive_loss_limit", 3),
    }
```

- [x] **Step 2: main() 顶部取值并打印，4 个调用点 `**pf` 展开**

`pf = _live_portfolio_kwargs()`；`build_delta_report(..., fidelity_threshold=0.0, **pf)`、
两处 `sweep_knob(..., **pf)`、`sweep_grid(..., baseline_config={}, **pf)`。

- [x] **Step 3: 验证**

Run: `python3 -c "import cf_direction_recommendation as a; print(a._live_portfolio_kwargs())"`
Expected: `{'initial_equity': 300.0, 'max_slots': 3, 'daily_pnl_hard_stop': -300.0, 'consecutive_loss_limit': 3}`

- [x] **Step 4: Commit**（含 Task 2，见下）

### Task 2: `cf_rr_fidelity_ab.py` 注入 live 组合参数

**Files:**
- Modify: `cf_rr_fidelity_ab.py`（imports + helper + 单个 `build_delta_report` 调用点）

- [x] **Step 1: 加同一 import + helper**（与 Task 1 Step 1 相同代码）
- [x] **Step 2: main() 取 `pf` 并打印；`build_delta_report(recs, {}, knobs, price_loader, fidelity_threshold=0.0, **pf)`**
- [x] **Step 3: 验证**

Run: `python3 -c "import cf_rr_fidelity_ab as b; print(b._live_portfolio_kwargs())"`
Expected: `{'initial_equity': 300.0, 'max_slots': 3, 'daily_pnl_hard_stop': -300.0, 'consecutive_loss_limit': 3}`

- [x] **Step 4: Commit**

```bash
git add cf_direction_recommendation.py cf_rr_fidelity_ab.py openspec/changes/cf-lab-driver-portfolio-param-parity/tasks.md
git commit -m "tweak(cf-param-parity): drivers pass live portfolio kwargs to CF sim"
```

### Task 3: 全量回归 + 边界确认

**Files:** 无（验证任务）

- [x] **Step 1: py_compile 两驱动**

Run: `python3 -m py_compile cf_direction_recommendation.py cf_rr_fidelity_ab.py`
Expected: 无输出（OK）

- [x] **Step 2: 全量 pytest**

Run: `python3 -m pytest -q`
Expected: `1285 passed, 4 deselected, 1 warning`

- [x] **Step 3: 确认零库/红线改动**

`git diff --stat a4192aa...HEAD` 仅含 2 驱动 + 文档/簿记；`utils/` 库与 `tests/test_cf_red_line_guard.py` 未改。

archived-with: 2026-06-17-cf-lab-driver-portfolio-param-parity
---

## Self-Review

- **Spec coverage**：Design Doc D1（驱动本地 helper）→ Task1/2 Step1；D2（load_config 派生映射）→ helper 实现；D3（`**` 展开）→ Task1/2 Step2；测试策略 3 项 → Task3。无遗漏。
- **Placeholder scan**：无 TBD/TODO；每步含实际代码/命令/期望输出。
- **Type consistency**：helper 在两驱动签名一致，返回 4 个 kwarg 与 `build_delta_report`/`sweep_knob`/`sweep_grid` 形参名一致。
