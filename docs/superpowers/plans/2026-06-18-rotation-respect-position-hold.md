---
change: rotation-respect-position-hold
design-doc: docs/superpowers/specs/2026-06-18-rotation-respect-position-hold-design.md
base-ref: 1bbbc2471ee1d1a3d61d7dfb0b04c125036a3a9c
---

# 轮换尊重持仓研判（B-revised）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 标的轮换时，仍有持仓的标的保留在活跃集中而非被强平，出场决策交回 PositionAnalyst。

**Architecture:** B-revised——SymbolRouter 在 `_handle_research_result` 中把持仓标的并入 `active_symbols`、从 `removed` 剔除，使整条监控链（采集/技术/Judge/PositionAnalyst）保持与持仓前一致的监控状态；受 config 开关 `rotation_close_held_enabled`（默认 False）控制，读持仓 fail-safe 退化为旧强平。

**Tech Stack:** Python 3.9, asyncio, pytest；config 经 `utils/config_loader.py` 四段式接入。

---

## File Structure

- `utils/config_loader.py`（Modify）：新增 bool 开关 `rotation_close_held_enabled`，按 `ev_winrate_gate_enabled` 范式接入 DEFAULTS + `_load_yaml` + env_map + `format_banner`。**不进 HARD_LIMITS**（HARD_LIMITS 仅数值区间，bool 标志靠 `_to_bool` 转换即校验，与项目现有 bool 开关一致）。
- `agents/research/symbol_router.py`（Modify）：新增 `_get_position_symbols()`（fail-safe），`__init__` 读 `_close_held`，`_handle_research_result` 改为 B-revised 门控。
- `test_rotation_respect_position_hold.py`（Create）：pytest 可收集的 `test_*` 函数 + `main()` 自注册（沿用 `test_ev_gate.py` 范式）。

---

## Task 1: Config 四段式接入 `rotation_close_held_enabled`

**Files:**
- Modify: `utils/config_loader.py`（DEFAULTS ~line 113、`_load_yaml` ~line 242、env_map ~line 270、`format_banner` ~line 471）
- Test: `test_rotation_respect_position_hold.py`

- [ ] **Step 1: 写失败测试（config 部分）**

新建 `test_rotation_respect_position_hold.py`，先写 config 三个用例：

```python
"""轮换尊重持仓研判（B-revised）单元测试

测试要点：
1. config 四段式接入 rotation_close_held_enabled（默认 False / env 覆盖 / banner 展示）
2. SymbolRouter B-revised 门控（持仓保留不平 / 无持仓仍平 / 开关回退 / fail-safe）
"""
import sys
import os
import json
import asyncio
import tempfile
sys.path.insert(0, '.')


# ───────────────── Config 四段式 ─────────────────

def test_config_default_is_false():
    """默认 rotation_close_held_enabled=False（保护生效）"""
    from utils.config_loader import load_config
    with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as f:
        f.write("risk: {}\n")
        path = f.name
    try:
        cfg = load_config(yaml_path=path)
        assert cfg.get('rotation_close_held_enabled') is False, \
            f"默认应为 False，实际 {cfg.get('rotation_close_held_enabled')}"
    finally:
        os.unlink(path)
    print("  ✅ Case: config 默认 False")


def test_config_env_override_true():
    """env ROTATION_CLOSE_HELD_ENABLED=true 覆盖为 True"""
    from utils.config_loader import load_config
    with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as f:
        f.write("risk: {}\n")
        path = f.name
    os.environ['ROTATION_CLOSE_HELD_ENABLED'] = 'true'
    try:
        cfg = load_config(yaml_path=path)
        assert cfg.get('rotation_close_held_enabled') is True, \
            f"env 覆盖应为 True，实际 {cfg.get('rotation_close_held_enabled')}"
    finally:
        del os.environ['ROTATION_CLOSE_HELD_ENABLED']
        os.unlink(path)
    print("  ✅ Case: config env 覆盖 True")


def test_config_yaml_override_true():
    """config.yaml risk.rotation_close_held_enabled=true 生效"""
    from utils.config_loader import load_config
    with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as f:
        f.write("risk:\n  rotation_close_held_enabled: true\n")
        path = f.name
    try:
        cfg = load_config(yaml_path=path)
        assert cfg.get('rotation_close_held_enabled') is True, \
            f"yaml 覆盖应为 True，实际 {cfg.get('rotation_close_held_enabled')}"
    finally:
        os.unlink(path)
    print("  ✅ Case: config yaml 覆盖 True")


def test_banner_shows_rotation_flag():
    """启动 banner 含「轮换强平持仓」行"""
    from utils.config_loader import load_config, format_banner
    with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as f:
        f.write("risk: {}\n")
        path = f.name
    try:
        cfg = load_config(yaml_path=path)
        banner = format_banner(cfg)
        assert '轮换强平持仓' in banner, "banner 应含「轮换强平持仓」行"
        assert '关闭' in banner.split('轮换强平持仓')[1][:10], "默认应显示『关闭』"
    finally:
        os.unlink(path)
    print("  ✅ Case: banner 展示开关状态")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 -m pytest test_rotation_respect_position_hold.py -k config -q`
Expected: FAIL（`rotation_close_held_enabled` 未接入，返回 None）

- [ ] **Step 3: DEFAULTS 加默认值**

在 `utils/config_loader.py` line 114 后（`"ev_neutral_p_win": 0.55,` 同一 DEFAULTS dict 内）加：

```python
    # 标的轮换是否强平已持仓标的：默认 False=保留持仓交 PositionAnalyst（B-revised 保护）
    "rotation_close_held_enabled": False,
```

- [ ] **Step 4: `_load_yaml` 映射 risk 节点**

在 `utils/config_loader.py` line 244（`out['ev_neutral_p_win'] = ...` 之后、`return out` 之前）加：

```python
    if 'rotation_close_held_enabled' in risk:
        out['rotation_close_held_enabled'] = _to_bool(risk['rotation_close_held_enabled'])
```

- [ ] **Step 5: env_map 接入**

在 `utils/config_loader.py` env_map 中（line 270 `"EV_WINRATE_GATE_ENABLED": ("ev_winrate_gate_enabled", _to_bool),` 之后）加：

```python
        "ROTATION_CLOSE_HELD_ENABLED": ("rotation_close_held_enabled", _to_bool),
```

- [ ] **Step 6: format_banner 展示**

在 `utils/config_loader.py` line 471（EV 胜率门那行）之后加：

```python
        f"  轮换强平持仓:          {'开启' if cfg.get('rotation_close_held_enabled', False) else '关闭'}",
```

- [ ] **Step 7: 运行测试验证通过**

Run: `python3 -m pytest test_rotation_respect_position_hold.py -k config -q`
Expected: PASS（4 passed）

- [ ] **Step 8: 提交**

```bash
git add utils/config_loader.py test_rotation_respect_position_hold.py
git commit -m "feat(rotation-hold): config 接入 rotation_close_held_enabled（默认 False，四段式+banner）"
```

---

## Task 2: SymbolRouter `_get_position_symbols()` fail-safe

**Files:**
- Modify: `agents/research/symbol_router.py`
- Test: `test_rotation_respect_position_hold.py`

- [ ] **Step 1: 写失败测试（fail-safe 部分）**

追加到 `test_rotation_respect_position_hold.py`：

```python
# ───────────────── _get_position_symbols fail-safe ─────────────────

def _new_router(close_held=False):
    """裸构造 SymbolRouter（不需 exchange），绕过轮换冷却"""
    from agents.research.symbol_router import SymbolRouter
    r = SymbolRouter(config={'rotation_close_held_enabled': close_held})
    r._min_rotation_interval = 0          # 绕过 3600s 冷却
    r._active_symbols = ['XLM-USDT', 'SUI-USDT']
    return r


def test_get_position_symbols_missing_file(monkeypatch):
    """positions 文件不存在 → 返回 []"""
    import utils.state_paths as sp
    r = _new_router()
    missing = tempfile.mktemp(suffix='.json')   # 不创建
    monkeypatch.setattr(
        sp, 'get_state_paths',
        lambda: type('P', (), {'positions': missing})()
    )
    assert r._get_position_symbols() == [], "缺失文件应返回 []"
    print("  ✅ Case: positions 文件缺失 fail-safe []")


def test_get_position_symbols_corrupt_file(monkeypatch):
    """positions 文件损坏 → 返回 [] 不抛"""
    import utils.state_paths as sp
    r = _new_router()
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
        f.write("{not valid json")
        path = f.name
    monkeypatch.setattr(
        sp, 'get_state_paths',
        lambda: type('P', (), {'positions': path})()
    )
    try:
        assert r._get_position_symbols() == [], "损坏文件应返回 []"
    finally:
        os.unlink(path)
    print("  ✅ Case: positions 文件损坏 fail-safe []")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 -m pytest test_rotation_respect_position_hold.py -k position_symbols -q`
Expected: FAIL（`_get_position_symbols` 不存在 → AttributeError）

- [ ] **Step 3: 实现 `_get_position_symbols()`**

在 `agents/research/symbol_router.py` 顶部 import 处补 import（line 3 `import time` 之后）：

```python
import json
import os
```

在类内 `active_symbols` property 之前加方法：

```python
    def _get_position_symbols(self) -> list:
        """读取持仓标的列表（统一为内部规范 BASE-USDT）。

        fail-safe：文件缺失/损坏 → 返回 []，不抛异常。
        持仓信息不可得时，轮换退化为旧强平行为，绝不产生无人看管持仓。
        """
        from utils.state_paths import get_state_paths
        from utils.symbol import to_internal
        positions_file = get_state_paths().positions
        if not os.path.exists(positions_file):
            return []
        try:
            with open(positions_file, 'r') as f:
                positions = json.load(f)
            return [to_internal(s) for s in positions.keys()]
        except Exception as e:
            self.logger.warning(f"[路由] 读取持仓失败，退化为旧轮换行为: {e}")
            return []
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python3 -m pytest test_rotation_respect_position_hold.py -k position_symbols -q`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add agents/research/symbol_router.py test_rotation_respect_position_hold.py
git commit -m "feat(rotation-hold): SymbolRouter 新增 _get_position_symbols() fail-safe"
```

---

## Task 3: B-revised 门控逻辑

**Files:**
- Modify: `agents/research/symbol_router.py`（`__init__`、`_handle_research_result`）
- Test: `test_rotation_respect_position_hold.py`

- [ ] **Step 1: 写失败测试（门控部分）**

追加到 `test_rotation_respect_position_hold.py`：

```python
# ───────────────── B-revised 门控 ─────────────────

def _run_rotation(router, selected_symbols, held_symbols, monkeypatch):
    """驱动一次轮换，返回捕获的 publish 列表 [(msg_type, payload), ...]"""
    captured = []

    async def _fake_publish(msg_type, payload, to="broadcast", symbol=None):
        captured.append((msg_type, payload))

    monkeypatch.setattr(router, 'publish', _fake_publish)
    monkeypatch.setattr(router, '_get_position_symbols', lambda: list(held_symbols))
    payload = {'selected': [{'symbol': s} for s in selected_symbols]}
    asyncio.run(router._handle_research_result(payload))
    return captured


def test_held_symbol_retained_not_closed(monkeypatch):
    """持仓标的被轮出研判选集 → 保留在 active、不发 close"""
    r = _new_router(close_held=False)
    # 旧 active=[XLM,SUI]，研判新选=[SUI,ADA]，XLM 仍持仓
    cap = _run_rotation(r, ['SUI-USDT', 'ADA-USDT'], ['XLM-USDT'], monkeypatch)

    closes = [p['symbol'] for t, p in cap if t == 'trade_decision' and p.get('action') == 'close']
    assert 'XLM-USDT' not in closes, f"持仓标的 XLM 不应被平，实际 closes={closes}"

    updates = [p for t, p in cap if t == 'symbol_update']
    assert updates, "应发 symbol_update"
    active = updates[-1]['active_symbols']
    assert 'XLM-USDT' in active, f"持仓标的 XLM 应保留在 active，实际 {active}"
    assert 'XLM-USDT' not in updates[-1].get('removed', []), "XLM 不应在 removed"
    print("  ✅ Case: 持仓标的保留不平")


def test_unheld_symbol_still_closed(monkeypatch):
    """无持仓标的被轮出 → 照发 close（原行为）"""
    r = _new_router(close_held=False)
    # 旧 active=[XLM,SUI]，新选=[ADA]，无持仓
    cap = _run_rotation(r, ['ADA-USDT'], [], monkeypatch)

    closes = [p['symbol'] for t, p in cap if t == 'trade_decision' and p.get('action') == 'close']
    assert 'XLM-USDT' in closes and 'SUI-USDT' in closes, \
        f"无持仓标的应被平，实际 closes={closes}"
    print("  ✅ Case: 无持仓标的仍平")


def test_close_held_true_reverts_old_behavior(monkeypatch):
    """开关 true → 持仓标的也被强平（回退旧行为）"""
    r = _new_router(close_held=True)
    cap = _run_rotation(r, ['SUI-USDT', 'ADA-USDT'], ['XLM-USDT'], monkeypatch)

    closes = [p['symbol'] for t, p in cap if t == 'trade_decision' and p.get('action') == 'close']
    assert 'XLM-USDT' in closes, f"开关 true 时持仓标的应被平，实际 closes={closes}"
    updates = [p for t, p in cap if t == 'symbol_update']
    assert 'XLM-USDT' not in updates[-1]['active_symbols'], "开关 true 时 XLM 不应保留 active"
    print("  ✅ Case: 开关 true 回退旧强平")


def test_retained_merged_into_active(monkeypatch):
    """多个持仓标的均保留进 active（即便超出研判新选）"""
    r = _new_router(close_held=False)
    r._active_symbols = ['XLM-USDT', 'SUI-USDT', 'DOGE-USDT']
    cap = _run_rotation(r, ['ADA-USDT'], ['XLM-USDT', 'DOGE-USDT'], monkeypatch)

    active = [p for t, p in cap if t == 'symbol_update'][-1]['active_symbols']
    assert 'ADA-USDT' in active, "新选应在 active"
    assert 'XLM-USDT' in active and 'DOGE-USDT' in active, \
        f"两个持仓标的均应保留，实际 {active}"
    closes = [p['symbol'] for t, p in cap if t == 'trade_decision' and p.get('action') == 'close']
    assert 'SUI-USDT' in closes, "无持仓的 SUI 应被平"
    assert 'XLM-USDT' not in closes and 'DOGE-USDT' not in closes, "持仓标的不应被平"
    print("  ✅ Case: 多持仓标的合并进 active")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 -m pytest test_rotation_respect_position_hold.py -k "retained or held or closed or reverts" -q`
Expected: FAIL（门控未实现：持仓标的仍被平、active 不含持仓标的）

- [ ] **Step 3: `__init__` 读开关**

在 `agents/research/symbol_router.py` `__init__`（line 17 `self._min_rotation_interval = 3600` 之后）加：

```python
        self._close_held = (config or {}).get('rotation_close_held_enabled', False)
```

- [ ] **Step 4: 改写 `_handle_research_result` 门控段**

将 `agents/research/symbol_router.py` line 51-87 区间（从 `new_symbols = ...` 到平仓循环结束）替换为：

```python
        new_symbols = [s['symbol'] for s in selected[:self._max_active]]
        old_symbols = self._active_symbols.copy()

        held = set(self._get_position_symbols())

        if self._close_held:
            # 旧行为：轮出即强平，不保留持仓标的
            removed = [s for s in old_symbols if s not in new_symbols]
            active_symbols = new_symbols
            retained = []
        else:
            # B-revised：持仓标的保留在 active，不进 removed
            removed = [s for s in old_symbols if s not in new_symbols and s not in held]
            retained = [s for s in held if s not in new_symbols]
            active_symbols = new_symbols + retained

        added = [s for s in new_symbols if s not in old_symbols]

        removed_action = {}
        for s in removed:
            removed_action[s] = "close_at_market"

        self._active_symbols = active_symbols
        self._symbol_meta = {s['symbol']: s for s in selected[:self._max_active]}
        self._last_update_time = now

        await self.publish("symbol_update", {
            "active_symbols": active_symbols,
            "added": added,
            "removed": removed,
            "removed_action": removed_action,
            "symbol_meta": self._symbol_meta,
        })

        self.logger.info(
            f"[路由] 活跃标的更新: {active_symbols} "
            f"(+{added}, -{removed}, 持仓保留={retained})"
        )

        for symbol in retained:
            self.logger.info(
                f"[路由] {symbol} 持仓中，保留监控，出场交 PositionAnalyst"
            )

        for symbol in removed:
            if removed_action.get(symbol) == "close_at_market":
                await self.publish("trade_decision", {
                    "action": "close",
                    "symbol": symbol,
                    "confidence": 100,
                    "size_pct": 1.0,
                    "reasoning": "标的轮换，平仓退出",
                }, symbol=symbol)
                self.logger.info(f"[路由] 发送平仓指令: {symbol}")
```

- [ ] **Step 5: 运行测试验证通过**

Run: `python3 -m pytest test_rotation_respect_position_hold.py -q`
Expected: PASS（全部用例）

- [ ] **Step 6: 加 `main()` 自注册（项目惯例）**

在 `test_rotation_respect_position_hold.py` 末尾加（沿用 `test_ev_gate.py` 范式）：

```python
def main():
    import pytest
    raise SystemExit(pytest.main([__file__, '-q']))


if __name__ == '__main__':
    main()
```

- [ ] **Step 7: 提交**

```bash
git add agents/research/symbol_router.py test_rotation_respect_position_hold.py
git commit -m "feat(rotation-hold): B-revised 门控——持仓标的保留 active 不强平，出场交 PositionAnalyst"
```

---

## Task 4: tasks.md 勾选 + 全量回归

- [ ] **Step 1: 勾选 openspec tasks.md**

将 `openspec/changes/rotation-respect-position-hold/tasks.md` 所有 `- [ ]` 改为 `- [x]`。

- [ ] **Step 2: 跑本 change 测试**

Run: `python3 -m pytest test_rotation_respect_position_hold.py -q`
Expected: PASS（全绿）

- [ ] **Step 3: 全量回归无退化**

Run: `python3 -m pytest -q`
Expected: 基线 1302 + 本次新增用例全绿，无退化。
（若个别历史用例因环境/网络 flaky，对照 base-ref 确认非本次引入。）

- [ ] **Step 4: 提交**

```bash
git add openspec/changes/rotation-respect-position-hold/tasks.md
git commit -m "chore(rotation-hold): tasks 勾选完成 + 全量回归通过"
```

---

## Self-Review

- **Spec coverage**：delta spec 三需求八场景 → Task1（config 开关/banner）、Task2（fail-safe 缺失/损坏）、Task3（持仓保留/无持仓仍平/开关回退/retained 合并）逐一覆盖。✓
- **Placeholder scan**：无 TBD/TODO，每步含完整代码与命令。✓
- **Type consistency**：`_get_position_symbols`、`_close_held`、`active_symbols`、`retained`、`removed` 在各 Task 命名一致；`to_internal` 来自 `utils.symbol`（单数）。✓
- **HARD_LIMITS**：bool 开关不进 HARD_LIMITS，与 `ev_winrate_gate_enabled` 现状一致（避免数值区间校验误用）。✓
