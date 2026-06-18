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
