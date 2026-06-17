# Proposal: fix-lever2-low-rr-sizing-tp1

## 问题

`trend-entry-levers-default-on` 把 lever2（`ladder_rr_enabled`）默认开后，code review 发现一个**被低估的 live 敞口放大**：低 R:R 保护性缩仓判定被错误耦合到阶梯口径。

## 根因

`effective_risk_reward_ratio`（lever2 开时 = 阶梯值）不只喂 R:R 地板 gate，还喂了**低 R:R 保护性缩仓块**（`judge.py:1486` 主路径 + `3038` `_apply_regime_policy`）：

```python
rr = plan['effective_risk_reward_ratio']   # lever2 开 → 阶梯值
if rr < 1.5 and rr_policy in low_rr_policies:
    plan['size_usdt'] *= rr_scale * low_rr_max_position_pct   # 缩仓
    plan['leverage'] = min(leverage, low_rr_max_leverage)     # 降杠杆
```

同一笔 `long_aligned_low_rr` 趋势单：lever2 关时 TP1 口径 rr=1.4 → `1.4<1.5` → 缩仓+降杠杆（保护）；lever2 开时阶梯 rr≈1.64 → `1.64<1.5` 为假 → **跳过保护 → 全仓+满杠杆**。在 300u/5x/单笔 30u 的实盘上是实打实的敞口放大，且 rejected 流 A/B（+0.181R/簇）只验了开/不开、**没验仓位放大后的期望**。

## 目标

**保护性缩仓判定与"多开仓"解耦**：地板 gate 继续用阶梯口径（lever2 仍多开仓），但低 R:R 缩仓判定 + `rr_scale` 计算改用 **TP1 口径**（`effective_rr_tp1`，已存在 `judge.py:3601`），恢复 pre-lever2 的保护性 sizing 行为。等影子记录器②的前向数据足够再决定是否松绑缩仓。

## 修复（单一概念）

`judge.py` 两处 low_rr 缩仓块：缩仓 `if` 条件与 `rr_scale` 用 `plan.get('effective_rr_tp1', rr)` 而非 `rr`。地板 gate（`rr < min_rr`）不变。

## 范围 / 非目标

- 范围：`judge.py` 2 处 + 测试 + delta spec（ladder-weighted-rr 加缩仓口径要求）。
- 非目标：不改地板 gate（lever2 多开仓不变）、不改 lever2 本体口径、不动 lever1（仍关）。
- Judge 策略逻辑改动 → event_backtest 非回归（结构性已知失真同 lever2，主验证=回归 + 该 change 是更保守的恢复）。
