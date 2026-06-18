# Tasks

> 详细任务在 comet-build 阶段细化。本清单为 open 阶段初始边界。

## Config 接入（utils/config_loader.py 四段式）
- [ ] 新增 `rotation_close_held_enabled` 默认值（默认 `false`）
- [ ] 范围/类型校验（bool）
- [ ] env_map 接入 `ROTATION_CLOSE_HELD_ENABLED`
- [ ] `_load_yaml` 映射对应 yaml 节点
- [ ] `format_banner` 新增「轮换强平持仓: 关闭/开启」展示行

## SymbolRouter 门控（agents/research/symbol_router.py）
- [ ] 新增 `_get_position_symbols()`（复用 MultiDataCollector 同款，fail-safe 返回 `[]`）
- [ ] `__init__` 读取 `rotation_close_held_enabled` 配置
- [ ] `_handle_research_result`：removed 标的发 close 前查持仓，有持仓且开关为 false 则 skip（仍移出 active_symbols + 发 symbol_update）
- [ ] 跳过平仓时打日志（如 `[路由] {symbol} 有持仓，保留持仓交 PositionAnalyst，仅移出研究集`）

## 测试（test_rotation_respect_position_hold.py）
- [ ] 有持仓 → 不发 close（默认开关）
- [ ] 无持仓 → 仍发 close
- [ ] 开关 `true` → 回退旧强平行为
- [ ] 读持仓 fail-safe（文件缺失/损坏 → `[]`，不抛）
- [ ] main() 登记新用例

## 验证
- [ ] `python3 -m pytest test_rotation_respect_position_hold.py -q` 全绿
- [ ] 全量回归无退化
