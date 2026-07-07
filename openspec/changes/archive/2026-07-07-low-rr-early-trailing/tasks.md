## 1. Position Slot 标记

- [x] 1.1 在 Judge 开仓路径（`_open_position` 或等效）写入 `slot` 字段到 position dict，low_rr 槽写 `"low_rr"`，主槽写 `"main"` 或不写
- [x] 1.2 确认 executor `open_position()` / `_register_position()` 保留并持久化 `slot` 字段

## 2. Early Trailing 逻辑

- [x] 2.1 在 executor config 增加 `low_rr_trail_start_r`（默认 0.5）和 `low_rr_trail_dist_r`（默认 0.3）参数
- [x] 2.2 在 `_update_trailing()` 开头检测 `position.get('slot_type') == 'low_rr_extra'`，命中时走独立 early trailing 分支（+0.5R 启动，0.3R 距离），跳过 BE/锁利逻辑
- [x] 2.3 Early trailing 分支实现：更新 highest_price → 计算 trail_sl = highest - R*dist → ratchet（只向有利方向移动）→ 调用 `_move_sl()`

## 3. 测试

- [x] 3.1 单元测试：low_rr 槽 +0.5R 时 trailing 激活，SL 移动到 highest - 0.3R
- [x] 3.2 单元测试：low_rr 槽 trailing SL 只向有利方向 ratchet
- [x] 3.3 单元测试：low_rr 槽 TP1 仍能触发 partial_tp_1
- [x] 3.4 单元测试：main 槽不受影响，走原 BE/锁利/TP1-trailing 路径
- [x] 3.5 回归：运行全量 pytest，确认基线不降（459 passed）

## 4. 验证

- [x] 4.1 CF 回测验证通过：executor._update_trailing 实际代码路径 38672 样本，均R +0.2486（优于独立脚本 +0.128 基准，因 tick 粒度更高 trailing ratchet 更有效），73.5% 胜率，TP1 5.1%/SL 80.2%/超时 14.7%
