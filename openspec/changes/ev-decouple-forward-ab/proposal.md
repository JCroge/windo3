## Why

`ev-gate-winrate-decouple`（2026-06-18 上线）把开仓 EV 门的胜率因子剔除：`ev_winrate_gate_enabled=false` 时 `_get_p_win` 返回固定 0.55、跳过胜率<40% 硬阈值，只保留经济门。复盘最近 8 笔开仓发现它们**全是 neutral 趋势 + 勉强压地板 R:R~1.5 的边缘单**，`p_win=0.55 fixed` 放行，旧胜率门（真实胜率 21%<40%）本会 EV 拒；放行后实盘**净亏 ~−16U/8 笔**（赢小 +0.2/+0.9、亏大 −4.4/−10）。

端到端验证（磁带 gate-toggle 复盘）证实这不是边缘现象：**64 条 replayable accept 中，baseline 自检忠实 52 条，其中 36 条（69%）是"解耦放行"——旧胜率门会以 `ev_gate` 拒**。即近期大多数开仓只因解耦才过门。需量化这批解耦放行单的前向期望，决定是否回滚或加约束（如 neutral 趋势不享解耦）。

本 change **只量化、不改 live**；证据足够再另起 change 决定回滚/约束。

## What Changes

- 新增 observability-only 离线驱动 `cf_ev_decouple_ab.py`（镜像 `cf_lever2_rejected_ab.py`），对决策磁带的 accept 流做 gate-toggle 两臂复盘 + 前向结算：
  - **baseline 自检臂** `replay(ev_winrate_gate_enabled=False)`（= live 现配置）必须复现 live accept，否则该条复盘失真、排除（复用 `fix-shadow-logger-replay-baseline-parity` 的 baseline 自检思想）。
  - **反事实臂** `replay(ev_winrate_gate_enabled=True)`（= 旧胜率门）翻成 reject(ev_gate) = "解耦放行"。
  - 解耦放行簇 vs 双门皆过簇，各用 `resolve_counterfactual`+klines **统一 CF 结算**（TP1 保守口径、含亏单），系统性偏差在两桶 delta 抵消。
  - real PnL（实际开仓 ~8 笔，经 symbol+ts 模糊 join lifecycle）作**次要 sanity 交叉验证**。
  - 簇去重后经 `cf_honesty_gate` 诚实门，薄样本拒答。
- 输出报表：解耦放行簇数 / 前向净 R / vs 双门皆过基线 / coverage 受限说明。

## Capabilities

### New Capabilities

- `ev-decouple-forward-ab`: observability-only 量化"胜率解耦放行单"前向期望的离线驱动（gate-toggle 两臂复盘 + baseline 自检 + CF 结算 + 诚实门）。

### Modified Capabilities

（无——不改 `open-gate-ev` 门逻辑、不改 live）

## Impact

- 新增 `cf_ev_decouple_ab.py`（repo 根，与 cf_lever2_rejected_ab.py / cf_shadow_lever1_compare.py 同级）。
- 复用：`utils/decision_replay.py::replay_decision`（gate-toggle 经 perturbation override）、`utils/counterfactual_pnl.py::resolve_counterfactual`、`utils/cf_honesty_gate.py::summarize_bucket`、`data/decision_replay_tape.jsonl` / `data/klines*.db` / `data/live_position_lifecycle.json`。
- 红线：observability-only write-only——输出严禁任何交易决策/风控路径消费、绝不自动改线上 config、绝不下单。
- 无 live 行为改动、无库机制改动（纯新驱动 + 测试）。
