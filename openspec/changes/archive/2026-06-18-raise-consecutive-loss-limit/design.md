# Design: 连亏熔断阈值 3 → 5（tweak）

## 实现说明

改动落在配置加载层，运行链路逻辑不变。

### 1. yaml 映射（utils/config_loader.py · `_load_yaml`）

在 risk 节点解析末尾增加一条键映射，使 config.yaml 可配置该旋钮：

```python
if 'consecutive_loss_limit' in risk:
    out['consecutive_loss_limit'] = int(risk['consecutive_loss_limit'])
```

- 与同函数内 `max_trade_amount / max_daily_loss` 等映射同构。
- 合并优先级（既有逻辑不变）：RISK_DEFAULTS(3) < config.yaml < 环境变量 `CONSECUTIVE_LOSS_LIMIT`。
- 范围校验沿用 `config_loader` 既有 `(1, 20)` 约束；越界抛 `ConfigError`。

### 2. 配置值（config.yaml · risk 节点）

```yaml
risk:
  ...
  consecutive_loss_limit: 5  # 连续亏损熔断次数（连亏达此数即全平熔断）
```

## 不变量

- Reviewer 在实例化时读取 `config.get('consecutive_loss_limit', 3)`（reviewer.py:55），需重启交易进程才会用新值。
- `daily_pnl_hard_stop = -300` 独立并行兜底不变；小额连亏由连亏线管，大额亏损由日亏线先兜。
- 连亏统计 `_track_consecutive_losses`（24h 窗口、从最新往前数负 PnL）逻辑不变。

## 风险与权衡

- 放宽到 5 增大单日最大回撤的暴露窗口；但 -300 日亏线兜底，整体可控。
- counterfactual-portfolio-sim spec 以「Reviewer 阈值常数」参数化引用该值，不锁定字面量 3，故无需 delta spec。
