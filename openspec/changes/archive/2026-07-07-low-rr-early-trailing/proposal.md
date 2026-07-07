## Why

Low RR 槽（low_rr_extra / long_bullish_low_rr / long_aligned_low_rr）的退出机制与主仓位相同，但其 TP1 距离远（R:R 1.21-1.48），实际极少触达 TP1（全量回测 TP1 命中率仅 17.9%），导致方向正确的单大量利润回吐后止损。CF 实验室 38,648 条信号回测证实当前机制均R -0.071（负期望），加提前 trailing 后均R +0.128（正期望），胜率从 53.9% 提升至 68.6%。

## What Changes

- Low RR 槽持仓在浮盈达 +0.5R 时即启动 trailing stop，trailing 距离 0.3R
- 不再等待 TP1 触发才激活 trailing，TP1 仍保留作为全平触发条件
- Position dict 增加 `slot` 字段标记开仓槽类型，供退出逻辑判断
- 主仓位（size=30, lev=10x）退出逻辑完全不变

## Capabilities

### New Capabilities
- `low-rr-early-trailing`: Low RR 槽独立的提前 trailing 退出机制，+0.5R 启动、0.3R 距离跟踪

### Modified Capabilities

## Impact

- `executor.py`: `_update_trailing()` 增加 low_rr 槽分支逻辑
- `agents/trading/judge.py`: 开仓时在 position dict 写入 `slot` 标记
- 测试：新增 trailing 参数化测试覆盖 low_rr 路径
- 不影响现有交易所交互、风控逻辑、主仓位退出
