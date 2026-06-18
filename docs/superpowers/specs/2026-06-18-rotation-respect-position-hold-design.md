---
comet_change: rotation-respect-position-hold
role: technical-design
canonical_spec: openspec
---

# Design Doc: 轮换尊重持仓研判（B-revised）

> 需求事实源 = OpenSpec（proposal + `specs/symbol-rotation-position-guard/spec.md`）。本文件只做技术设计，不重复定义需求。

## 1. 问题与根因（摘自 proposal）

SymbolRouter 轮换时对所有 `removed` 标的无条件发 `trade_decision(action=close, confidence=100)`，绕过 PositionAnalyst 的出场研判强平持仓。根因：`symbol_router.py` 从未查过持仓，研究层越权替交易层做了无依据的平仓。实证：2026-06-18 XLM 多单三次研判判 hold 仍被轮换强平在低点，事后涨 +1.33%。

## 2. 方案选型：B-revised（持仓标的保留在 active 集）

### 否决的方案
- **方案 A（订阅 position_review 门控）**：PositionAnalyst 每小时才跑，裁决最旧 ~60min；且 close/reduce 时 PA 自发指令，门控冗余。
- **方案 B（移出 active 但跳过平仓）**：监控连续性依赖 DataCollector 独立的 `position_symbols` 二次合并（`multi_data_collector.py:89`）。该合并在 `_get_position_symbols()` 读 positions 文件失败时返回 `[]`，会让持仓标的**静默掉出采集** → 产生"不强平但也不监控"的无人看管持仓，比旧行为更糟。

### 采纳：B-revised
持仓标的**保留在 active_symbols** 里（不进 `removed`），而非"移出后跳过平仓"。整条监控链（DataCollector / TechAnalyst / Judge / PositionAnalyst）共享同一个 active 集，持仓标的的监控状态与持仓前完全一致。

## 3. 监控链路（已逐环验证）

```
SymbolRouter.active_symbols (B-revised: new_symbols ∪ held)
   │  symbol_update
   ▼
DataCollector._active_symbols = set(new_symbols + position_symbols)   # 既已并入持仓(line 89/105)
   ├─ _tick_price/mid/low/slow 遍历 → market_data + price_tick 对持仓标的照发
   ▼
TechAnalyst (订阅 market_data:*)
   ├─ symbol_update.removed → pop strategy state(line 62)
   └─ 下一条 market_data → _get_strategy 懒重建(line 44) → tech_analysis 继续产出
   ▼
PositionAnalyst (订阅 tech_analysis + price_tick + 每轮对账 positions 文件 line 161-172) → 持续研判
Judge (_open_positions 从 positions.json 维护 line 317-326；symbol_update 只清入场缓存不动持仓) → SL/TP 看护
```

B-revised 让持仓标的根本不进 `removed`，因此 TechAnalyst 的 pop / Judge 的入场缓存清理都不对它触发——它就是一个普通的 active 标的，与持仓前无差别。

## 4. 实现

### 4.1 SymbolRouter（`agents/research/symbol_router.py`）

`_handle_research_result` 在算出 `new_symbols` 后：

```python
held = set(self._get_position_symbols())              # fail-safe → []
# 持仓标的保留在 active：研究新选 ∪ 仍持仓
retained = [s for s in held if s not in new_symbols]
active_symbols = new_symbols + retained
# removed 只含"既非新选、又无持仓"的
removed = [s for s in old_symbols if s not in new_symbols and s not in held]
# 仅当开关显式开启(旧行为)时，持仓标的才回到 removed 被强平
if self._close_held:
    removed = [s for s in old_symbols if s not in new_symbols]
    active_symbols = new_symbols
```

- `_active_symbols`、`_symbol_meta`、`symbol_update` 均以 `active_symbols`（含 retained）为准。
- `removed` 里现在只剩无持仓标的 → 照发 `trade_decision close`（原行为不变）。
- retained 标的打日志：`[路由] {symbol} 持仓中，保留监控，出场交 PositionAnalyst`。

### 4.2 `_get_position_symbols()`（新增，复用 MultiDataCollector 同款）

```python
def _get_position_symbols(self) -> list:
    import json, os
    from utils.state_paths import get_state_paths
    from utils.symbols import to_internal      # 与 collector 同款规范化
    pf = get_state_paths().positions
    if not os.path.exists(pf):
        return []
    try:
        with open(pf) as f:
            positions = json.load(f)
        return [to_internal(s) for s in positions.keys()]
    except Exception as e:
        self.logger.warning(f"[路由] 读取持仓失败，退化为旧轮换行为: {e}")
        return []
```

fail-safe：任何读失败 → `[]` → held 为空 → 持仓标的进 removed → **平仓（旧行为，仓平成 flat 安全）**。永不产生无人看管持仓。

### 4.3 Config 四段式（`utils/config_loader.py`，risk: 节点）

| 段 | 内容 |
|---|---|
| `RISK_DEFAULTS` | `rotation_close_held_enabled: False` |
| `HARD_LIMITS` | bool 类型校验 |
| env_map | `ROTATION_CLOSE_HELD_ENABLED` → bool |
| `_load_yaml` | 映射 `risk.rotation_close_held_enabled` |
| `format_banner` | 新增「轮换强平持仓: 关闭 / 开启」 |

SymbolRouter `__init__` 读 `self._close_held = (config or {}).get('rotation_close_held_enabled', False)`。

合并优先级：defaults < config.yaml < 环境变量（与 [[comet-workflow-for-changes]] 一致）。改动需重启 `run_agents.py` 生效。

## 5. 边界与 fail-safe 语义

| 场景 | 行为 |
|---|---|
| removed 有持仓 + 开关 false（默认） | 保留在 active，不发 close，持续监控 |
| removed 无持仓 | 进 removed，照发 close（原行为） |
| 有持仓 + 开关 true | 回退旧强平（持仓也平） |
| positions 文件缺失/损坏 | `_get_position_symbols()` → `[]` → 持仓标的被平（旧行为，flat 安全），不抛 |
| active 集超 max_active | 允许临时超出（≤ +max_concurrent_positions=3），持仓监控优先 |

## 6. 测试策略（`test_rotation_respect_position_hold.py`）

1. removed 含持仓标的 + 默认开关 → active_symbols 含该标的、**不发** close、symbol_update 正常。
2. removed 含无持仓标的 → 发 close（原行为）。
3. 开关 true → 持仓标的回到 removed 被强平（旧行为）。
4. `_get_position_symbols()` fail-safe：文件缺失 / JSON 损坏 → 返回 `[]`，不抛；此时持仓标的走旧平仓路径。
5. active 集 retained 合并：持仓标的不在新选时仍出现在 active_symbols。
6. config 四段式：默认 False / bool 越界校验 / env 覆盖 / banner 文案。
7. main() 登记新用例（沿用项目 test 自注册惯例）。

build/verify：`python3 -m pytest test_rotation_respect_position_hold.py -q` 全绿 + 全量回归无退化。

## 7. Spec Patch

proposal 已声明新 capability `symbol-rotation-position-guard`。specs 阶段将把第 5、6 节的验收场景写成正式 spec（含 B-revised 的"保留在 active"语义与 fail-safe 退化为强平的契约）。本 Design Doc 不重复定义需求。
