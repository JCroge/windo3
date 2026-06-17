# Design: fix-lever2-low-rr-sizing-tp1

修复方案（hotfix，单一方案）。

## 改动

两处 low_rr 缩仓块（`judge.py:1485-1492` 主路径 + `3037-3044` `_apply_regime_policy`），把缩仓判定与 `rr_scale` 的输入从 `rr`（=`effective_risk_reward_ratio`，lever2 开时为阶梯值）改为 TP1 口径：

```python
rr_for_sizing = plan.get('effective_rr_tp1', rr)   # 缩仓口径用 TP1, 不被 lever2 阶梯松绑
if (rr_for_sizing < 1.5 and is_long and rr_policy in low_rr_policies and not plan.get('is_probe')):
    rr_scale = min(0.8, max(0.4, (rr_for_sizing - 1.2) / 0.3))
    ...
```

- 地板 gate `if rr < min_rr`（用阶梯 `rr`）**不变** → lever2 继续多开仓。
- 缩仓/降杠杆判定与 scale 用 `effective_rr_tp1` → 保护性 sizing 恢复 pre-lever2 行为。
- `effective_rr_tp1` 已由 `_build_plan` 写入 plan（`judge.py:3601`），无需新增计算；`plan.get(..., rr)` 兜底（缺字段时退回旧行为，安全）。

## 不变量

- lever2 关时：`effective_rr_tp1 == effective_risk_reward_ratio` → 行为与改动前完全一致（零回归）。
- lever2 开时：地板用阶梯（多开仓），缩仓用 TP1（保护不松绑）—— 二者解耦。

## 测试

- 单测：构造 lever2 开 + 阶梯 rr≥1.5 但 TP1 rr<1.5 + low_rr_policy 的 plan → 仍走缩仓（size/leverage 被缩）。
- 回归：全量 pytest 绿（lever2 关时零回归）。
- event_backtest 非回归（结构性失真同 lever2）。
