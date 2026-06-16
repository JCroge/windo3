# Design (high-level) — fix-cf-lab-replay-config-parity

> OpenSpec 高层草图。详细 RFC + 方案权衡定在 comet-design 的 Superpowers Design Doc。

## 问题边界（已坐实）

```
live 生产 Judge: config_loader.DEFAULTS → 四个 phase2 flag = True
        │
        ▼  录制决策 (rr_below_floor:1.41, confidence≥60 走 htf-aligned 保值分支 judge.py:1281)

replay/CF-sim baseline: config={} → _install_config_flags 默认 phase2 flag = False
        │
        ▼  confidence 走 judge.py:1283 max(40,conf*0.7)=40 → quality_gate  ❌ 发散
```

全量 660 条：config={} → fidelity 0.365；config=DEFAULTS → fidelity **0.902**。

## 待修点

- replay/CF-sim 的有效 config 基线：`build_delta_report`/`run_arm` baseline 臂、`sweep_knob`、`cf_direction_recommendation.py` 驱动。perturbation 叠加在生产基线上（不是叠在 `{}` 上）。

## 候选方案（comet-design 定夺）

- **A. 用 `config_loader` 生产默认作基线**：driver/build_delta_report 以 `config_loader.DEFAULTS`（或 `get_config()`）为 baseline_config，perturbation dict 覆盖其上。简单、立即生效（坐实 0.90）。风险：不含 env override（若 live 用了非默认 rr floor 等，仍有小差）。
- **B. 录制时把 resolved config 存进决策磁带**：replay 用录制 config（最忠实，含 env override；rr 地板 1.50 本身是 config 值也被覆盖）。需 tape schema 加字段 + 累积，重。
- **C. 折中**：A 立即落地；磁带加 resolved-config 字段为 B 铺路（旧记录 fallback 到 DEFAULTS）。

权衡点：perturbation 语义——扰动 dict 必须只覆盖目标旋钮，不能把其它旋钮重置回 default 之外的值；两臂都以同一生产基线起步，delta 才干净。

## 不变量 / 红线

- observability-only write-only：禁交易决策路径读 CF 产物（守卫不放松）。
- 不改 live Judge 决策逻辑、不改 live 生产 config、不改 choppy 地板 1.50、无需 event_backtest。
- 非目标：production-config 下剩余 ~10% 残差（ev_gate→15m_blocked/accept）留后续 change。
