# Design (high-level): restore-llm-rsi-veto-power

> 高层架构决策。详细机制 + delta spec 在 comet-design 阶段产出（`docs/superpowers/specs/`）。

## 决策 1：触发 = 双信号合流（已定）

veto 仅在两个**相互独立**的反转信号同时成立时触发：

```
待开方向 dir ∈ {long, short}
  veto_trigger =
       LLM_counter(dir)          # LLM 给出与 dir 相反的开仓建议（强冲突）
   AND RSI_div_against(dir)      # bearish_div(若 dir=long) 或 bullish_div(若 dir=short)
```

- 合流（AND）→ 误杀最少，适配策略衰减期"先立足再放宽"。
- LLM 单边方向实测不可靠（4/4 反），故不让 LLM 单独 veto；RSI 背离单独也不够强 → 必须共振。

## 决策 2：动作 = 转 deferred_pullback（已定）

触发后**不硬拒**，复用已有 `deferred_pullback` 路径让该笔等回调再评估，保留回调后入场的机会，与 regime-aware-long-entry-guard 同哲学。

## 决策 3：不动 scoring（±35 / 背离压制留病根1，已定）

veto 读 `tech.momentum.rsi_divergence` 原始布尔 + LLM action，**不改 `_compute_score` 任何权重**。归因可独立度量 veto 效果。

## 插入点（待 comet-design 对现行代码定稿）

候选：judge.py:1295-1310「has_rule_signal AND llm 反向 → 缩仓60%」分支旁——该处已识别 LLM 强冲突，叠加 RSI 背离判定后改走 defer。需确认主路径 + 三条 deferred 路径（15m/pullback/chase）的覆盖与单点收口（避免病根3 P1-03 那种"第二份内联实现"红线）。

## 配置（走 config_loader 四段式）

- 总开关 `llm_rsi_reversal_veto_enabled`（可回退）。
- 可能的 LLM 置信下限阈值（避免低置信 LLM 反向也触发）——comet-design 定。
- default 值与缓进策略：**event_backtest 结果出来后定稿**。

## 归因（observability）

新增 attribution 字段：`reversal_veto_triggered`、`reversal_veto_llm_action`、`reversal_veto_rsi_div`、`reversal_veto_deferred_dir`。放行与 defer 双路径都写，供 Reviewer 分桶与 backtest pre/post 对比。

## 验证（红线）

- **event_backtest**：构造/复用含"追势买在反转点"的历史样本，对比开 veto 前后该类样本的 PnL/胜率分布；通过标准 comet-design 定（至少：被 veto 样本集净 PnL 不变差、整体不引入新回归）。
- 单元测试：合流触发 defer / 仅 LLM 反向不触发 / 仅 RSI 背离不触发 / 开关 off 回退 / 主路径与 deferred 路径 parity。

## 风险

- 过冻：合流罕见，风险低；开关 + 阈值兜底。
- 单点收口：必须避免多份内联实现（参考既往短单 gate 红线整改）。
