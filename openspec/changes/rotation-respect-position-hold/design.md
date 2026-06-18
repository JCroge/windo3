# Design (高层)

> 深度技术设计见 comet-design 阶段产出的 Design Doc（`docs/superpowers/specs/`）。本文件只记高层架构决策与方案选型。canonical spec = openspec。

## 架构决策

**方案 B-revised（采纳）：持仓标的保留在 active 集。** SymbolRouter 让有持仓的标的**留在 active_symbols**（不进 `removed`），既不强平、又让整条监控链保持与持仓前一致的监控状态；持仓出场决策完全归 PositionAnalyst。

否决的备选：
- **方案 A（订阅裁决门控）**：PositionAnalyst 每小时才跑，裁决最旧 ~60min；且 close/reduce 时 PA 自发 `trade_decision`，门控冗余。
- **方案 B（移出 active 但跳过平仓）**：监控连续性依赖 DataCollector 独立的 `position_symbols` 二次合并（`multi_data_collector.py:89`），该合并在读 positions 文件失败时返回 `[]` 会让持仓标的**静默掉出采集** → 产生"不强平但也不监控"的无人看管持仓，比旧行为更糟。B-revised 把持仓标的的保留上提到 SymbolRouter，全系统单一 active 集，消除该缺口。
- **方案 C（A+B 混合）**：高复杂度无收益。

**核心论据**：PositionAnalyst 已完整拥有出场决策权（hold/add/reduce/close 四档 + SL/TP/趋势反转硬覆盖，`action≠hold` 时自发 `trade_decision`），交易所还挂 SL/TP 实单。所有出场路径已覆盖 → 轮换强平要么冗余、要么有害。持仓标的留在 active 集后 Judge 只打"开仓冷却中"不会重复开仓（今日 XLM 持仓期实盘验证安全）。

## 数据流（B-revised）

```
positions 文件 (utils.state_paths().positions)  ← 单一真相源
   ├─ MultiDataCollector._get_position_symbols()  → "持仓补充" 继续采集
   ├─ PositionAnalyst 每轮对账 _positions (与 active_symbols 无关)
   └─ SymbolRouter._get_position_symbols()  ← 【新增】

SymbolRouter._handle_research_result:
   held = _get_position_symbols()                          # fail-safe → []
   active_symbols = new_symbols ∪ (held 中不在 new 的)      # 持仓标的【保留】→ 全链持续监控
   removed = old - new - held                              # 只剩无持仓标的
   removed 标的 → 发 trade_decision close（原行为不变）
   开关 _close_held=true → 退回旧行为（持仓也进 removed 被平）
```

fail-safe：读持仓失败 → held=[] → 持仓标的进 removed 被平（旧行为，flat 安全）。永不产生"开着但没人看"的仓。

## 关键技术选型

- **持仓查询**：复用 MultiDataCollector 同款 `_get_position_symbols()`，读 positions JSON 文件，fail-safe 返回 `[]`（读失败时退化为旧行为，不阻断轮换）。零新依赖、零跨层调用、零循环。
- **config 开关**：`rotation_close_held_enabled`（默认 `false` = 不强平持仓 = 新行为），经 `utils/config_loader.py` 四段式接入（defaults + 范围校验 + env_map + _load_yaml + format_banner），env `ROTATION_CLOSE_HELD_ENABLED=true` 回滚阀，启动 banner 展示。

## 安全边界（已验证）

PositionAnalyst 每轮从同一 positions 文件对账 `_positions`（`position_analyst.py:161-172`），持仓标的离开 active_symbols 后照样被研判（"持仓补充"持续喂 price_tick），出场该触发时 PA 自发指令。方案 B 不会导致持仓"无人看管"。

## 测试策略

- 有持仓的 removed 标的 → 不发 close（默认开关）。
- 无持仓的 removed 标的 → 仍发 close。
- `rotation_close_held_enabled=true` → 回退旧行为（持仓也强平）。
- 读持仓 fail-safe：positions 文件缺失/损坏 → 返回 `[]`，不抛异常。
- config 四段式：默认值、越界校验、env 覆盖、banner 展示。
