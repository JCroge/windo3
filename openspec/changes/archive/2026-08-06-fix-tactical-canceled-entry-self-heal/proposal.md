## Why

Tactical V2 将一个 OKX 已明确取消且零成交的 PUMP 限价单继续解释为仍有 200 张余量，随后每个恢复周期重复撤单并收到 `51400 OrderNotFound`。该错误使 `entry_cancel_unproven` 完整性熔断无法自愈、V2 状态快照持续陈旧并阻止所有新 Tactical 入场，必须立即修复。

## What Changes

- 将 OKX 明确终态的 Tactical entry 规范化为零剩余量，同时保留真实部分成交量。
- 让撤单路径在“查询后、撤单前订单已经终态”的竞态中执行精确 `clOrdId` 回查；只有回查证明终态或无剩余量才视为安全成功。
- 增加生产 PUMP 形态的回归测试：已取消零成交订单不得再次撤单，撤单 `OrderNotFound` 竞态必须通过终态回查收敛。
- 部署时只重启 Main；Sidecar 保持驻留且 admission 继续关闭。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `tactical-intent-lifecycle`: 明确已终态 entry 的剩余量语义，以及撤单 `OrderNotFound` 竞态必须通过精确身份回查收敛。

## Impact

- 代码：`executor.py::query_tactical_entry()` 与 `cancel_tactical_entry()`。
- 测试：`tests/test_tactical_v2_exchange.py`。
- 运行态：修复部署并重启 Main 后，启动恢复应将 PUMP intent 终结为 `expired`、清除 Tactical integrity halt，并恢复新仓 admission。
- 无 API、配置、依赖、数据库或持久化 schema 变更。
