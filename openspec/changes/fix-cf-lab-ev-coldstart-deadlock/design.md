# Design (high-level) — fix-cf-lab-ev-coldstart-deadlock

> 本文件是 OpenSpec 高层架构草图。详细技术 RFC + 方案权衡定夺在 comet-design 阶段的 Superpowers Design Doc 完成。

## 问题边界（已坐实）

```
record (v2, tech 非空, 有 state_snapshot)
   │  直接 replay_decision({rr_floor_default:0.3})  → open_long      ✅ 旋钮生效
   │
   └─ run_arm: _inject_cf_state(record, cf) 灌入 CF 组合冷 EV 状态
          │
          ▼  replay_decision(injected, config)
        EV gate: EV = -0.41 < 0.05  (p_win=40% bayesian_prior)  → hold  ❌ 永远拦死
```

死锁：开仓需正 EV → EV 靠累计 CF 胜率 → 没单开成 → 胜率不累计 → EV 永远冷。
矛盾锚点：live 当时这些单撞 `rr_below_floor`（live EV gate 已过），CF-sim 注入的 EV 比现实更悲观。

## 三个待修点

1. **EV 冷启动（主）** — `cf_portfolio` / `sequential_perturbation._inject_cf_state` / `_seed_cf_prior`。
2. **gate-level 保真** — `perturbation_delta_report.build_delta_report` 的 `baseline_fidelity` 比对粒度。
3. **驱动 v2 过滤** — `cf_direction_recommendation.load_records`（次要、独立、低风险）。

## 候选方案（comet-design 定夺，勿在此拍板）

EV 冷启动修法（互斥/可组合，需 brainstorm）：
- **A. CF-sim EV gate 改读录制 EV**：回放时 EV gate 直接用 tape 录制的 live EV/p_win（live 已算过），不冷重算。最忠实，但需确认 tape 是否录了 EV 输入。
- **B. 暖启动 `_seed_cf_prior`**：用磁带窗口前真实战绩给 CF EV 一个代表性先验，避免 40% 冷拒。简单，但先验来源/代表性需论证。
- **C. CF EV 状态贴 live**：每步注入时把 CF 的 archetype EV 状态对齐 live 决策时快照里的 EV 相关字段。

每个方案都要回答：会不会人为抬高 baseline_fidelity / 掩盖级联（L3b 最终审查修过的核心陷阱）。

## 不变量 / 红线

- observability-only write-only：禁止任何交易决策路径读取 CF 产物（`tests/test_cf_red_line_guard.py` 守卫不放松）。
- 不改 live Judge 决策逻辑、不改 choppy R:R 地板 1.50、不新增 LLM 旋钮扰动、无需 event_backtest。
- 两臂同估算 → 系统性偏差在 delta 抵消的设计原则保持。
