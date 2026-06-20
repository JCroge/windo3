## 高层架构决策（深度技术设计见 comet-design 的 Superpowers Design Doc）

### 测量方法学

```
对磁带每条 accept 决策(replayable):
  ① baseline 自检臂  replay(ev_winrate_gate_enabled=False)  # = live 现配置
       └─ 复现 live accept? 否 → 复盘失真, 排除
  ② 反事实臂        replay(ev_winrate_gate_enabled=True)   # = 旧胜率门
       └─ 翻 reject(ev_gate)? 是 → "解耦放行" ; 否 → "双门皆过"
  ③ 簇去重(同 symbol 连续重复评估归一簇, 同 cf_lever2_rejected_ab)
  ④ 两桶各 resolve_counterfactual+klines 统一 CF 结算(TP1保守含亏单) → 净R
  ⑤ cf_honesty_gate 诚实门: 薄样本拒答
  ⑥ real PnL(实际开仓~8, symbol+ts 模糊 join lifecycle) 作次要 sanity 交叉
```

### 关键决策

1. **CF 结算为主、real PnL 为辅**（用户已确认）：解耦放行单虽真实开了仓，但 lifecycle 无 request_id（只能 symbol+ts 模糊 join）、样本仅 ~8、含 pending external_close。统一 CF 口径结算两桶使系统性偏差在 delta 抵消（同 `cf_lever2_rejected_ab` / `sequential_perturbation` 两臂同估算原则），N 更大、口径一致。real PnL 作 sanity 交叉锚，不作主判据。

2. **baseline 自检闸不可省**：端到端验证显示 64 accept 中 12 条（~23%）复盘失真（与影子记录器诊断的失真率一致）。不自检则解耦放行分类被复盘失真污染。复用 `fix-shadow-logger-replay-baseline-parity` 刚确立的 baseline 二元 accept/reject 自检。

3. **对比桶 = 双门皆过**：解耦放行桶（gate-on→reject）vs 双门皆过桶（gate-on 仍 accept）。两桶净 R 的 delta 才是"解耦特有边缘单"的增量效应；绝对值受 CF 口径限制不单独采信。

4. **纯新驱动、零库改动**：复用 replay_decision（gate toggle 经 perturbation override，`_EPOCH_FALLBACK` 已含 ev_winrate_gate_enabled）+ resolve_counterfactual + cf_honesty_gate。无新机制、无 live 改动。

### 端到端可行性（explore 已实测）

`replay(accept, {ev_winrate_gate_enabled:True/False})` 跑通：64 accept → 52 忠实 / 12 失真，其中 **36 解耦放行（全 ev_gate）**。方法可行、population 非平凡。

### 红线（不变）

- observability-only write-only：输出严禁交易决策/风控路径消费、绝不下单、绝不自动改 config、绝不 mutate live。
- 复盘臂复用 replay_decision 隔离机器（publish 绝不进真实 bus）。

### 非目标

- 不改 `open-gate-ev` 门逻辑、不回滚/约束解耦（证据足够后另起 change）。
- 不追求 real PnL 精确归因（无 request_id，模糊 join 仅作 sanity）。
- 不解决 klines coverage 受限（如实报跳过数，不外推）。
