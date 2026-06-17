## ADDED Requirements

### Requirement: 低 R:R 保护性缩仓判定用 TP1 口径（与阶梯解耦）

阶梯加权 effective_rr（lever2）SHALL 只用于 **R:R 地板 gate**（判定是否开仓）。低 R:R 保护性缩仓/降杠杆判定（`low_rr_policies` 命中时的 `size_usdt` 缩放、`leverage` 上限、`rr_scale` 计算）MUST 用 **TP1 口径 effective_rr**（`effective_rr_tp1`），不得用阶梯值——否则阶梯抬高的 R:R 会把本应保护性缩仓的低-R:R 趋势单松绑成全仓满杠杆，意外放大敞口。地板 gate 与缩仓判定 SHALL 解耦：lever2 多开仓不变，保护性 sizing 不被阶梯松绑。

#### Scenario: 阶梯抬高仍保护性缩仓

- **WHEN** lever2 开、某 `long_aligned_low_rr` / `long_bullish_low_rr` 单的阶梯 effective_rr ≥ 1.5 但 TP1 口径 effective_rr < 1.5
- **THEN** 该单仍走低 R:R 保护性缩仓（`size_usdt` 缩放 + `leverage` 上限），不因阶梯口径松绑为全仓满杠杆

#### Scenario: lever2 关时零回归

- **WHEN** lever2 关（`ladder_rr_enabled=False`）
- **THEN** `effective_rr_tp1 == effective_risk_reward_ratio`，缩仓行为与改动前完全一致
