## Why

前向影子决策记录器（`trend-entry-shadow-decision-logger`，2026-06-17）的对比口径是 **`live(real) vs replay(both-levers)`**——拿一个**真·live 决策**去对比一个**复盘决策**，把复盘保真误差混进了"lever1 纯增量"。实证：截至 2026-06-20 累积 3809 条影子记录里 37 条 `shadow_holds`（real 开仓但 shadow hold）经本地重放证明**全部不是 lever1 效应**：

- 用同一磁带 bundle 本地重跑 `replay(lever2-only)` vs `replay(both-levers)`，两臂对这 37 条**零分歧**（lever1 真实增量 = 0）。
- 其中 **13/37** 是 `replay(lever2-only)` 也复现不出 live 当时的 accept（复盘失真、方向偏保守 hold）。

即 `shadow − real(live)` 这个差里装的是**复盘失真**，不是 lever1。同仓库已建成的 `perturbation_replay` / `sequential_perturbation` 都用 **replay-vs-replay 两臂同复盘**（系统性偏差在 delta 抵消）+ **baseline 复现自检闸**（replay-baseline 不复现 live 即标 `baseline_mismatch` 排除）规避此问题；影子记录器两条都缺，导致其 lever1 增量结论当前不可信。

> 注：诊断初期曾假设是 ev-gate config parity（live `config.yaml` 把 `ev_winrate_gate_enabled` 改 false 而复盘用 true）。该假设经实测**证伪**——34/37 条记录的 `config_snapshot.ev_winrate_gate_enabled` 正确为 `False`、0 条为 `True`。本 change **不动** ev-gate config。

## What Changes

- 影子对比口径从 `live(real) vs replay(both-levers)` 改为 **`replay(lever2-only baseline) vs replay(both-levers shadow)`**：lever1 增量 = 两臂复盘之差，系统性复盘偏差在 delta 抵消（对齐 `sequential_perturbation` 两臂同估算原则）。
- 新增 **baseline 复现自检闸**：`replay(lever2-only)` 的 accept/reject 必须复现 live record 的 accept/reject，否则该条标 `baseline_mismatch=True`，排除出 lever1 增量统计（对齐 `perturbation_replay` 的 `baseline_mismatch` 守卫）。
- 影子日志 jsonl 新增字段：`baseline_action`、`baseline_gate`、`baseline_mismatch`；`flip_kind` 改为基于 `baseline vs shadow`（而非 `real(live) vs shadow`）。
- 红线不变：observability-only write-only、fail-safe 影子绝不破 live、影子/baseline 复盘绝不 publish 真实 bus / 不下单 / 不 mutate live 状态。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `shadow-decision-logger`: 「对比隔离 lever1 增量」要求从 `影子 − 实盘(live)` 改为 `replay(both-levers) − replay(lever2-only)`，并新增 baseline 复现自检闸 requirement（low-fidelity 记录须标 `baseline_mismatch` 并排除出增量统计）。

## Impact

- `utils/shadow_decision_logger.py`：新增 baseline 复盘臂（再跑一次 `replay_decision(bundle, BASELINE_CONFIG)`）+ 自检逻辑 + 新 record 字段；`flip_kind` 改基于 baseline vs shadow。
- `agents/trading/judge.py`：shadow chokepoint 接线把 live record 的 accept/reject 传入用于自检（不改决策逻辑）。
- `tests/`：新增/更新影子记录器单测（baseline 自检、两臂 delta、`baseline_mismatch` 排除、fail-safe 不破 live）；红线守卫不回归。
- 离线驱动 `cf_shadow_lever1_compare.py`：按新字段筛选（排除 `baseline_mismatch`）。
- observability-only：不碰 live 决策、不改 ev-gate config、live 行为零回归。
