## 1. 回归测试

- [x] 1.1 在 `tests/test_tactical_v2_exchange.py` 增加已取消零成交订单和撤单 `OrderNotFound` 竞态测试，并确认在修复前按预期失败。

## 2. 根因修复

- [x] 2.1 在 `executor.py` 统一终态订单余量语义，并让撤单异常通过精确订单身份回查收敛；运行 Tactical V2 exchange/exit/crash-recovery 专项测试。

## 3. 验证与部署

- [ ] 3.1 运行完整回归与严格 OpenSpec 校验，提交 hotfix；备份并同步云端，保持 Sidecar 不动、仅重启 Main，验证 PUMP 幽灵状态和 integrity halt 自动清除且错误循环停止。
