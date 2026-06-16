## MODIFIED Requirements

### Requirement: baseline 序列保真自检（delta 信任锚）
系统 SHALL 统计 baseline 臂的每步决策与录下决策的一致率（`baseline_fidelity`）；比对 SHALL 在 **gate-level** 进行——复现须触达同一 gate（accept，或同一 `reject_reason` 类别），不得把"换了个 gate 拦下"误判为复现。一致率低于阈值时标 `untrustworthy` 并拒给 delta 结论。

#### Scenario: 高一致率 delta 可信
- **WHEN** baseline-sim 决策与录下决策在 gate-level 一致率 ≥ 阈值（默认 0.8）
- **THEN** 系统 SHALL 给出 delta 结论，并随报告报出 `baseline_fidelity`

#### Scenario: 低一致率拒答
- **WHEN** baseline-sim 与录下决策 gate-level 一致率 < 阈值
- **THEN** 系统 SHALL 标 `untrustworthy` 并 SHALL NOT 给 delta 方向结论

#### Scenario: 换 gate 拦计为不复现
- **WHEN** 录下决策为某 gate 拒（如 `rr_below_floor`），baseline-sim 却被另一 gate 拒（如 `ev_gate` / `daily_bearish_required`）或反之
- **THEN** 系统 SHALL 将该步计为不复现（计入 divergence / 拉低 baseline_fidelity），SHALL NOT 因二者同属"非-accept"类即算复现
