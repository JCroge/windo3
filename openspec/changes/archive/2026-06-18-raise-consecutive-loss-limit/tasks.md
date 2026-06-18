# Tasks: 连亏熔断阈值 3 → 5

- [x] 1. `utils/config_loader.py` 的 `_load_yaml` 增加 `consecutive_loss_limit` 的 yaml 映射（int 转换）
- [x] 2. `config.yaml` 的 `risk` 节点新增 `consecutive_loss_limit: 5`
- [x] 3. 验证 `load_config()` 读到 `consecutive_loss_limit == 5`，且不影响 `daily_pnl_hard_stop`
