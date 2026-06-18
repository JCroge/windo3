# Tasks

> 详细任务在 comet-build 阶段细化。实现采用 B-revised（持仓标的保留在 active 集）。

## Config 接入（utils/config_loader.py 四段式）
- [x] 新增 `rotation_close_held_enabled` 默认值（默认 `false`）
- [x] 类型校验（bool，经 `_to_bool`；bool 标志不进 HARD_LIMITS）
- [x] env_map 接入 `ROTATION_CLOSE_HELD_ENABLED`
- [x] `_load_yaml` 映射对应 yaml 节点
- [x] `format_banner` 新增「轮换强平持仓: 关闭/开启」展示行

## SymbolRouter 门控（agents/research/symbol_router.py）
- [x] 新增 `_get_position_symbols()`（复用 MultiDataCollector 同款，fail-safe 返回 `[]`）
- [x] `__init__` 读取 `rotation_close_held_enabled` 配置（`_close_held`）
- [x] `_handle_research_result` B-revised：持仓标的保留在 active_symbols（不进 removed、不发 close），removed 只剩无持仓标的；开关 true 时回退旧强平
- [x] retained 标的打日志（`[路由] {symbol} 持仓中，保留监控，出场交 PositionAnalyst`）

## 测试（test_rotation_respect_position_hold.py）
- [x] 有持仓 → 保留 active 不发 close（默认开关）
- [x] 无持仓 → 仍发 close
- [x] 开关 `true` → 回退旧强平行为
- [x] 读持仓 fail-safe（文件缺失/损坏 → `[]`，不抛）
- [x] retained 合并进 active / 既持仓又重选不重复
- [x] config 四段式（默认 / env 覆盖 / yaml 覆盖 / banner）
- [x] main() 登记新用例

## 验证
- [x] `python3 -m pytest test_rotation_respect_position_hold.py -q` 全绿
- [ ] 全量回归无退化
