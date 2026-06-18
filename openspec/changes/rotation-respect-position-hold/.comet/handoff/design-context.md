# Comet Design Handoff

- Change: rotation-respect-position-hold
- Phase: design
- Mode: compact
- Context hash: c4011987581f46fd158bb1dfe020a5a7bff712acc6fc509b5e0c842b63e1674e

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/rotation-respect-position-hold/proposal.md

- Source: openspec/changes/rotation-respect-position-hold/proposal.md
- Lines: 1-36
- SHA256: edeb405810765f81f788c662f83cff35a4d84459c35b775881da5189bf001afa

```md
## Why

标的轮换（SymbolRouter）在把某标的轮出活跃研究集时，会**无条件**对其发平仓指令，绕过持仓研判官（PositionAnalyst）的出场决策。这导致 PositionAnalyst 判 hold 的持仓被研究层越权强平——既冗余又有害。

实证案例（2026-06-18 XLM-USDT 多单）：

- PositionAnalyst 持仓期三次研判全判 **hold**（12:31 `add→hold` / 13:31 `add→hold` / 14:31 `hold→hold`，末次距平仓仅 12 分钟）。
- 14:43 SymbolRouter 将 XLM 轮出活跃池，`agents/research/symbol_router.py:57-59` 对所有 `removed` 标的无条件标 `close_at_market`，直发 `trade_decision(action=close, confidence=100, size_pct=1.0)`，绕过 hold 裁决强平，仅 **+0.68%** 擦平手续费。
- 事后 XLM 继续上涨 **+1.33%**（`data/klines_1s.db` 实证），PositionAnalyst 判 hold 是对的。

根因：`symbol_router.py` git 历史 3 个 commit **从未查过持仓**——轮换路径与持仓研判从一开始就没握手，属架构缺口而非回归。在策略衰减期，这种"对的持仓被轮换砍掉"在系统性削减趋势策略赖以为生的右尾收益。

## What Changes

- SymbolRouter 在轮换发平仓指令前，先判断该 `removed` 标的**是否有持仓**；有持仓则**跳过平仓**，仅将其移出 `active_symbols`（继续发 `symbol_update`）。出场决策完全交还 PositionAnalyst。
- SymbolRouter 新增 `_get_position_symbols()`，复用 MultiDataCollector 同款实现（读 `utils.state_paths().positions`，fail-safe 返回 `[]`）。
- 新增 config 开关 `rotation_close_held_enabled`（默认 `false` = 不强平持仓 = 新行为），保留 env 回滚阀，启动 banner 展示，经 `utils/config_loader.py` 四段式接入。
- **行为变更**：开关默认值下，轮换不再强平任何已持仓标的。无持仓标的的轮换行为不变。需重启交易进程生效。

## Capabilities

### New Capabilities

- `symbol-rotation-position-guard`: 标的轮换时对已持仓标的的保护契约——轮换只管理研究/扫描集，不得越权平仓；持仓出场决策归 PositionAnalyst。覆盖"有持仓则跳过平仓""无持仓维持原平仓行为""config 开关与 fail-safe 语义"。

### Modified Capabilities

<!-- 无既有 spec 的需求变更：SymbolRouter 此前无 spec 覆盖，本变更新建 capability。 -->

## Impact

- **代码**：`agents/research/symbol_router.py`（主改，新增持仓查询 + 平仓门控）、`utils/config_loader.py`（`rotation_close_held_enabled` 四段式接入：RISK/ROUTER_DEFAULTS + 范围校验 + env_map + _load_yaml + format_banner）。
- **测试**：新增 `test_rotation_respect_position_hold.py`（有持仓跳过平仓 / 无持仓仍平仓 / 开关开启回退旧行为 / 读持仓 fail-safe）。
- **共享状态**：`utils.state_paths().positions` 成为 SymbolRouter / MultiDataCollector / PositionAnalyst 三方共读的持仓真相源（已验证 PositionAnalyst 每轮对账，与 active_symbols 无关）。
- **运维**：observability/行为变更，需重启 `run_agents.py` 生效；启动 banner 新增「轮换强平持仓: 关闭」一行供核对。env `ROTATION_CLOSE_HELD_ENABLED=true` 可回滚至旧强平行为。
- **下游不变**：PositionAnalyst、Executor、交易所 SL/TP 实单均已覆盖全部出场路径，无需改动。
```

## openspec/changes/rotation-respect-position-hold/design.md

- Source: openspec/changes/rotation-respect-position-hold/design.md
- Lines: 1-49
- SHA256: 39b337cac6da656f26b3ff4bd53eeb78fe2ffeb7da125165b9e92d0d0790c290

```md
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
```

## openspec/changes/rotation-respect-position-hold/tasks.md

- Source: openspec/changes/rotation-respect-position-hold/tasks.md
- Lines: 1-27
- SHA256: b9f73c9fd638d54c6ac60f1706ef416090230e0b312c518bfb889034b3b0c8a3

```md
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
```

## openspec/changes/rotation-respect-position-hold/specs/symbol-rotation-position-guard/spec.md

- Source: openspec/changes/rotation-respect-position-hold/specs/symbol-rotation-position-guard/spec.md
- Lines: 1-49
- SHA256: 90d8ed07f76e279c917de88dae858ea1551ccbdb011ec086f073afb41ec4d5f4

```md
## ADDED Requirements

### Requirement: 轮换保留已持仓标的于活跃集

标的轮换（SymbolRouter）SHALL 在轮换时将**仍有持仓**的标的保留在活跃标的集（active_symbols）中，而非将其移出并强平。持仓标的的出场决策 MUST 完全交由 PositionAnalyst（含其 SL/TP/趋势反转硬覆盖）与交易所挂单负责，SymbolRouter MUST NOT 因轮换对持仓标的发出平仓指令。

持仓真相源 SHALL 为 `utils.state_paths().positions`（与 MultiDataCollector、PositionAnalyst 共读的同一文件）。

#### Scenario: 持仓标的被研判轮出但仍被保留监控
- **WHEN** 轮换计算出某标的不在新研判选集中，但该标的在 positions 文件中仍有持仓
- **THEN** 该标的 SHALL 保留在 active_symbols 中（不进入 removed）
- **AND** SymbolRouter MUST NOT 对该标的发送 `trade_decision(action=close)`
- **AND** symbol_update 广播的 active_symbols MUST 包含该标的，使下游采集/分析/看护链路持续监控

#### Scenario: 无持仓标的维持原轮换平仓行为
- **WHEN** 轮换计算出某标的不在新研判选集中，且该标的无持仓
- **THEN** 该标的 SHALL 进入 removed
- **AND** SymbolRouter SHALL 对其发送 `trade_decision(action=close)`（与变更前行为一致）

### Requirement: 持仓查询 fail-safe 退化为旧行为

SymbolRouter 读取持仓的 `_get_position_symbols()` MUST 为 fail-safe：positions 文件缺失、不可读或 JSON 损坏时 SHALL 返回空列表且不抛异常。在持仓信息不可得时，系统 SHALL 退化为旧的轮换强平行为（持仓标的进入 removed 被平为 flat），以保证绝不产生"已开仓但无监控"的无人看管持仓。

#### Scenario: positions 文件缺失
- **WHEN** positions 文件不存在
- **THEN** `_get_position_symbols()` SHALL 返回空列表
- **AND** 轮换 SHALL 按"无持仓"路径处理所有 removed 标的（旧强平行为）

#### Scenario: positions 文件损坏
- **WHEN** positions 文件存在但内容无法解析为 JSON
- **THEN** `_get_position_symbols()` SHALL 返回空列表且记录 warning，不抛异常
- **AND** 轮换流程 SHALL 继续，不被阻断

### Requirement: 行为开关与运维可核对性

该保护行为 SHALL 受 config 开关 `rotation_close_held_enabled` 控制，经 `utils/config_loader.py` 接入（默认值、类型校验、环境变量覆盖、yaml 映射、启动 banner 展示）。默认值 SHALL 为 `false`（= 不强平持仓 = 新保护行为）。环境变量 `ROTATION_CLOSE_HELD_ENABLED=true` SHALL 可回滚至旧强平行为。启动 banner SHALL 展示该开关状态以便重启后核对。

#### Scenario: 开关默认关闭（保护生效）
- **WHEN** 未显式配置 `rotation_close_held_enabled`
- **THEN** 其值 SHALL 为 `false`
- **AND** 轮换对持仓标的 SHALL 执行保留（不强平）

#### Scenario: 开关开启回退旧行为
- **WHEN** `rotation_close_held_enabled` 配置为 `true`
- **THEN** 轮换对被轮出的持仓标的 SHALL 发送平仓指令（旧强平行为）

#### Scenario: 启动 banner 展示开关状态
- **WHEN** 交易进程启动并打印风控 banner
- **THEN** banner SHALL 包含「轮换强平持仓: 关闭/开启」一行，反映当前开关值
```

