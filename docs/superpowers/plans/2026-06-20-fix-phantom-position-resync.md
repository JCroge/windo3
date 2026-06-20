---
change: fix-phantom-position-resync
design-doc: docs/superpowers/specs/2026-06-20-fix-phantom-position-resync-design.md
base-ref: 5715ae0c0f7efc338f337b27f6d2e4be6ea2aebb
archived-with: 2026-06-20-fix-phantom-position-resync
---

# 幽灵持仓补录双确认 + 症状硬化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `sync_positions` 平仓后不再从交易所滞后快照补录幽灵持仓——补录前要求连续 N（默认 2）个 sync tick 都见到该持仓；并对 protection-unknown 告警去重退避、幽灵移除后自愈 `migrate_missing_sl` halt。

**Architecture:** 改动集中在 `executor.py::sync_positions`：补录 else 分支前置 `_pending_resync` 双确认计数 + 扫尾清幽灵；`[Migrate]` protection-unknown 分支加 `_last_protection_alert` 去重 + halt 幂等；移除分支加 `migrate_missing_sl` halt 自愈。新增 config `position_resync_confirm_ticks`。`_calc_risk_budget`（20x 按设计）不动。

**Tech Stack:** Python 3.9, pytest；测试 harness 复用 `test_position_sync_retry.py`（`ContractExecutor.__new__` + mock `_fetch_positions_with_retry`）。

archived-with: 2026-06-20-fix-phantom-position-resync
---

## Task 1: config `position_resync_confirm_ticks`

**Files:**
- Modify: `utils/config_loader.py`
- Test: `tests/test_phantom_position_resync.py`

- [ ] **Step 1: Write the failing test**

创建 `tests/test_phantom_position_resync.py`：

```python
"""fix-phantom-position-resync: 幽灵持仓补录双确认 + 症状硬化单测。"""
from unittest.mock import MagicMock

from executor import ContractExecutor


def test_config_resync_confirm_ticks_default():
    from utils.config_loader import DEFAULTS
    assert DEFAULTS.get("position_resync_confirm_ticks") == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_phantom_position_resync.py::test_config_resync_confirm_ticks_default -v`
Expected: FAIL（key 不存在）

- [ ] **Step 3: Write minimal implementation**

在 `utils/config_loader.py` 的 `DEFAULTS` dict 加一行（与其它 position/sync 项相邻）：

```python
    "position_resync_confirm_ticks": 2,
```

并在 `HARD_LIMITS`（若该 dict 存在且同步维护）加约束 `"position_resync_confirm_ticks": (1, 10),`；env 映射表加 `"POSITION_RESYNC_CONFIRM_TICKS": ("position_resync_confirm_ticks", int),`（仿邻近 int 项）。

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_phantom_position_resync.py::test_config_resync_confirm_ticks_default -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add utils/config_loader.py tests/test_phantom_position_resync.py
git commit -m "feat(phantom-resync): config position_resync_confirm_ticks 默认 2"
```

archived-with: 2026-06-20-fix-phantom-position-resync
---

## Task 2: 双确认状态机（核心）

**Files:**
- Modify: `executor.py` (`sync_positions` 补录 else 分支 ~2729 + 扫尾)
- Test: `tests/test_phantom_position_resync.py`

**实施者须先做**：Read `executor.py:2667-2760` 全 `sync_positions`，确认补录 else 分支行号、`_save_positions()` 调用点、以及补录后 sync 尾部的 `[Migrate]`/算法扫描调用名（测试需 stub 这些重下游，隔离双确认逻辑）。

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_phantom_position_resync.py`：

```python
def _mk_executor():
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.exchange = MagicMock()
    ex.logger = MagicMock()
    ex.positions = {}
    ex._close_cooldown = {}
    ex._pending_resync = {}
    ex._last_protection_alert = {}
    ex._halted_symbols = {}
    ex._removed_positions_data = []
    ex._last_removed_symbols = []
    ex._sl_check_failures = {}
    ex._config = {"position_resync_confirm_ticks": 2}
    # stub 重下游, 隔离双确认逻辑（方法名以实施者 Read 到的为准）
    ex._save_positions = MagicMock()
    ex._migrate_and_reconcile_protection = MagicMock()  # [Migrate]/算法扫描入口, 名以实读为准
    ex.clear_symbol_halt = MagicMock(return_value=0)
    return ex


def _ex_pos(sym="XRP-USDT-SWAP", side="short"):
    # _fetch_positions_with_retry 返回的原始 ccxt 持仓格式
    return {"symbol": sym, "contracts": 3.7, "side": side, "leverage": 20,
            "notional": 74.0, "entryPrice": 1.13, "unrealizedPnl": 0.0}


def test_phantom_not_imported(monkeypatch):
    # 幽灵: tick1 交易所见到 XRP, tick2 消失 → 永不补录
    ex = _mk_executor()
    ex._fetch_positions_with_retry = MagicMock(side_effect=[[_ex_pos()], []])
    ex.sync_positions()                       # tick1
    assert "XRP-USDT-SWAP" not in ex.positions    # 未补录
    assert ex._pending_resync.get("XRP-USDT-SWAP") == 1
    ex.sync_positions()                       # tick2 幽灵消失
    assert "XRP-USDT-SWAP" not in ex.positions
    assert "XRP-USDT-SWAP" not in ex._pending_resync   # 计数清除


def test_real_position_imported_after_2_ticks():
    # 真仓: 连续 2 tick 都见 → 第 2 tick 补录
    ex = _mk_executor()
    ex._fetch_positions_with_retry = MagicMock(side_effect=[[_ex_pos()], [_ex_pos()]])
    ex.sync_positions()                       # tick1: pending
    assert "XRP-USDT-SWAP" not in ex.positions
    ex.sync_positions()                       # tick2: 补录
    assert "XRP-USDT-SWAP" in ex.positions
    assert "XRP-USDT-SWAP" not in ex._pending_resync


def test_cooldown_skips_resync():
    # 冷却期内交易所仍上报 → 跳过, 不计双确认 tick
    import time as _t
    ex = _mk_executor()
    ex._close_cooldown = {"XRP-USDT-SWAP": _t.time() + 60}
    ex._fetch_positions_with_retry = MagicMock(side_effect=[[_ex_pos()]])
    ex.sync_positions()
    assert "XRP-USDT-SWAP" not in ex.positions
    assert "XRP-USDT-SWAP" not in ex._pending_resync   # 冷却跳过, 不计 tick
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_phantom_position_resync.py -k "phantom or 2_ticks or cooldown" -v`
Expected: FAIL（现 sync_positions 立即补录、无 `_pending_resync` 逻辑）

- [ ] **Step 3: Write minimal implementation**

在 `executor.py::sync_positions` 构造期/方法顶部确保 `self._pending_resync` 存在（`if not hasattr(self,'_pending_resync'): self._pending_resync={}`）。把补录 else 分支（`else:` at ~2729）改为前置双确认（保留原补录体不动）：

```python
            confirm_ticks = (getattr(self, '_config', {}) or {}).get('position_resync_confirm_ticks', 2)
            for sym, ex_pos in active.items():
                if sym in cooldown and now < cooldown[sym]:
                    continue                                  # 第一道防线: 冷却期不补录、不计 tick
                if sym in self.positions:
                    ... 既有数量校正 / unrealized 更新 ...        # 不变
                else:
                    cnt = self._pending_resync.get(sym, 0) + 1
                    if cnt < confirm_ticks:
                        self._pending_resync[sym] = cnt
                        continue                              # 等下个 tick 确认
                    self._pending_resync.pop(sym, None)
                    ... 既有补录逻辑(SL/TP 兜底 + setdefault + self.positions[sym]=ex_pos + newly_synced.append) ...
```

补录循环之后、`_save_positions()` 之前加扫尾清幽灵：

```python
            for sym in list(self._pending_resync):
                if sym not in active:
                    self._pending_resync.pop(sym, None)
```

注：`now` 变量补录循环已有（`now = time.time()` at ~2717）；`confirm_ticks` 从 `self._config` 读（构造期 config dict；若该实例用别的字段名存 config，以实读为准，fallback 默认 2）。

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_phantom_position_resync.py -k "phantom or 2_ticks or cooldown" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add executor.py tests/test_phantom_position_resync.py
git commit -m "feat(phantom-resync): sync_positions 补录双确认(persist-2-ticks)+扫尾清幽灵"
```

archived-with: 2026-06-20-fix-phantom-position-resync
---

## Task 3: protection-unknown 告警去重 + halt 幂等

**Files:**
- Modify: `executor.py` (`[Migrate]` protection-unknown 分支 ~661-669)
- Test: `tests/test_phantom_position_resync.py`

**实施者须先做**：Read `executor.py:655-705` 确认 `migrate_missing_sl` ERROR + `_halt_symbol` 精确分支。

- [ ] **Step 1: Write the failing test**

追加（直接驱动该分支较重，改测公共行为：连续两次同因不应重复 ERROR）：

```python
def test_protection_unknown_error_deduped():
    # 同 symbol+reason 连续两个 tick protection-unknown → ERROR 仅首次
    ex = _mk_executor()
    ex._last_protection_alert = {}
    # 模拟 migrate 分支的去重 helper（实施者把分支抽成可测 helper _alert_protection_unknown）
    first = ex._alert_protection_unknown("XRP-USDT-SWAP")
    second = ex._alert_protection_unknown("XRP-USDT-SWAP")
    assert first is True and second is False        # 首次告警, 第二次去重静默
    assert ex.logger.error.call_count == 1


def test_protection_alert_resets_on_clear():
    ex = _mk_executor()
    ex._alert_protection_unknown("XRP-USDT-SWAP")
    ex._last_protection_alert.pop("XRP-USDT-SWAP", None)   # 状态恢复
    again = ex._alert_protection_unknown("XRP-USDT-SWAP")
    assert again is True                            # 恢复后能重新告警
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_phantom_position_resync.py -k protection -v`
Expected: FAIL（`_alert_protection_unknown` 不存在）

- [ ] **Step 3: Write minimal implementation**

在 `executor.py` 抽出去重 helper：

```python
    def _alert_protection_unknown(self, symbol: str) -> bool:
        """protection-unknown 告警去重: 仅状态变化时记 ERROR + halt。返回是否首次告警。"""
        if not hasattr(self, '_last_protection_alert'):
            self._last_protection_alert = {}
        if self._last_protection_alert.get(symbol) == 'migrate_missing_sl':
            return False                            # 同因已告警, 去重静默
        self.logger.error(
            f"[Migrate] {symbol} 本地有仓位但交易所无 SL algo,protection_state→unknown")
        self._last_protection_alert[symbol] = 'migrate_missing_sl'
        if not self.is_symbol_halted(symbol):
            self._halt_symbol(symbol, reason='migrate_missing_sl')
        return True
```

把原 `executor.py:661-669` 的 `self.logger.error(...)` + `position['protection_state']='unknown'` + `_halt_symbol(reason='migrate_missing_sl')` 改为：

```python
            position['protection_state'] = 'unknown'
            self._alert_protection_unknown(symbol)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_phantom_position_resync.py -k protection -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add executor.py tests/test_phantom_position_resync.py
git commit -m "feat(phantom-resync): protection-unknown 告警去重 + halt 幂等"
```

archived-with: 2026-06-20-fix-phantom-position-resync
---

## Task 4: 幽灵移除后 halt 自愈

**Files:**
- Modify: `executor.py` (移除分支 ~2704-2710)
- Test: `tests/test_phantom_position_resync.py`

- [ ] **Step 1: Write the failing test**

追加：

```python
def test_migrate_halt_self_heals_on_removal():
    # migrate_missing_sl halt → sync 移除该 symbol → halt 自动清
    ex = _mk_executor()
    ex.positions = {"XRP-USDT-SWAP": {"symbol": "XRP-USDT-SWAP", "amount": 3.7}}
    ex._halted_symbols = {"XRP-USDT-SWAP": {"reason": "migrate_missing_sl", "halted_at": 1.0}}
    cleared = []
    ex.clear_symbol_halt = MagicMock(side_effect=lambda s, **k: cleared.append(s) or 1)
    ex._fetch_positions_with_retry = MagicMock(side_effect=[[]])   # 交易所已无 XRP
    ex.sync_positions()
    assert "XRP-USDT-SWAP" not in ex.positions       # 移除
    assert cleared == ["XRP-USDT-SWAP"]              # halt 自愈


def test_non_migrate_halt_not_cleared_on_removal():
    # 其它 reason 的 halt 不被移除误清
    ex = _mk_executor()
    ex.positions = {"XRP-USDT-SWAP": {"symbol": "XRP-USDT-SWAP", "amount": 3.7}}
    ex._halted_symbols = {"XRP-USDT-SWAP": {"reason": "reconcile_conflict", "halted_at": 1.0}}
    ex.clear_symbol_halt = MagicMock(return_value=0)
    ex._fetch_positions_with_retry = MagicMock(side_effect=[[]])
    ex.sync_positions()
    ex.clear_symbol_halt.assert_not_called()         # 非 migrate_missing_sl 不清
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_phantom_position_resync.py -k "self_heal or not_cleared" -v`
Expected: FAIL（移除分支无自愈逻辑）

- [ ] **Step 3: Write minimal implementation**

在移除分支（`del self.positions[sym]` 之后，`executor.py:2709` 附近）加：

```python
                    halt_info = getattr(self, '_halted_symbols', {}).get(sym)
                    if halt_info and halt_info.get('reason') == 'migrate_missing_sl':
                        self.clear_symbol_halt(sym, source='self_heal:phantom_removed')
                        self.logger.info(f"[SelfHeal] {sym} 幽灵移除, 自动清 migrate_missing_sl halt")
                    if hasattr(self, '_last_protection_alert'):
                        self._last_protection_alert.pop(sym, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_phantom_position_resync.py -k "self_heal or not_cleared" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add executor.py tests/test_phantom_position_resync.py
git commit -m "feat(phantom-resync): 幽灵移除后 migrate_missing_sl halt 自愈"
```

archived-with: 2026-06-20-fix-phantom-position-resync
---

## Task 5: 全量回归 + 不回归既有

**Files:** 无（验证 only）

- [ ] **Step 1: 本 change 测试 + 既有 sync 测试**

Run: `python3 -m pytest tests/test_phantom_position_resync.py test_position_sync_retry.py test_reconciliation.py test_halt_resume_ownership.py -v`
Expected: PASS（新用例全绿 + 既有 transient-error 重试 / 对账 / halt 测试零回归）

- [ ] **Step 2: 编译 + 全量回归**

Run: `python3 -m compileall -q executor.py && python3 -m pytest -q`
Expected: PASS 数 ≥ 1331 基线 + 新用例；8 failed 仅既有 round2 asyncio flaky（`test_round2_probe_long_dispatcher` / `test_round2_request_id_position`），零新退化

- [ ] **Step 3: main() 登记（若 test 文件有 main 注册惯例则补；无则跳过）+ Commit**

```bash
git add -A
git commit -m "test(phantom-resync): 全量回归零退化 + 既有 sync/halt 测试不回归" || echo "nothing to commit"
```

archived-with: 2026-06-20-fix-phantom-position-resync
---

## Task 6: 更新 CLAUDE.md 风控红线

**Files:**
- Modify: `CLAUDE.md`（position-sync / 对账相关红线段）

- [ ] **Step 1: 追加红线条目**

在 `CLAUDE.md` 风控红线区合适位置（对账 / sync 相关条目附近）追加：

```
- 仓位同步补录双确认（2026-06-20 `fix-phantom-position-resync`）：`sync_positions` 对本地缺失、交易所新出现的持仓 MUST 连续 `position_resync_confirm_ticks`（默认 2）个 sync tick 确认后才补录，防交易所平仓后上报延迟产生幽灵持仓（实证 OKX 滞后 76s 击穿原 60s `_close_cooldown`，近 3 天复发 3 次 UNI/XLM/XRP）。`_close_cooldown` 作第一道防线保留。protection-unknown(`migrate_missing_sl`) 告警经 `_alert_protection_unknown` 去重(同 symbol+reason 仅状态变化时记 ERROR+halt)；幽灵被 sync 移除时自动清 `migrate_missing_sl` halt（仅此 reason 自愈，其它 fail-closed halt 不动）。安全不放松：真·无保护仓位(2 tick 确认补录后 reconcile 仍无 SL)照旧 halt。不改 `_calc_risk_budget`（20x 是恒定风险公式按设计、max_loss bounded 5%）。
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(phantom-resync): CLAUDE.md 红线加仓位同步补录双确认"
```

archived-with: 2026-06-20-fix-phantom-position-resync
---

## Self-Review 结论

- **Spec coverage**：delta spec 三 requirement 全覆盖——双确认(Task 2)、告警去重退避(Task 3)、halt 自愈(Task 4)；config(Task 1)。
- **Placeholder scan**：无 TBD；代码 step 含完整代码。两处"以实读为准"是给实施者的精确定位指令（重下游 stub 名 + 分支行号），非占位。
- **Type consistency**：`_pending_resync: dict[str,int]` / `_last_protection_alert: dict[str,str]` / `_alert_protection_unknown(symbol)->bool` / `clear_symbol_halt(sym, source=)` / config `position_resync_confirm_ticks` 跨任务一致。
