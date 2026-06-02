---
change: tg-graceful-ops
design-doc: docs/superpowers/specs/2026-05-30-tg-graceful-ops-design.md
base-ref: 826e0ed1b0cc7181e615faca0dd652bd2431fa23
archived-with: 2026-06-01-tg-graceful-ops
---

# TG Graceful Ops Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Telegram 真正成为系统优雅运维入口：修复 `/resume` 不清 per-symbol halt 残留 bug；新增 `/halts` `/resume_symbol` `/pnl` `/pnl_id` 命令；`/status` 增强 per-symbol halt 与 agent health 可见性。命令准确执行、不留残留状态、可见可控。

**Architecture:** 4 个 capability 解耦但有依赖：F-TG-001（root API + agent resume 改造）→ F-TG-004（Orchestrator 写 agent_health.json + MultiExecutor publish halts_snapshot）→ F-TG-002（TG /halts /resume_symbol /status 行）→ F-TG-003（TG /pnl /pnl_id）。引入两个 helper 作为单点契约：`ContractExecutor.clear_symbol_halt()` 与 TG `_resolve_pending_for_pnl_correction()`。

**Tech Stack:** Python 3 / pytest / asyncio / 项目内 message bus / utils/state_paths

**实施顺序：** F-TG-001 → F-TG-004 → F-TG-002 → F-TG-003 → 全量回归 → 文档同步

**约定：**
- 测试前缀：项目根目录直接放 `test_*.py`（与现有 `test_telegram.py`、`test_state_namespace.py` 对齐）
- 提交：每个 Task 末尾一次提交，message 带 `[TG-OPS]` 前缀，子项加 `[TG-001/002/003/004]`
- 验证命令：`python3 -m pytest -q <file>` 当前目录运行
- bus DLQ 真实属性：`MessageBus._dead_letter`（deque，不是 _dlq），通过 `len()` 取大小

---

## File Structure

**新增文件：**
- `test_tg_symbol_halt_control.py` — F-TG-001 + F-TG-002 单测
- `test_tg_pnl_correction.py` — F-TG-003 单测
- `test_tg_status_enhancement.py` — F-TG-004 单测
- `docs/audit_remediation_tg_graceful_ops_acceptance.md` — 验收报告

**修改文件：**
- `executor.py` (root) — 新增 `clear_symbol_halt` / `get_halted_symbols`
- `agents/trading/executor.py` — `_handle_resume` 三分支清；`force_resume` 清+audit；`on_message` 增加 `cmd='resume_symbol'`；新增 `_publish_halts_snapshot`
- `agents/orchestrator.py` — 订阅 `halts_snapshot`；新增 `_health_loop` + `_write_agent_health`
- `agents/trading/telegram_notifier.py` — 新增 `/halts` `/resume_symbol` `/pnl` `/pnl_id`；`/status` 增强；订阅新 risk_alert types
- `utils/state_paths.py` — `StatePaths` 加 `agent_health` 字段；`as_banner_lines` 加一行
- `agents/message_bus.py` — 注册 `halts_snapshot` topic（如需显式注册）

**文档同步：**
- `CLAUDE.md` — 新基线 + TG 命令清单
- `docs/to-do-list.md` — 行 58 / 行 64 标已闭环
- `docs/runbook.md`（如存在）— 补 TG 运维 SOP

---

## F-TG-001 Resume 清 per-symbol halt（根因修复）

### Task 1: ContractExecutor 暴露 clear_symbol_halt + get_halted_symbols

**Files:**
- Modify: `executor.py:900-915` (在 `_halt_symbol` / `is_symbol_halted` 附近新增)
- Test: `test_tg_symbol_halt_control.py` (Create)

- [ ] **Step 1: 写失败测试**

新建 `test_tg_symbol_halt_control.py`：

```python
"""F-TG-001 + F-TG-002 测试矩阵：root executor halt API + TG 命令路径。"""

import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


def _make_executor_stub():
    """构造可用于单测 _halted_symbols 的 ContractExecutor 实例(不连交易所)。"""
    from executor import ContractExecutor
    ex = ContractExecutor.__new__(ContractExecutor)
    ex.logger = MagicMock()
    ex._halted_symbols = {}
    return ex


class TestClearSymbolHalt:
    def test_clear_all_returns_count_and_empties(self):
        from executor import ContractExecutor
        ex = _make_executor_stub()
        ex._halted_symbols = {
            "A-USDT-SWAP": {"reason": "x", "halted_at": time.time()},
            "B-USDT-SWAP": {"reason": "y", "halted_at": time.time()},
        }
        n = ex.clear_symbol_halt(None)
        assert n == 2
        assert ex._halted_symbols == {}

    def test_clear_single_symbol(self):
        ex = _make_executor_stub()
        ex._halted_symbols = {
            "A-USDT-SWAP": {"reason": "x"},
            "B-USDT-SWAP": {"reason": "y"},
        }
        n = ex.clear_symbol_halt("A-USDT-SWAP")
        assert n == 1
        assert "A-USDT-SWAP" not in ex._halted_symbols
        assert "B-USDT-SWAP" in ex._halted_symbols

    def test_clear_missing_symbol_returns_zero(self):
        ex = _make_executor_stub()
        ex._halted_symbols = {"A-USDT-SWAP": {"reason": "x"}}
        n = ex.clear_symbol_halt("NOT-EXIST")
        assert n == 0
        assert ex._halted_symbols == {"A-USDT-SWAP": {"reason": "x"}}

    def test_clear_when_attribute_missing(self):
        from executor import ContractExecutor
        ex = ContractExecutor.__new__(ContractExecutor)
        ex.logger = MagicMock()
        # 不初始化 _halted_symbols
        n = ex.clear_symbol_halt(None)
        assert n == 0


class TestGetHaltedSymbols:
    def test_returns_shallow_copy_top_level(self):
        ex = _make_executor_stub()
        ex._halted_symbols = {"X": {"reason": "a"}}
        snapshot = ex.get_halted_symbols()
        assert snapshot == {"X": {"reason": "a"}}
        # 顶层 add 不影响内部
        snapshot["NEW"] = {"reason": "z"}
        assert "NEW" not in ex._halted_symbols

    def test_returns_empty_when_attribute_missing(self):
        from executor import ContractExecutor
        ex = ContractExecutor.__new__(ContractExecutor)
        snapshot = ex.get_halted_symbols()
        assert snapshot == {}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_tg_symbol_halt_control.py::TestClearSymbolHalt test_tg_symbol_halt_control.py::TestGetHaltedSymbols -v`
Expected: 6 个 FAIL（AttributeError: clear_symbol_halt / get_halted_symbols 未定义）

- [ ] **Step 3: 实现两个公开方法**

修改 `executor.py`，在 `is_symbol_halted` 之后插入（约 line 915 后）：

```python
    def clear_symbol_halt(self, symbol: Optional[str] = None) -> int:
        """清除 per-symbol halt 残留。

        Args:
            symbol: 指定 symbol 仅清该项；None 清全部。

        Returns:
            清掉的项数（用于审计日志）。
        """
        halted = getattr(self, '_halted_symbols', None)
        if not halted:
            return 0
        if symbol is None:
            n = len(halted)
            cleared_keys = list(halted.keys())
            halted.clear()
            if n > 0:
                self.logger.info(
                    f"[ClearSymbolHalt] cleared {n} per-symbol halt(s): {cleared_keys}"
                )
            return n
        if symbol in halted:
            reason = halted[symbol].get('reason', '')
            del halted[symbol]
            self.logger.info(
                f"[ClearSymbolHalt] cleared {symbol} (reason={reason})"
            )
            return 1
        return 0

    def get_halted_symbols(self) -> Dict[str, dict]:
        """返回 _halted_symbols 顶层浅拷贝快照。

        调用方 MUST NOT 修改返回 dict 的 value（内部 dict 引用复用）。
        """
        return dict(getattr(self, '_halted_symbols', {}))
```

确认 `executor.py` 顶部已有 `from typing import Optional, Dict`（不在则补充导入）。

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest -q test_tg_symbol_halt_control.py::TestClearSymbolHalt test_tg_symbol_halt_control.py::TestGetHaltedSymbols -v`
Expected: 6 PASS

- [ ] **Step 5: 跑现有保护单 / executor 测试确认无回归**

Run: `python3 -m pytest -q test_protective_sl_owner.py test_protective_cleanup_owner.py test_executor_upgrade.py`
Expected: 全 PASS

- [ ] **Step 6: 提交**

```bash
git add executor.py test_tg_symbol_halt_control.py
git commit -m "[TG-OPS][TG-001] add ContractExecutor.clear_symbol_halt + get_halted_symbols

公开两个方法供 agent 层 _handle_resume / force_resume 路径清理
in-memory _halted_symbols 残留。get_halted_symbols 返回顶层浅拷贝快照。
6 个单测覆盖清单 / 清全 / 不存在 symbol / attribute 缺失等边界。"
```

---

### Task 2: agent _handle_resume 三分支清 + force_resume audit + risk_alert publish

**Files:**
- Modify: `agents/trading/executor.py:80-89` (system_command force_resume 分支)
- Modify: `agents/trading/executor.py:376-407` (`_handle_resume` 三分支)
- Test: `test_tg_symbol_halt_control.py` (扩展)

- [ ] **Step 1: 写失败测试（追加）**

```python
class TestHandleResumeClearsHaltedSymbols:
    @pytest.mark.asyncio
    async def test_payload_matched_clears(self):
        """resume payload 含 reconciliation_result.matched 时清 _halted_symbols。"""
        from agents.trading.executor import MultiExecutor

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex._trading_halted = True
        ex._halt_state = MagicMock()
        ex.executor = MagicMock()
        ex.executor.get_halted_symbols.return_value = {"X-USDT-SWAP": {"reason": "r"}}
        ex.executor.clear_symbol_halt = MagicMock(return_value=1)

        await ex._handle_resume("telegram", {
            "reconciliation_result": {"status": "matched"}
        })

        ex.executor.clear_symbol_halt.assert_called_once_with(None)
        ex._halt_state.confirm_resume.assert_called_once()

    @pytest.mark.asyncio
    async def test_local_reconciler_matched_clears(self):
        """本地 reconciler 通过时清 _halted_symbols。"""
        from agents.trading.executor import MultiExecutor

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex._trading_halted = True
        ex._halt_state = MagicMock()
        ex._reconciler = MagicMock()
        ex._reconciler.reconcile.return_value = {"blocking_issues": []}
        ex.executor = MagicMock()
        ex.executor.positions = {}
        ex.executor.clear_symbol_halt = MagicMock(return_value=0)

        await ex._handle_resume("telegram", {})  # 无 payload, 走本地 reconciler

        ex.executor.clear_symbol_halt.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_local_reconciler_blocking_does_not_clear(self):
        """本地 reconciler 报 blocking 时不清。"""
        from agents.trading.executor import MultiExecutor

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex._trading_halted = True
        ex._halt_state = MagicMock()
        ex._reconciler = MagicMock()
        ex._reconciler.reconcile.return_value = {
            "blocking_issues": [{"type": "x"}]
        }
        ex.executor = MagicMock()
        ex.executor.positions = {}
        ex.executor.clear_symbol_halt = MagicMock(return_value=0)

        await ex._handle_resume("telegram", {})

        ex.executor.clear_symbol_halt.assert_not_called()
        # halt 维持
        assert ex._trading_halted is True

    @pytest.mark.asyncio
    async def test_no_reconciler_clears(self):
        """无 reconciler 直接恢复路径清。"""
        from agents.trading.executor import MultiExecutor

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex._trading_halted = True
        ex._halt_state = MagicMock()
        ex._reconciler = None
        ex.executor = MagicMock()
        ex.executor.clear_symbol_halt = MagicMock(return_value=0)

        await ex._handle_resume("telegram", {})

        ex.executor.clear_symbol_halt.assert_called_once_with(None)


class TestForceResumeClearsWithAudit:
    @pytest.mark.asyncio
    async def test_force_resume_clears_and_publishes_audit_alert(self):
        """force_resume 同时清 _halted_symbols 并 publish risk_alert。"""
        from agents.trading.executor import MultiExecutor

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex._trading_halted = True
        ex._halt_state = MagicMock()
        ex.publish = fake_publish
        ex.executor = MagicMock()
        ex.executor.get_halted_symbols.return_value = {
            "XLM-USDT-SWAP": {"reason": "sl_replace_failed", "halted_at": 0}
        }
        ex.executor.clear_symbol_halt = MagicMock(return_value=1)

        msg = {"type": "system_command", "payload": {
            "command": "force_resume", "source": "telegram"
        }}
        await ex.on_message(msg)

        ex.executor.clear_symbol_halt.assert_called_once_with(None)
        # publish risk_alert
        types = [p.get("type") for t, p in published if t == "risk_alert"]
        assert "force_resume_cleared_symbol_halts" in types
        alert = next(p for t, p in published
                       if t == "risk_alert" and p.get("type") == "force_resume_cleared_symbol_halts")
        assert "XLM-USDT-SWAP" in str(alert.get("cleared_symbols", []))
        assert alert.get("source") == "telegram"

    @pytest.mark.asyncio
    async def test_force_resume_empty_halts_no_audit_alert(self):
        """force_resume 但 _halted_symbols 已空,不发 audit alert。"""
        from agents.trading.executor import MultiExecutor

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex._trading_halted = True
        ex._halt_state = MagicMock()
        ex.publish = fake_publish
        ex.executor = MagicMock()
        ex.executor.get_halted_symbols.return_value = {}
        ex.executor.clear_symbol_halt = MagicMock(return_value=0)

        msg = {"type": "system_command", "payload": {
            "command": "force_resume", "source": "telegram"
        }}
        await ex.on_message(msg)

        types = [p.get("type") for t, p in published if t == "risk_alert"]
        assert "force_resume_cleared_symbol_halts" not in types
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_tg_symbol_halt_control.py::TestHandleResumeClearsHaltedSymbols test_tg_symbol_halt_control.py::TestForceResumeClearsWithAudit -v`
Expected: 6 个 FAIL

- [ ] **Step 3: 修改 _handle_resume 三分支**

`agents/trading/executor.py:376-407` 改为：

```python
    async def _handle_resume(self, source: str, payload: dict):
        """Executor 是 resume 的唯一 owner：执行对账后决定是否恢复交易"""
        reconciliation_result = payload.get('reconciliation_result')

        if reconciliation_result and reconciliation_result.get('status') == 'matched':
            self._halt_state.confirm_resume(resume_by=source, reconcile_ok=True)
            self._trading_halted = False
            self.executor.clear_symbol_halt(None)  # F-TG-001
            self.logger.info(f"[解除熔断] 通过{source}触发，对账通过")
            return

        if self._reconciler:
            try:
                result = self._reconciler.reconcile(
                    executor_positions=self.executor.positions
                )
                blocking = result.get('blocking_issues', [])
                if not blocking:
                    self._halt_state.confirm_resume(resume_by=source, reconcile_ok=True)
                    self._trading_halted = False
                    self.executor.clear_symbol_halt(None)  # F-TG-001
                    self.logger.info(f"[解除熔断] 通过{source}触发，本地对账通过")
                else:
                    self._halt_state.confirm_resume(resume_by=source, reconcile_ok=False)
                    self.logger.warning(
                        f"[熔断维持] 对账失败: {len(blocking)}个阻断问题 — {blocking}"
                    )
            except Exception as e:
                self._halt_state.confirm_resume(resume_by=source, reconcile_ok=False)
                self.logger.error(f"[熔断维持] 对账异常: {e}")
        else:
            self._halt_state.confirm_resume(resume_by=source, reconcile_ok=True)
            self._trading_halted = False
            self.executor.clear_symbol_halt(None)  # F-TG-001
            self.logger.info(f"[解除熔断] 通过{source}触发（无reconciler，直接恢复）")
```

- [ ] **Step 4: 修改 force_resume 分支**

`agents/trading/executor.py:80-89` 内 `elif cmd == 'force_resume':` 块改为：

```python
            elif cmd == 'force_resume':
                # F-TG-001: 先快照,后清除,再 publish audit
                halted_snapshot = self.executor.get_halted_symbols()
                self._halt_state.force_resume(resume_by=source)
                self._trading_halted = False
                cleared_n = self.executor.clear_symbol_halt(None)
                if cleared_n > 0:
                    cleared_symbols = [
                        f"{sym} ({info.get('reason', '?')})"
                        for sym, info in halted_snapshot.items()
                    ]
                    self.logger.warning(
                        f"[强制解除熔断 audit] {source} 同时清除 {cleared_n} 个 "
                        f"per-symbol halt: {cleared_symbols} — 请确认根因已排除"
                    )
                    await self.publish('risk_alert', {
                        'type': 'force_resume_cleared_symbol_halts',
                        'cleared_symbols': cleared_symbols,
                        'source': source,
                    })
                self.logger.warning(f"[强制解除熔断] 通过{source}触发，跳过对账")
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest -q test_tg_symbol_halt_control.py -v`
Expected: 12 PASS（包含 Task 1 的 6）

- [ ] **Step 6: 跑既有 executor agent 测试确认无回归**

Run: `python3 -m pytest -q test_executor_upgrade.py test_full_pipeline.py 2>/dev/null | tail -5`
Expected: 全 PASS

- [ ] **Step 7: 提交**

```bash
git add agents/trading/executor.py test_tg_symbol_halt_control.py
git commit -m "[TG-OPS][TG-001] _handle_resume 三分支 + force_resume 同步清 _halted_symbols

resume 成功后立即清 root executor 的 in-memory _halted_symbols 残留,
解决 5/30 XLM 8 小时静默拒单 bug。force_resume 同时清,但额外
publish risk_alert{type=force_resume_cleared_symbol_halts}
让 Telegram 回显被清的 symbol 与 reason,提示运维确认根因。"
```

---

## F-TG-004 agent_health.json（基础设施 #2）

### Task 3: state_paths 加 agent_health 字段

**Files:**
- Modify: `utils/state_paths.py:56-95` (StatePaths dataclass + for_namespace + as_banner_lines)
- Test: `test_tg_status_enhancement.py` (Create)

- [ ] **Step 1: 写失败测试**

新建 `test_tg_status_enhancement.py`：

```python
"""F-TG-004 测试矩阵：agent_health.json 路径 + Orchestrator 写入 + TG /status 增强。"""

import json
import os
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


class TestStatePathsAgentHealth:
    def test_live_path(self, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "live")
        from utils.state_paths import StatePaths, reset_state_paths
        reset_state_paths()
        paths = StatePaths.for_namespace("live")
        assert paths.agent_health == "data/agent_health.json"

    def test_testnet_path(self, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import StatePaths, reset_state_paths
        reset_state_paths()
        paths = StatePaths.for_namespace("testnet")
        assert paths.agent_health == "data/testnet_agent_health.json"

    def test_paper_path(self, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "paper")
        from utils.state_paths import StatePaths, reset_state_paths
        reset_state_paths()
        paths = StatePaths.for_namespace("paper")
        assert paths.agent_health == "data/paper_agent_health.json"

    def test_banner_includes_agent_health(self, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "live")
        from utils.state_paths import StatePaths, reset_state_paths
        reset_state_paths()
        paths = StatePaths.for_namespace("live")
        lines = paths.as_banner_lines()
        text = "\n".join(lines)
        assert "agent_health" in text
        assert "data/agent_health.json" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_tg_status_enhancement.py::TestStatePathsAgentHealth -v`
Expected: 4 FAIL（agent_health 字段未定义）

- [ ] **Step 3: 修改 utils/state_paths.py**

`StatePaths` dataclass 加字段：

```python
@dataclass(frozen=True)
class StatePaths:
    """状态文件路径集合（不可变）。

    所有路径均为相对项目根的相对路径，仍指向 `data/` 子目录；
    namespace 仅决定 basename 前缀，不改变父目录。
    """
    namespace: str
    positions: str
    risk_state: str
    riskguard_state: str
    halt_state: str
    live_order_events: str
    live_position_lifecycle: str
    agent_health: str  # F-TG-004 新加

    @classmethod
    def for_namespace(cls, namespace: Optional[str] = None) -> 'StatePaths':
        ns = _resolve_namespace(namespace)
        p = _prefix(ns)
        return cls(
            namespace=ns,
            positions=f'data/{p}positions.json',
            risk_state=f'data/{p}risk_state.json',
            riskguard_state=f'data/{p}riskguard_state.json',
            halt_state=f'data/{p}halt_state.json',
            live_order_events=f'data/{p}live_order_events.jsonl',
            live_position_lifecycle=f'data/{p}live_position_lifecycle.json',
            agent_health=f'data/{p}agent_health.json',  # F-TG-004
        )
```

`as_banner_lines` 增加一行：

```python
    def as_banner_lines(self) -> list:
        bot_id = (os.getenv("BOT_INSTANCE_ID") or "").strip()
        lines = [
            f'  状态命名空间:          {self.namespace.upper()}',
            f'    positions          → {self.positions}',
            f'    risk_state         → {self.risk_state}',
            f'    riskguard_state    → {self.riskguard_state}',
            f'    halt_state         → {self.halt_state}',
            f'    live_order_events  → {self.live_order_events}',
            f'    live_position_life → {self.live_position_lifecycle}',
            f'    agent_health       → {self.agent_health}',  # F-TG-004
            f'    BOT_INSTANCE_ID    → {bot_id or "<empty>"}',
        ]
        if self.namespace == "live" and not bot_id:
            lines.append(
                "    WARNING: BOT_INSTANCE_ID not configured; "
                "cross-bot SL ownership cannot be proven by clOrdId."
            )
        return lines
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest -q test_tg_status_enhancement.py::TestStatePathsAgentHealth test_state_namespace.py -v`
Expected: 4 + 16 = 20 PASS

- [ ] **Step 5: 提交**

```bash
git add utils/state_paths.py test_tg_status_enhancement.py
git commit -m "[TG-OPS][TG-004] StatePaths add agent_health field

新增 agent_health: str 路径字段(live=data/agent_health.json,
testnet/paper 加前缀);as_banner_lines 加一行展示。
为 Orchestrator 写 health.json 与 TG /status 读 health.json
提供统一路径派生入口。"
```

---

### Task 4: MultiExecutor publish halts_snapshot

**Files:**
- Modify: `agents/trading/executor.py` (新增 `_publish_halts_snapshot` 方法 + 在 `_run_reconciliation` 末尾调用)
- Test: `test_tg_status_enhancement.py` (扩展)

- [ ] **Step 1: 写失败测试**

```python
class TestMultiExecutorPublishHaltsSnapshot:
    @pytest.mark.asyncio
    async def test_publishes_halts_snapshot_with_payload(self):
        from agents.trading.executor import MultiExecutor

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.publish = fake_publish
        ex.executor = MagicMock()
        ex.executor.get_halted_symbols.return_value = {
            "XLM-USDT-SWAP": {"reason": "sl_replace_failed", "halted_at": 100.0}
        }

        await ex._publish_halts_snapshot()

        topics = [t for t, _ in published]
        assert "halts_snapshot" in topics
        snap = next(p for t, p in published if t == "halts_snapshot")
        assert snap["halted_symbols"] == {
            "XLM-USDT-SWAP": {"reason": "sl_replace_failed", "halted_at": 100.0}
        }
        assert "ts" in snap

    @pytest.mark.asyncio
    async def test_publish_halts_snapshot_handles_exception(self):
        """publish 失败时 logger.warning,不抛。"""
        from agents.trading.executor import MultiExecutor

        async def failing_publish(*args, **kwargs):
            raise RuntimeError("bus down")

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.publish = failing_publish
        ex.executor = MagicMock()
        ex.executor.get_halted_symbols.return_value = {}

        # 不抛
        await ex._publish_halts_snapshot()
        ex.logger.warning.assert_called()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_tg_status_enhancement.py::TestMultiExecutorPublishHaltsSnapshot -v`
Expected: 2 FAIL（_publish_halts_snapshot 未定义）

- [ ] **Step 3: 实现 _publish_halts_snapshot**

`agents/trading/executor.py` 在 `_run_reconciliation` 之前或之后位置增加：

```python
    async def _publish_halts_snapshot(self):
        """F-TG-004: 周期性 publish halts_snapshot 事件供 Orchestrator 写 agent_health.json。

        payload schema:
            halted_symbols: dict {symbol -> {reason, halted_at}}
            ts: 当前时戳
        """
        try:
            halts = self.executor.get_halted_symbols()
            await self.publish('halts_snapshot', {
                'halted_symbols': halts,
                'ts': time.time(),
            })
        except Exception as e:
            self.logger.warning(f"[HaltsSnapshot] publish 失败: {e}")
```

确认 `agents/trading/executor.py` 顶部已 import `time`（不在则补充）。

- [ ] **Step 4: 在 _run_reconciliation 末尾调用**

定位 `_run_reconciliation` 函数（约 line 690-740 范围），在函数末尾追加：

```python
        # F-TG-004: 同步 publish halts_snapshot 给 Orchestrator
        await self._publish_halts_snapshot()
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest -q test_tg_status_enhancement.py::TestMultiExecutorPublishHaltsSnapshot test_pnl_resolved_event_contract.py -v`
Expected: 2 + 28 = 30 PASS

- [ ] **Step 6: 提交**

```bash
git add agents/trading/executor.py test_tg_status_enhancement.py
git commit -m "[TG-OPS][TG-004] MultiExecutor publish halts_snapshot 周期事件

新增 _publish_halts_snapshot 方法,_run_reconciliation 末尾调用。
payload 含 halted_symbols(get_halted_symbols 浅拷贝)+ ts。
publish 失败 logger.warning 不阻塞主循环。"
```

---

### Task 5: Orchestrator 订阅 halts_snapshot + 写 agent_health.json

**Files:**
- Modify: `agents/orchestrator.py` (订阅 + 缓存 + _health_loop + _write_agent_health)
- Test: `test_tg_status_enhancement.py` (扩展)

- [ ] **Step 1: 阅读现有 Orchestrator**

Run: `grep -nE "register|asyncio.create_task|_research_loop|_command_listener" agents/orchestrator.py | head -20`

确认现有 loop 模式：`_research_loop` 与 `_command_listener` 都是 `await asyncio.sleep(...)` + while loop。新加 `_health_loop` 同模式。

- [ ] **Step 2: 写失败测试**

```python
class TestOrchestratorWritesAgentHealth:
    def _make_orchestrator(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        # 切换 cwd 到 tmp_path 让 data/ 写入临时目录
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        from agents.orchestrator import Orchestrator
        orch = Orchestrator.__new__(Orchestrator)
        orch.logger = MagicMock()
        orch._tasks = []
        orch._research_agents = []
        orch._trading_agents = []
        orch._latest_halts_snapshot = {}
        return orch

    def test_write_agent_health_creates_file_with_schema(self, tmp_path, monkeypatch):
        orch = self._make_orchestrator(tmp_path, monkeypatch)
        orch._tasks = [MagicMock(done=lambda: False, exception=lambda: None) for _ in range(3)]
        orch._research_agents = [object()] * 5
        orch._trading_agents = [object()] * 10
        orch._latest_halts_snapshot = {"XLM-USDT-SWAP": {"reason": "x"}}

        orch._write_agent_health()

        path = "data/testnet_agent_health.json"
        assert os.path.exists(path)
        with open(path) as f:
            health = json.load(f)
        assert health["agents_registered"] == 15
        assert health["tasks_alive"] == 3
        assert health["tasks_failed"] == 0
        assert health["halted_symbols"] == {"XLM-USDT-SWAP": {"reason": "x"}}
        assert "ts" in health
        assert "bus_dlq_size" in health  # 0 if no bus

    def test_write_agent_health_counts_failed_tasks(self, tmp_path, monkeypatch):
        orch = self._make_orchestrator(tmp_path, monkeypatch)
        # 2 个 alive, 1 个 done with exception, 1 个 done without exception
        t_alive_1 = MagicMock(done=lambda: False)
        t_alive_2 = MagicMock(done=lambda: False)
        t_failed = MagicMock(done=lambda: True, exception=lambda: RuntimeError("boom"))
        t_finished = MagicMock(done=lambda: True, exception=lambda: None)
        orch._tasks = [t_alive_1, t_alive_2, t_failed, t_finished]

        orch._write_agent_health()

        with open("data/testnet_agent_health.json") as f:
            health = json.load(f)
        assert health["tasks_alive"] == 2
        assert health["tasks_failed"] == 1

    def test_write_agent_health_failure_does_not_raise(self, tmp_path, monkeypatch):
        orch = self._make_orchestrator(tmp_path, monkeypatch)
        # 让 atomic_write_json 失败:写到不存在的目录
        with patch("utils.atomic_io.atomic_write_json", side_effect=OSError("disk full")):
            orch._write_agent_health()
        orch.logger.warning.assert_called()

    def test_orchestrator_caches_halts_snapshot_event(self, tmp_path, monkeypatch):
        orch = self._make_orchestrator(tmp_path, monkeypatch)
        msg = {"type": "halts_snapshot", "payload": {
            "halted_symbols": {"X": {"reason": "y"}}, "ts": 1.0
        }}
        # _on_halts_snapshot 是新方法
        orch._on_halts_snapshot(msg)
        assert orch._latest_halts_snapshot == {"X": {"reason": "y"}}
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python3 -m pytest -q test_tg_status_enhancement.py::TestOrchestratorWritesAgentHealth -v`
Expected: 4 FAIL

- [ ] **Step 4: 修改 Orchestrator**

`agents/orchestrator.py` `__init__` 增加缓存字段：

```python
class Orchestrator:
    def __init__(self, config: dict = None):
        # ... 现有字段 ...
        self._latest_halts_snapshot: dict = {}  # F-TG-004
        self._health_write_interval: float = 30.0  # F-TG-004
```

新增订阅处理与写入方法：

```python
    def _on_halts_snapshot(self, msg: dict):
        """F-TG-004: 缓存 halts_snapshot 事件,供 _write_agent_health 用。"""
        try:
            payload = msg.get('payload', {}) or {}
            halts = payload.get('halted_symbols', {})
            self._latest_halts_snapshot = dict(halts) if halts else {}
        except Exception as e:
            self.logger.warning(f"[Orchestrator] _on_halts_snapshot 异常: {e}")

    def _write_agent_health(self):
        """F-TG-004: 写 data/<ns_>agent_health.json。失败 logger.warning 不抛。"""
        try:
            from utils.state_paths import get_state_paths
            from utils.atomic_io import atomic_write_json
            from agents.message_bus import MessageBus

            tasks_alive = 0
            tasks_failed = 0
            for t in self._tasks:
                try:
                    if t.done():
                        if t.exception() is not None:
                            tasks_failed += 1
                    else:
                        tasks_alive += 1
                except Exception:
                    pass  # 单个 task 异常不影响整体计数

            agents_registered = len(self._research_agents) + len(self._trading_agents)

            try:
                bus = MessageBus.get_instance()
                dlq_size = len(getattr(bus, '_dead_letter', []))
            except Exception:
                dlq_size = 0

            health = {
                'ts': time.time(),
                'agents_registered': agents_registered,
                'tasks_alive': tasks_alive,
                'tasks_failed': tasks_failed,
                'halted_symbols': dict(self._latest_halts_snapshot),
                'bus_dlq_size': dlq_size,
            }
            path = get_state_paths().agent_health
            atomic_write_json(path, health)
        except Exception as e:
            self.logger.warning(f"[AgentHealth] 写入失败: {e}")

    async def _health_loop(self):
        """F-TG-004: 每 30s 写一次 agent_health.json。"""
        while not self._shutdown_event.is_set():
            self._write_agent_health()
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self._health_write_interval,
                )
            except asyncio.TimeoutError:
                continue
```

确认 `agents/orchestrator.py` 顶部已 import `time`（不在则补充）。

在 `_run` 方法（line 104+）启动 loop 处增加：

```python
        # F-TG-004: 启动 health.json 写入循环
        self._tasks.append(asyncio.create_task(self._health_loop()))
```

并在 bus 注册阶段订阅 `halts_snapshot`：

```python
        bus.register('orchestrator', topics=['research_*', 'halts_snapshot'])

        async def _orch_consumer():
            while not self._shutdown_event.is_set():
                try:
                    msg = await asyncio.wait_for(
                        bus.receive('orchestrator', timeout=1.0),
                        timeout=2.0,
                    )
                    if msg is None:
                        continue
                    if msg.get('type') == 'halts_snapshot':
                        self._on_halts_snapshot(msg)
                    # 其他研判事件由现有 _research_loop 处理
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    self.logger.warning(f"[Orchestrator] consumer 异常: {e}")
```

⚠️ **注意**：如果现有 Orchestrator 已经有 bus consumer 模式（通过 `_research_loop` 等），优先复用；如果没有，新增 `_orch_consumer` task。具体集成方式以读现状为准。本 plan 提供的是"如何加"的范式，具体合并要适配 `_research_loop` 现有结构。

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest -q test_tg_status_enhancement.py::TestOrchestratorWritesAgentHealth -v`
Expected: 4 PASS

- [ ] **Step 6: 跑既有 orchestrator 相关测试确认无回归**

Run: `python3 -m pytest -q test_state_namespace.py 2>&1 | tail -5`
Expected: 16 PASS

- [ ] **Step 7: 提交**

```bash
git add agents/orchestrator.py test_tg_status_enhancement.py
git commit -m "[TG-OPS][TG-004] Orchestrator 订阅 halts_snapshot + 写 agent_health.json

新增 _on_halts_snapshot / _write_agent_health / _health_loop。
每 30s 写 data/<ns_>agent_health.json,schema 含 ts /
agents_registered / tasks_alive / tasks_failed / halted_symbols /
bus_dlq_size。失败 logger.warning 不阻塞主循环。"
```

---

## F-TG-002 `/halts` `/resume_symbol` `/status` per-symbol halt

### Task 6: TG `/halts` 命令（读 health.json）

**Files:**
- Modify: `agents/trading/telegram_notifier.py` (handlers 字典 + `_cmd_halts` + `_read_agent_health` helper + `_format_elapsed` helper)
- Test: `test_tg_symbol_halt_control.py` (扩展)

- [ ] **Step 1: 写失败测试**

```python
class TestCmdHalts:
    def _make_notifier(self):
        from agents.trading.telegram_notifier import TelegramNotifier
        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._chat_id = "12345"
        return n

    @pytest.mark.asyncio
    async def test_cmd_halts_no_halts(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        with open("data/testnet_agent_health.json", "w") as f:
            json.dump({"halted_symbols": {}}, f)

        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_halts()
        assert any("无 per-symbol halt" in s for s in sent)

    @pytest.mark.asyncio
    async def test_cmd_halts_one_symbol(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        with open("data/testnet_agent_health.json", "w") as f:
            json.dump({
                "halted_symbols": {
                    "XLM-USDT-SWAP": {
                        "reason": "sl_replace_failed",
                        "halted_at": time.time() - 3600,  # 1 hour ago
                    }
                }
            }, f)

        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_halts()
        text = "\n".join(sent)
        assert "XLM-USDT-SWAP" in text
        assert "sl_replace_failed" in text
        # 显示 1h something ago
        assert "1h" in text or "60m" in text

    @pytest.mark.asyncio
    async def test_cmd_halts_health_file_missing(self, tmp_path, monkeypatch):
        """health.json 缺失时返回降级文案,不抛错。"""
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        # 不创建文件

        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_halts()
        text = "\n".join(sent)
        assert "无 per-symbol halt" in text or "缺失" in text or "?" in text


class TestFormatElapsed:
    def test_seconds(self):
        from agents.trading.telegram_notifier import TelegramNotifier
        assert TelegramNotifier._format_elapsed(45) == "45s"

    def test_minutes(self):
        from agents.trading.telegram_notifier import TelegramNotifier
        assert TelegramNotifier._format_elapsed(120) == "2m"

    def test_hours(self):
        from agents.trading.telegram_notifier import TelegramNotifier
        assert TelegramNotifier._format_elapsed(3700) == "1h1m"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_tg_symbol_halt_control.py::TestCmdHalts test_tg_symbol_halt_control.py::TestFormatElapsed -v`
Expected: 6 FAIL

- [ ] **Step 3: 实现 helper 与命令**

`agents/trading/telegram_notifier.py` 增加 helper（建议放在 `_cmd_status` 附近，约 line 396 之前）：

```python
    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        """F-TG-002: 格式化经过时间为人类可读 '2h15m' / '45s'。"""
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m"
        hours = minutes // 60
        return f"{hours}h{minutes % 60}m"

    def _read_agent_health(self) -> Optional[dict]:
        """F-TG-002: 读 data/<ns_>agent_health.json,失败返回 None。"""
        try:
            from utils.state_paths import get_state_paths
            path = get_state_paths().agent_health
            with open(path, 'r') as f:
                return json.load(f)
        except Exception:
            return None

    async def _cmd_halts(self):
        """F-TG-002: 列出当前 per-symbol halt。"""
        health = self._read_agent_health() or {}
        halts = health.get('halted_symbols', {})

        if not halts:
            await self._send_message("✅ 无 per-symbol halt")
            return

        lines = [f"🔒 Per-symbol halt: {len(halts)} 个"]
        now = time.time()
        for sym, info in halts.items():
            reason = info.get('reason', '?')
            halted_at = info.get('halted_at', 0)
            elapsed = now - halted_at if halted_at else 0
            lines.append(f"• {sym}")
            lines.append(f"  reason: {reason}")
            lines.append(f"  halted: {self._format_elapsed(elapsed)} ago")
        await self._send_message("\n".join(lines))
```

确认 telegram_notifier.py 顶部已 import `Optional`（不在则补充 `from typing import Optional`）。

`_handle_command` handlers 字典加：

```python
        handlers = {
            '/status': self._cmd_status,
            '/positions': self._cmd_positions,
            '/stop': self._cmd_stop,
            '/restart': self._cmd_restart,
            '/halt': self._cmd_halt,
            '/resume': self._cmd_resume,
            '/force_resume': self._cmd_force_resume,
            '/reconcile': self._cmd_reconcile,
            '/log': self._cmd_log,
            '/halts': self._cmd_halts,           # F-TG-002
        }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest -q test_tg_symbol_halt_control.py::TestCmdHalts test_tg_symbol_halt_control.py::TestFormatElapsed -v`
Expected: 6 PASS

- [ ] **Step 5: 提交**

```bash
git add agents/trading/telegram_notifier.py test_tg_symbol_halt_control.py
git commit -m "[TG-OPS][TG-002] add /halts command + _read_agent_health/_format_elapsed helpers

/halts 从 agent_health.json 读 halted_symbols 列表,格式化输出
symbol/reason/halted-elapsed-time。无 halt 时输出明确文案。
health 文件缺失时降级。_format_elapsed 支持秒/分/时人类可读格式。"
```

---

### Task 7: TG `/resume_symbol` 走 bus + MultiExecutor 处理

**Files:**
- Modify: `agents/trading/telegram_notifier.py` (handlers + `_cmd_resume_symbol` + `_handle_command` 支持 args list)
- Modify: `agents/trading/executor.py:on_message` (新增 `cmd='resume_symbol'` 分支)
- Modify: `agents/trading/telegram_notifier.py:_handle_risk_alert` (新增 `symbol_halt_cleared` / `symbol_halt_not_found` / `force_resume_cleared_symbol_halts` 三个 alert types)
- Test: `test_tg_symbol_halt_control.py` (扩展)

- [ ] **Step 1: 写失败测试**

```python
class TestCmdResumeSymbolViaBus:
    @pytest.mark.asyncio
    async def test_resume_symbol_publishes_system_command(self):
        from agents.trading.telegram_notifier import TelegramNotifier

        published = []
        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._chat_id = "12345"
        n.publish = fake_publish
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_resume_symbol(["XLM"])

        topics = [t for t, _ in published]
        assert "system_command" in topics
        cmd_payload = next(p for t, p in published if t == "system_command")
        assert cmd_payload["command"] == "resume_symbol"
        assert cmd_payload["symbol"] == "XLM-USDT-SWAP"
        assert cmd_payload["source"] == "telegram"
        assert any("XLM" in s for s in sent)

    @pytest.mark.asyncio
    async def test_resume_symbol_no_args_returns_usage(self):
        from agents.trading.telegram_notifier import TelegramNotifier

        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._chat_id = "12345"
        published = []
        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))
        n.publish = fake_publish
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_resume_symbol([])

        # 不发 system_command
        assert all(t != "system_command" for t, _ in published)
        # 给出用法
        assert any("用法" in s or "usage" in s.lower() for s in sent)


class TestExecutorAgentResumeSymbol:
    @pytest.mark.asyncio
    async def test_resume_symbol_calls_clear_and_publishes_cleared_alert(self):
        from agents.trading.executor import MultiExecutor

        published = []
        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.publish = fake_publish
        ex.executor = MagicMock()
        ex.executor._normalize_symbol.return_value = "XLM-USDT-SWAP"
        ex.executor.clear_symbol_halt = MagicMock(return_value=1)

        msg = {"type": "system_command", "payload": {
            "command": "resume_symbol",
            "symbol": "XLM-USDT-SWAP",
            "source": "telegram",
        }}
        await ex.on_message(msg)

        ex.executor.clear_symbol_halt.assert_called_once_with("XLM-USDT-SWAP")
        types = [p.get("type") for t, p in published if t == "risk_alert"]
        assert "symbol_halt_cleared" in types

    @pytest.mark.asyncio
    async def test_resume_symbol_not_found_publishes_not_found_alert(self):
        from agents.trading.executor import MultiExecutor

        published = []
        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.publish = fake_publish
        ex.executor = MagicMock()
        ex.executor._normalize_symbol.return_value = "X-USDT-SWAP"
        ex.executor.clear_symbol_halt = MagicMock(return_value=0)

        msg = {"type": "system_command", "payload": {
            "command": "resume_symbol",
            "symbol": "X-USDT-SWAP",
            "source": "telegram",
        }}
        await ex.on_message(msg)

        types = [p.get("type") for t, p in published if t == "risk_alert"]
        assert "symbol_halt_not_found" in types

    @pytest.mark.asyncio
    async def test_resume_symbol_empty_symbol_no_op(self):
        from agents.trading.executor import MultiExecutor

        published = []
        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.logger = MagicMock()
        ex.publish = fake_publish
        ex.executor = MagicMock()
        ex.executor.clear_symbol_halt = MagicMock()

        msg = {"type": "system_command", "payload": {
            "command": "resume_symbol",
            "symbol": "",
            "source": "telegram",
        }}
        await ex.on_message(msg)

        # 不调 clear_symbol_halt
        ex.executor.clear_symbol_halt.assert_not_called()


class TestTelegramAlertSubscriptions:
    @pytest.mark.asyncio
    async def test_handles_symbol_halt_cleared(self):
        from agents.trading.telegram_notifier import TelegramNotifier

        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._daily_summary = {"alerts": 0}
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        msg = {"type": "risk_alert", "payload": {
            "type": "symbol_halt_cleared",
            "symbol": "XLM-USDT-SWAP",
            "source": "telegram",
        }}
        await n._handle_risk_alert(msg)
        text = "\n".join(sent)
        assert "XLM-USDT-SWAP" in text
        assert "解除" in text or "cleared" in text.lower()

    @pytest.mark.asyncio
    async def test_handles_symbol_halt_not_found(self):
        from agents.trading.telegram_notifier import TelegramNotifier

        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._daily_summary = {"alerts": 0}
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        msg = {"type": "risk_alert", "payload": {
            "type": "symbol_halt_not_found",
            "symbol": "XYZ-USDT-SWAP",
            "source": "telegram",
        }}
        await n._handle_risk_alert(msg)
        text = "\n".join(sent)
        assert "XYZ-USDT-SWAP" in text
        assert "没有" in text or "not_found" in text.lower() or "未" in text

    @pytest.mark.asyncio
    async def test_handles_force_resume_cleared(self):
        from agents.trading.telegram_notifier import TelegramNotifier

        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._daily_summary = {"alerts": 0}
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        msg = {"type": "risk_alert", "payload": {
            "type": "force_resume_cleared_symbol_halts",
            "cleared_symbols": ["XLM-USDT-SWAP (sl_replace_failed)"],
            "source": "telegram",
        }}
        await n._handle_risk_alert(msg)
        text = "\n".join(sent)
        assert "XLM-USDT-SWAP" in text
        assert "force_resume" in text or "强制" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_tg_symbol_halt_control.py::TestCmdResumeSymbolViaBus test_tg_symbol_halt_control.py::TestExecutorAgentResumeSymbol test_tg_symbol_halt_control.py::TestTelegramAlertSubscriptions -v`
Expected: 8 FAIL

- [ ] **Step 3: 修改 _handle_command 支持 args list**

`agents/trading/telegram_notifier.py:_handle_command`：

```python
    async def _handle_command(self, update: dict):
        msg = update.get('message', {})
        chat_id = msg.get('chat', {}).get('id')
        text = (msg.get('text') or '').strip()

        if str(chat_id) != str(self._chat_id):
            return

        parts = text.split()
        cmd = parts[0] if parts else ''
        args = parts[1:]
        handlers = {
            '/status': self._cmd_status,
            '/positions': self._cmd_positions,
            '/stop': self._cmd_stop,
            '/restart': self._cmd_restart,
            '/halt': self._cmd_halt,
            '/resume': self._cmd_resume,
            '/force_resume': self._cmd_force_resume,
            '/reconcile': self._cmd_reconcile,
            '/log': self._cmd_log,
            '/halts': self._cmd_halts,                       # F-TG-002
            '/resume_symbol': self._cmd_resume_symbol,        # F-TG-002
        }
        handlers_with_args = {'/resume_symbol'}  # 需要 args 的命令

        handler = handlers.get(cmd)
        if handler:
            self.logger.info(f"[Telegram] 收到命令: {cmd}")
            if cmd in handlers_with_args:
                await handler(args)
            else:
                await handler()
        elif text.startswith('/'):
            self.logger.info(f"[Telegram] 未知命令: {text}")
```

- [ ] **Step 4: 实现 _cmd_resume_symbol**

```python
    async def _cmd_resume_symbol(self, args: list):
        """F-TG-002: 通过 bus system_command 单 symbol 解锁。"""
        if not args:
            await self._send_message("用法: /resume_symbol <SYMBOL>")
            return

        raw = args[0].strip().upper()
        # TG 端粗归一化:容忍带后缀,统一加 -USDT-SWAP
        if raw.endswith('-SWAP'):
            symbol = raw
        elif raw.endswith('-USDT'):
            symbol = f"{raw}-SWAP"
        else:
            symbol = f"{raw}-USDT-SWAP"

        await self.publish('system_command', {
            'command': 'resume_symbol',
            'symbol': symbol,
            'source': 'telegram',
        })
        await self._send_message(f"🔄 已发送 /resume_symbol {symbol} 请求")
```

- [ ] **Step 5: 修改 MultiExecutor on_message 增加 cmd='resume_symbol' 分支**

`agents/trading/executor.py:on_message` system_command 分支扩展（在 `force_resume` 之后）：

```python
            elif cmd == 'force_resume':
                # ... 现有 + Task 2 改造 ...
            elif cmd == 'resume_symbol':
                # F-TG-002: 单 symbol 解锁
                symbol_raw = msg.get('payload', {}).get('symbol', '').strip()
                if not symbol_raw:
                    return
                normalized = self.executor._normalize_symbol(symbol_raw)
                cleared = self.executor.clear_symbol_halt(normalized)
                if cleared > 0:
                    await self.publish('risk_alert', {
                        'type': 'symbol_halt_cleared',
                        'symbol': normalized,
                        'source': source,
                    })
                    self.logger.info(
                        f"[ResumeSymbol] {source} 解除 {normalized} per-symbol halt"
                    )
                else:
                    await self.publish('risk_alert', {
                        'type': 'symbol_halt_not_found',
                        'symbol': normalized,
                        'source': source,
                    })
            return
```

- [ ] **Step 6: 修改 telegram_notifier _handle_risk_alert 处理三种新 alert**

定位 `_handle_risk_alert`（约 line 189-220），在 `critical_types` allowlist 加新类型，并在分支处理：

```python
    async def _handle_risk_alert(self, msg: dict):
        payload = msg['payload']
        alert_type = payload.get('type', '')
        symbol = payload.get('symbol', '')
        self._daily_summary['alerts'] += 1

        critical_types = (
            'flash_move', 'max_drawdown', 'emergency_close', 'llm_degraded',
            'protection_failed',
            'symbol_halt_cleared',                  # F-TG-002
            'symbol_halt_not_found',                # F-TG-002
            'force_resume_cleared_symbol_halts',    # F-TG-001
        )
        if alert_type not in critical_types:
            return

        # F-TG-002: 三种新 alert 类型独立分支
        if alert_type == 'symbol_halt_cleared':
            text = f"✅ {symbol} per-symbol halt 已解除 (来源: {payload.get('source', '?')})"
            await self._send_message(text)
            return

        if alert_type == 'symbol_halt_not_found':
            text = f"ℹ️ {symbol} 没有 per-symbol halt (无需解除)"
            await self._send_message(text)
            return

        if alert_type == 'force_resume_cleared_symbol_halts':
            cleared = payload.get('cleared_symbols', [])
            text = (
                f"⚠️ /force_resume 同时清除了 {len(cleared)} 个 per-symbol halt:\n"
                + "\n".join(f"  • {s}" for s in cleared)
                + "\n\n请确认根因已排除"
            )
            await self._send_message(text)
            return

        # 现有 critical_types 处理（flash_move 等）保持不变
        # ... 现有代码 ...
```

⚠️ **保留 protection_failed 现有分支**：F4-001 的 `protection_failed` 处理逻辑（line 216-219）保持不变，只是上面的新分支 `return`，避免落到下面通用处理。

- [ ] **Step 7: 运行测试确认通过**

Run: `python3 -m pytest -q test_tg_symbol_halt_control.py -v`
Expected: 全部 PASS（累计 ~26 case）

- [ ] **Step 8: 跑既有 telegram + executor 测试无回归**

Run: `python3 -m pytest -q test_telegram.py test_executor_upgrade.py 2>&1 | tail -5`
Expected: 全 PASS

- [ ] **Step 9: 提交**

```bash
git add agents/trading/telegram_notifier.py agents/trading/executor.py test_tg_symbol_halt_control.py
git commit -m "[TG-OPS][TG-002] /resume_symbol via bus + 三种 risk_alert 回显

TG /resume_symbol 通过 bus system_command 路由到 MultiExecutor agent,
再调 self.executor.clear_symbol_halt(normalized)。TG agent 不持有
root executor 引用(agent 隔离)。新增 risk_alert types:
symbol_halt_cleared / symbol_halt_not_found /
force_resume_cleared_symbol_halts(F-TG-001 的 audit 回显)。
critical_types allowlist 同步更新。"
```

---

### Task 8: `/status` 增强 per-symbol halt + agent health 行

**Files:**
- Modify: `agents/trading/telegram_notifier.py:_cmd_status` (line 396-437)
- Test: `test_tg_status_enhancement.py` (扩展)

- [ ] **Step 1: 写失败测试**

```python
class TestStatusEnhancement:
    def _make_notifier(self):
        from agents.trading.telegram_notifier import TelegramNotifier
        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._chat_id = "12345"
        n._start_time = time.time() - 3600  # 1h uptime
        n._daily_summary = {"trades": 0, "pnl": 0.0, "alerts": 0}
        return n

    @pytest.mark.asyncio
    async def test_status_includes_agents_line(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        with open("data/testnet_agent_health.json", "w") as f:
            json.dump({
                "ts": time.time(),
                "agents_registered": 17,
                "tasks_alive": 17,
                "tasks_failed": 0,
                "halted_symbols": {},
                "bus_dlq_size": 0,
            }, f)
        # 同步建必要的状态文件让 _cmd_status 不崩
        for fn in ("testnet_positions.json", "testnet_riskguard_state.json", "testnet_halt_state.json"):
            with open(f"data/{fn}", "w") as f:
                json.dump({}, f)

        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        with patch("agents.trading.telegram_notifier._positions_path", return_value="data/testnet_positions.json"), \
             patch("agents.trading.telegram_notifier._riskguard_path", return_value="data/testnet_riskguard_state.json"):
            await n._cmd_status()

        text = "\n".join(sent)
        assert "Agents" in text
        assert "17" in text  # agents_registered

    @pytest.mark.asyncio
    async def test_status_includes_per_symbol_halt_line_zero(self, tmp_path, monkeypatch):
        # 同上 setup,halted_symbols 空
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        with open("data/testnet_agent_health.json", "w") as f:
            json.dump({
                "agents_registered": 17, "tasks_alive": 17, "tasks_failed": 0,
                "halted_symbols": {}, "bus_dlq_size": 0,
            }, f)
        for fn in ("testnet_positions.json", "testnet_riskguard_state.json"):
            with open(f"data/{fn}", "w") as f:
                json.dump({}, f)

        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        with patch("agents.trading.telegram_notifier._positions_path", return_value="data/testnet_positions.json"), \
             patch("agents.trading.telegram_notifier._riskguard_path", return_value="data/testnet_riskguard_state.json"):
            await n._cmd_status()

        text = "\n".join(sent)
        assert "Per-symbol halt: 0" in text

    @pytest.mark.asyncio
    async def test_status_includes_per_symbol_halt_line_one(self, tmp_path, monkeypatch):
        # halted_symbols 一项
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        with open("data/testnet_agent_health.json", "w") as f:
            json.dump({
                "agents_registered": 17, "tasks_alive": 17, "tasks_failed": 0,
                "halted_symbols": {"XLM-USDT-SWAP": {"reason": "x"}},
                "bus_dlq_size": 0,
            }, f)
        for fn in ("testnet_positions.json", "testnet_riskguard_state.json"):
            with open(f"data/{fn}", "w") as f:
                json.dump({}, f)

        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        with patch("agents.trading.telegram_notifier._positions_path", return_value="data/testnet_positions.json"), \
             patch("agents.trading.telegram_notifier._riskguard_path", return_value="data/testnet_riskguard_state.json"):
            await n._cmd_status()

        text = "\n".join(sent)
        assert "Per-symbol halt: 1" in text
        assert "XLM" in text

    @pytest.mark.asyncio
    async def test_status_truncates_many_halts(self, tmp_path, monkeypatch):
        # 7 个 halt
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        halts = {f"S{i}-USDT-SWAP": {"reason": "r"} for i in range(7)}
        with open("data/testnet_agent_health.json", "w") as f:
            json.dump({
                "agents_registered": 17, "tasks_alive": 17, "tasks_failed": 0,
                "halted_symbols": halts, "bus_dlq_size": 0,
            }, f)
        for fn in ("testnet_positions.json", "testnet_riskguard_state.json"):
            with open(f"data/{fn}", "w") as f:
                json.dump({}, f)

        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        with patch("agents.trading.telegram_notifier._positions_path", return_value="data/testnet_positions.json"), \
             patch("agents.trading.telegram_notifier._riskguard_path", return_value="data/testnet_riskguard_state.json"):
            await n._cmd_status()

        text = "\n".join(sent)
        assert "Per-symbol halt: 7" in text
        assert "+2" in text or "…" in text  # 截断标记

    @pytest.mark.asyncio
    async def test_status_health_missing_falls_back(self, tmp_path, monkeypatch):
        """health.json 缺失时仍返回基础 status,health 行降级文案。"""
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        from utils.state_paths import reset_state_paths
        reset_state_paths()
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        # 不创建 health.json
        for fn in ("testnet_positions.json", "testnet_riskguard_state.json"):
            with open(f"data/{fn}", "w") as f:
                json.dump({}, f)

        n = self._make_notifier()
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        with patch("agents.trading.telegram_notifier._positions_path", return_value="data/testnet_positions.json"), \
             patch("agents.trading.telegram_notifier._riskguard_path", return_value="data/testnet_riskguard_state.json"):
            await n._cmd_status()

        text = "\n".join(sent)
        # 不抛错;基础字段(运行时长 / 持仓 / 熔断)仍在
        assert "运行" in text
        # health 行降级
        assert "缺失" in text or "?" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_tg_status_enhancement.py::TestStatusEnhancement -v`
Expected: 5 FAIL

- [ ] **Step 3: 修改 _cmd_status**

定位 `_cmd_status`（line 396），在原有 `text` 构造之后、`await self._send_message(text)` 之前插入：

```python
    async def _cmd_status(self):
        # 现有代码 line 396-435...

        # F-TG-004: 增加 health 行
        health = self._read_agent_health()
        if health:
            agents_registered = health.get('agents_registered', '?')
            tasks_alive = health.get('tasks_alive', '?')
            tasks_failed = health.get('tasks_failed', 0)
            dlq = health.get('bus_dlq_size', 0)
            text += f"\n─ Agents: {agents_registered} 注册 / {tasks_alive} 任务存活 / {tasks_failed} 异常"
            text += f"\n─ Bus DLQ: {dlq}"

            halts = health.get('halted_symbols', {})
            if not halts:
                text += "\n─ Per-symbol halt: 0"
            else:
                short_list = list(halts.keys())[:5]
                suffix = f" …+{len(halts) - 5}" if len(halts) > 5 else ""
                halt_str = ", ".join(s.split("-")[0] for s in short_list)  # 取 base
                text += f"\n─ Per-symbol halt: {len(halts)} ({halt_str}{suffix})"
        else:
            text += "\n─ Health: ?（agent_health.json 缺失）"

        await self._send_message(text)
```

注意：原有 `await self._send_message(text)` 在 line ~437，需要把上面的 health 块放在它**之前**。

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest -q test_tg_status_enhancement.py::TestStatusEnhancement -v`
Expected: 5 PASS

- [ ] **Step 5: 跑既有 status 测试无回归**

Run: `python3 -m pytest -q test_telegram.py 2>&1 | tail -5`
Expected: 全 PASS

- [ ] **Step 6: 提交**

```bash
git add agents/trading/telegram_notifier.py test_tg_status_enhancement.py
git commit -m "[TG-OPS][TG-004] /status 增强:Agents 行 + Bus DLQ 行 + Per-symbol halt 行

读 data/<ns_>agent_health.json 增强输出。多 halt 时截断展示前 5 个。
health 文件缺失时降级文案,基础字段不受影响。"
```

---

## F-TG-003 `/pnl` `/pnl_id` 手动 PnL correction

### Task 9: 共用 helper `_resolve_pending_for_pnl_correction`

**Files:**
- Modify: `agents/trading/telegram_notifier.py` (新增 helper + `_apply_pnl_correction`)
- Test: `test_tg_pnl_correction.py` (Create)

- [ ] **Step 1: 写失败测试**

新建 `test_tg_pnl_correction.py`：

```python
"""F-TG-003 /pnl + /pnl_id 测试矩阵。"""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


def _make_notifier_with_ledger(pending_events=None):
    """构造带 ledger mock 的 TelegramNotifier。"""
    from agents.trading.telegram_notifier import TelegramNotifier
    n = TelegramNotifier.__new__(TelegramNotifier)
    n.logger = MagicMock()
    n._chat_id = "12345"
    n._ledger = MagicMock()
    n._ledger.find_pending_external_closes.return_value = pending_events or []
    return n


class TestResolvePendingHelper:
    def test_resolve_one_candidate_returns_ok(self):
        n = _make_notifier_with_ledger([
            {"event_id": "e1", "symbol": "XLM-USDT-SWAP", "pnl_status": "pending"},
            {"event_id": "e2", "symbol": "BTC-USDT-SWAP", "pnl_status": "pending"},
        ])
        result = n._resolve_pending_for_pnl_correction(
            filter_fn=lambda ev: ev["symbol"] == "XLM-USDT-SWAP",
            label="symbol=XLM",
        )
        assert result["status"] == "ok"
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["event_id"] == "e1"

    def test_resolve_zero_candidates_returns_not_found(self):
        n = _make_notifier_with_ledger([
            {"event_id": "e1", "symbol": "BTC-USDT-SWAP"},
        ])
        result = n._resolve_pending_for_pnl_correction(
            filter_fn=lambda ev: ev["symbol"] == "XLM-USDT-SWAP",
            label="symbol=XLM",
        )
        assert result["status"] == "not_found"
        assert "XLM" in result["error_msg"]

    def test_resolve_multiple_candidates_returns_multiple(self):
        n = _make_notifier_with_ledger([
            {"event_id": "e1", "symbol": "XLM-USDT-SWAP"},
            {"event_id": "e2", "symbol": "XLM-USDT-SWAP"},
        ])
        result = n._resolve_pending_for_pnl_correction(
            filter_fn=lambda ev: ev["symbol"] == "XLM-USDT-SWAP",
            label="symbol=XLM",
        )
        assert result["status"] == "multiple"
        assert len(result["candidates"]) == 2

    def test_resolve_no_ledger_returns_error(self):
        from agents.trading.telegram_notifier import TelegramNotifier
        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._ledger = None
        result = n._resolve_pending_for_pnl_correction(
            filter_fn=lambda ev: True,
            label="any",
        )
        assert result["status"] == "error"
        assert "ledger" in result["error_msg"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_tg_pnl_correction.py::TestResolvePendingHelper -v`
Expected: 4 FAIL

- [ ] **Step 3: 实现 helper 与 _apply_pnl_correction**

`agents/trading/telegram_notifier.py` 增加：

```python
    def _resolve_pending_for_pnl_correction(self, filter_fn, label: str) -> dict:
        """F-TG-003: 共享候选解析。

        Args:
            filter_fn: callable(event_dict) -> bool, 过滤候选
            label: 错误消息中的标签(如 "symbol=XLM" / "event_id=abc")

        Returns:
            {status: 'ok'|'not_found'|'multiple'|'error',
             candidates: list,
             error_msg: str}
        """
        if not getattr(self, '_ledger', None):
            return {"status": "error", "candidates": [],
                    "error_msg": "ledger 未初始化"}

        try:
            all_pending = self._ledger.find_pending_external_closes()
        except Exception as e:
            return {"status": "error", "candidates": [],
                    "error_msg": f"查询 pending 失败: {e}"}

        candidates = [ev for ev in (all_pending or []) if filter_fn(ev)]

        if len(candidates) == 0:
            return {"status": "not_found", "candidates": [],
                    "error_msg": f"未找到 {label} 的活跃 pending external_close"}
        if len(candidates) > 1:
            return {"status": "multiple", "candidates": candidates,
                    "error_msg": f"{label} 匹配 {len(candidates)} 条 pending"}
        return {"status": "ok", "candidates": candidates, "error_msg": ""}

    async def _apply_pnl_correction(self, pending_ev: dict, net_pnl: float, reason: str):
        """F-TG-003: 根据 pending event 写 manual correction 并回显。"""
        resolution = {
            "pnl_status": "final",
            "pnl_source": "manual_tg_review",
            "symbol": pending_ev.get('symbol', ''),
            "side": pending_ev.get('side', ''),
            "position_id": pending_ev.get('position_id', ''),
            "entry_request_id": pending_ev.get('entry_request_id', ''),
            "realized_pnl_net_usdt": net_pnl,
            "estimated_pnl": pending_ev.get('estimated_pnl', 0),
            "gross_close_pnl_usdt": net_pnl,
            "fee_usdt": 0.0,
            "funding_usdt": 0.0,
            "order_ids": [],
            "bill_ids": [],
            "match_confidence": 1.0,
            "warnings": ["manual_pnl_correction"],
            "close_match_key": pending_ev.get('close_match_key', ''),
            "close_cause": "manual_close",
            "final_close_cause": "manual_close",
            "is_strategy_stop": False,
            "close_evidence": {},
            "manual_correction_reason": reason or "tg_user_review",
            "sl_algo_id": pending_ev.get('sl_algo_id', ''),
            "sl_algo_clord_id": pending_ev.get('sl_algo_clord_id', ''),
            "tp_algo_id": pending_ev.get('tp_algo_id', ''),
            "tp_algo_clord_id": pending_ev.get('tp_algo_clord_id', ''),
            "entry_attribution": pending_ev.get('entry_attribution', {}),
        }

        try:
            correction = self._ledger.apply_pnl_resolution(resolution)
        except Exception as e:
            await self._send_message(f"❌ apply_pnl_resolution 失败: {e}")
            return

        if correction:
            sym = pending_ev.get('symbol', '?')
            new_eid = (correction.get('event_id', '') or '')[:8]
            old_eid = pending_ev.get('event_id', '')[:8]
            await self._send_message(
                f"✅ PnL correction 已写入\n"
                f"symbol: {sym}\n"
                f"net_pnl: {net_pnl:+.4f} USDT\n"
                f"supersedes: {old_eid}\n"
                f"new event: {new_eid}"
            )
        else:
            await self._send_message(
                f"⚠️ apply_pnl_resolution 返回 None(可能已 superseded);未写新 correction"
            )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest -q test_tg_pnl_correction.py::TestResolvePendingHelper -v`
Expected: 4 PASS

- [ ] **Step 5: 提交**

```bash
git add agents/trading/telegram_notifier.py test_tg_pnl_correction.py
git commit -m "[TG-OPS][TG-003] add _resolve_pending_for_pnl_correction + _apply_pnl_correction

共用 helper 接受 filter_fn,返回 {status, candidates, error_msg}。
_apply_pnl_correction 构造 resolution 调 ledger.apply_pnl_resolution
写 source='manual_tg_review' 的 correction event。"
```

---

### Task 10: TG `/pnl <SYMBOL> <NET_PNL> [reason]` 命令

**Files:**
- Modify: `agents/trading/telegram_notifier.py:_handle_command` (handlers 字典 + handlers_with_args + `_cmd_pnl`)
- Test: `test_tg_pnl_correction.py` (扩展)

- [ ] **Step 1: 写失败测试**

```python
class TestCmdPnl:
    @pytest.mark.asyncio
    async def test_pnl_one_candidate_writes_correction(self):
        n = _make_notifier_with_ledger([
            {
                "event_id": "e1", "symbol": "XLM-USDT-SWAP", "side": "long",
                "position_id": "pos-1", "entry_request_id": "req-1",
                "estimated_pnl": -0.5, "close_match_key": "K1",
                "pnl_status": "pending",
            }
        ])
        n._ledger.apply_pnl_resolution.return_value = {
            "event_id": "corr-1", "supersedes_event_id": "e1"
        }
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_pnl(["XLM", "0.42"])

        n._ledger.apply_pnl_resolution.assert_called_once()
        resolution = n._ledger.apply_pnl_resolution.call_args[0][0]
        assert resolution["realized_pnl_net_usdt"] == 0.42
        assert resolution["pnl_source"] == "manual_tg_review"
        text = "\n".join(sent)
        assert "0.42" in text or "+0.4200" in text

    @pytest.mark.asyncio
    async def test_pnl_zero_candidate_rejects(self):
        n = _make_notifier_with_ledger([])
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_pnl(["XLM", "0.42"])

        n._ledger.apply_pnl_resolution.assert_not_called()
        text = "\n".join(sent)
        assert "未找到" in text or "not_found" in text.lower()

    @pytest.mark.asyncio
    async def test_pnl_multiple_candidate_lists_event_ids(self):
        n = _make_notifier_with_ledger([
            {"event_id": "abcdef12", "symbol": "XLM-USDT-SWAP"},
            {"event_id": "fedcba98", "symbol": "XLM-USDT-SWAP"},
        ])
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_pnl(["XLM", "0.42"])

        n._ledger.apply_pnl_resolution.assert_not_called()
        text = "\n".join(sent)
        assert "/pnl_id" in text
        # 提示候选 event_id (前 8 位)
        assert "abcdef12" in text or "fedcba98" in text

    @pytest.mark.asyncio
    async def test_pnl_invalid_net_pnl_rejects(self):
        n = _make_notifier_with_ledger([])
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_pnl(["XLM", "abc"])

        n._ledger.find_pending_external_closes.assert_not_called()
        text = "\n".join(sent)
        assert "用法" in text or "usage" in text.lower()

    @pytest.mark.asyncio
    async def test_pnl_missing_args_rejects(self):
        n = _make_notifier_with_ledger([])
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_pnl(["XLM"])

        n._ledger.find_pending_external_closes.assert_not_called()
        text = "\n".join(sent)
        assert "用法" in text


class TestCmdPnlReason:
    @pytest.mark.asyncio
    async def test_pnl_with_reason_writes_field(self):
        n = _make_notifier_with_ledger([
            {"event_id": "e1", "symbol": "XLM-USDT-SWAP", "pnl_status": "pending"}
        ])
        n._ledger.apply_pnl_resolution.return_value = {"event_id": "c1"}
        sent = []
        n._send_message = AsyncMock()

        await n._cmd_pnl(["XLM", "0.42", "OKX", "bills", "late"])

        resolution = n._ledger.apply_pnl_resolution.call_args[0][0]
        assert "OKX bills late" in resolution["manual_correction_reason"]

    @pytest.mark.asyncio
    async def test_pnl_without_reason_uses_default(self):
        n = _make_notifier_with_ledger([
            {"event_id": "e1", "symbol": "XLM-USDT-SWAP", "pnl_status": "pending"}
        ])
        n._ledger.apply_pnl_resolution.return_value = {"event_id": "c1"}
        n._send_message = AsyncMock()

        await n._cmd_pnl(["XLM", "0.42"])

        resolution = n._ledger.apply_pnl_resolution.call_args[0][0]
        assert resolution["manual_correction_reason"]  # 非空(默认值)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_tg_pnl_correction.py::TestCmdPnl test_tg_pnl_correction.py::TestCmdPnlReason -v`
Expected: 7 FAIL

- [ ] **Step 3: 实现 _cmd_pnl + 注册到 handlers**

```python
    async def _cmd_pnl(self, args: list):
        """F-TG-003: /pnl <SYMBOL> <NET_PNL> [reason] 写 manual PnL correction。"""
        if len(args) < 2:
            await self._send_message(
                "用法: /pnl <SYMBOL> <NET_PNL_USDT> [reason]"
            )
            return

        raw_sym = args[0].strip().upper()
        try:
            net_pnl = float(args[1])
        except ValueError:
            await self._send_message(
                "用法: /pnl <SYMBOL> <NET_PNL_USDT> [reason]\n"
                "NET_PNL 必须是数字"
            )
            return

        reason = " ".join(args[2:]) if len(args) > 2 else ""

        # 归一化:容忍带后缀
        if raw_sym.endswith('-SWAP'):
            symbol = raw_sym
        elif raw_sym.endswith('-USDT'):
            symbol = f"{raw_sym}-SWAP"
        else:
            symbol = f"{raw_sym}-USDT-SWAP"

        result = self._resolve_pending_for_pnl_correction(
            filter_fn=lambda ev: ev.get('symbol') == symbol,
            label=f"symbol={symbol}",
        )

        if result["status"] == "ok":
            await self._apply_pnl_correction(
                result["candidates"][0], net_pnl, reason
            )
        elif result["status"] == "multiple":
            eids = [(ev.get('event_id', '') or '')[:8] for ev in result["candidates"]]
            await self._send_message(
                f"⚠️ {result['error_msg']}\n"
                f"候选 event_id: {eids}\n"
                f"用 /pnl_id <event_id> <NET_PNL> [reason] 指定具体哪一条"
            )
        else:
            await self._send_message(f"❌ {result['error_msg']}")
```

`_handle_command` handlers 字典加 `'/pnl'` 与 handlers_with_args 加 `'/pnl'`：

```python
        handlers = {
            ...
            '/halts': self._cmd_halts,
            '/resume_symbol': self._cmd_resume_symbol,
            '/pnl': self._cmd_pnl,                            # F-TG-003
        }
        handlers_with_args = {'/resume_symbol', '/pnl'}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest -q test_tg_pnl_correction.py::TestCmdPnl test_tg_pnl_correction.py::TestCmdPnlReason -v`
Expected: 7 PASS

- [ ] **Step 5: 提交**

```bash
git add agents/trading/telegram_notifier.py test_tg_pnl_correction.py
git commit -m "[TG-OPS][TG-003] /pnl <SYMBOL> <NET_PNL> [reason] 命令

按 symbol 找未 supersede pending external_close。恰好 1 候选时
写 source='manual_tg_review' correction;0/多候选 fail-fast。
归一化兼容 XLM / XLM-USDT / XLM-USDT-SWAP。reason 写入
manual_correction_reason 字段。"
```

---

### Task 11: TG `/pnl_id <event_id> <NET_PNL> [reason]` 命令

**Files:**
- Modify: `agents/trading/telegram_notifier.py:_handle_command` (handlers + `_cmd_pnl_id`)
- Test: `test_tg_pnl_correction.py` (扩展)

- [ ] **Step 1: 写失败测试**

```python
class TestCmdPnlId:
    @pytest.mark.asyncio
    async def test_pnl_id_exact_match_writes_correction(self):
        n = _make_notifier_with_ledger([
            {"event_id": "abc-123", "symbol": "XLM-USDT-SWAP",
             "pnl_status": "pending", "side": "long",
             "position_id": "pos-1"},
            {"event_id": "def-456", "symbol": "XLM-USDT-SWAP"},
        ])
        n._ledger.apply_pnl_resolution.return_value = {
            "event_id": "corr-1", "supersedes_event_id": "abc-123"
        }
        sent = []
        n._send_message = AsyncMock()

        await n._cmd_pnl_id(["abc-123", "0.42"])

        n._ledger.apply_pnl_resolution.assert_called_once()
        resolution = n._ledger.apply_pnl_resolution.call_args[0][0]
        assert resolution["position_id"] == "pos-1"  # 来自 abc-123,不是 def-456

    @pytest.mark.asyncio
    async def test_pnl_id_not_found_rejects(self):
        n = _make_notifier_with_ledger([
            {"event_id": "abc-123", "symbol": "XLM-USDT-SWAP"}
        ])
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_pnl_id(["zzz-999", "0.42"])

        n._ledger.apply_pnl_resolution.assert_not_called()
        text = "\n".join(sent)
        assert "zzz-999" in text or "未找到" in text

    @pytest.mark.asyncio
    async def test_pnl_id_invalid_net_pnl_rejects(self):
        n = _make_notifier_with_ledger([])
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_pnl_id(["abc-123", "abc"])

        n._ledger.find_pending_external_closes.assert_not_called()
        text = "\n".join(sent)
        assert "用法" in text

    @pytest.mark.asyncio
    async def test_pnl_id_missing_args_rejects(self):
        n = _make_notifier_with_ledger([])
        sent = []
        async def fake_send(text):
            sent.append(text)
        n._send_message = fake_send

        await n._cmd_pnl_id(["abc-123"])

        n._ledger.find_pending_external_closes.assert_not_called()
        text = "\n".join(sent)
        assert "用法" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_tg_pnl_correction.py::TestCmdPnlId -v`
Expected: 4 FAIL

- [ ] **Step 3: 实现 _cmd_pnl_id + 注册**

```python
    async def _cmd_pnl_id(self, args: list):
        """F-TG-003: /pnl_id <event_id> <NET_PNL> [reason] 按 event_id 精确匹配。"""
        if len(args) < 2:
            await self._send_message(
                "用法: /pnl_id <event_id> <NET_PNL_USDT> [reason]"
            )
            return

        event_id = args[0]
        try:
            net_pnl = float(args[1])
        except ValueError:
            await self._send_message(
                "用法: /pnl_id <event_id> <NET_PNL_USDT> [reason]\n"
                "NET_PNL 必须是数字"
            )
            return

        reason = " ".join(args[2:]) if len(args) > 2 else ""

        result = self._resolve_pending_for_pnl_correction(
            filter_fn=lambda ev: ev.get('event_id') == event_id,
            label=f"event_id={event_id}",
        )

        if result["status"] == "ok":
            await self._apply_pnl_correction(
                result["candidates"][0], net_pnl, reason
            )
        else:
            # event_id 唯一,不可能 multiple
            await self._send_message(f"❌ {result['error_msg']}")
```

handlers 字典与 handlers_with_args 同步加：

```python
        handlers = {
            ...
            '/pnl': self._cmd_pnl,
            '/pnl_id': self._cmd_pnl_id,  # F-TG-003
        }
        handlers_with_args = {'/resume_symbol', '/pnl', '/pnl_id'}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest -q test_tg_pnl_correction.py::TestCmdPnlId -v`
Expected: 4 PASS

- [ ] **Step 5: 跑全 test_tg_pnl_correction.py 确认完整覆盖**

Run: `python3 -m pytest -q test_tg_pnl_correction.py -v`
Expected: 全部 PASS（累计 ~15 case）

- [ ] **Step 6: 提交**

```bash
git add agents/trading/telegram_notifier.py test_tg_pnl_correction.py
git commit -m "[TG-OPS][TG-003] /pnl_id <event_id> <NET_PNL> [reason] 命令

按 event_id 精确匹配 pending,作为 /pnl 多候选场景的回退。
共用 _resolve_pending_for_pnl_correction helper(filter_fn 不同)。"
```

---

## 全量回归与验证收尾

### Task 12: 字节码编译 + 默认全量回归

- [ ] **Step 1: 字节码扫描**

Run: `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_tg_pycache python3 -m compileall -q .`
Expected: 静默退出,无 SyntaxError

- [ ] **Step 2: 默认全量回归**

Run: `python3 -m pytest -q`
Expected: ≥ 895 passed / 4 deselected / 1 warning（基线 860 + 至少 35 新 case）

- [ ] **Step 3: 失败时定位修复**

如有回归，按错误信息定位修复并提交 `[TG-fix] ...` commit。

---

### Task 13: network 分层回归

- [ ] **Step 1: 跑 network 标签测试**

Run: `python3 -m pytest -q -m network`
Expected: 4 PASS

---

### Task 14: 人工 Mock TG run（验收证据）

- [ ] **Step 1: 起 mock TG 验证（人工或脚本）**

新建 `scripts/mock_tg_run.py`（如尚无），跑一遍命令链：

```bash
# 准备 mock 状态文件
cat > /tmp/agent_health.json << EOF
{
  "ts": $(date +%s),
  "agents_registered": 17,
  "tasks_alive": 17,
  "tasks_failed": 0,
  "halted_symbols": {"TEST-USDT-SWAP": {"reason": "manual_test", "halted_at": $(date +%s)}},
  "bus_dlq_size": 0
}
EOF

# 通过 Telegram 实际发送以下命令并截图保存:
# /halts          → 应显示 1 个 halt (TEST-USDT-SWAP)
# /resume_symbol TEST   → 应回 ✅ 已解除
# /halts          → 应回 "无 per-symbol halt"
# /status         → 应含 Agents / Bus DLQ / Per-symbol halt 三行
# /pnl XLM 0.42   → 当前无 pending → 应回 "未找到 XLM 的活跃 pending"
```

- [ ] **Step 2: 把 mock TG run 输出截图/日志保存**

到 `docs/generated_reports/tg_graceful_ops_mock_run_<timestamp>.md`，含每条命令的 input + output。

---

### Task 15: 撰写验收报告

**Files:**
- Create: `docs/audit_remediation_tg_graceful_ops_acceptance.md`

- [ ] **Step 1: 写验收报告**

```markdown
# TG Graceful Ops 整改验收报告 (2026-05-30)

## 范围

- F-TG-001: /resume + /force_resume 同步清 root executor _halted_symbols
- F-TG-002: /halts /resume_symbol /status per-symbol halt 行
- F-TG-003: /pnl /pnl_id 手动 PnL correction
- F-TG-004: /status agent health 轻量(Orchestrator 写 health.json)

## 验收命令

[列出 Task 12-14 的所有验收命令与预期]

## 验收结果

### F-TG-001
- AC-1.x 全部通过

### F-TG-002
- AC-2.x ...

### F-TG-003
- AC-3.x ...

### F-TG-004
- AC-4.x ...

## Mock TG run 摘要

[Task 14 截图/日志 链接]

## Go/No-Go

| 范围 | 第四次整改后 |
|---|---|
| 本地开发 | GO |
| paper/mock | GO |
| 小额 live 灰度 | GO |
| live 扩容 | GO（运维 SOP 含 BOT_INSTANCE_ID + TG 命令清单） |

## 附件

- 全量 pytest 输出: [日志摘要]
- mock TG run: docs/generated_reports/tg_graceful_ops_mock_run_<timestamp>.md
```

- [ ] **Step 2: 提交**

```bash
git add docs/audit_remediation_tg_graceful_ops_acceptance.md
git commit -m "[TG-acceptance] TG Graceful Ops 整改验收报告

闭环 F-TG-001/002/003/004,基线 860 → +35 case。
解决 5/30 XLM symbol-halt 残留 bug;落地 /halts /resume_symbol
/pnl /pnl_id 命令;/status 增强 agent health 行。"
```

---

### Task 16: 文档同步

**Files:**
- Modify: `CLAUDE.md` (当前事实段)
- Modify: `docs/to-do-list.md` (行 58 / 行 64 移已关闭)
- Modify: `openspec/changes/tg-graceful-ops/tasks.md` (全部勾选)

- [ ] **Step 1: 更新 CLAUDE.md 当前事实**

在现有 "当前事实" 段追加：

```markdown
- 2026-05-30 TG Graceful Ops 整改后基线：`<新数字> passed / 4 deselected / 1 warning`（净增 ~35 case：`test_tg_symbol_halt_control.py` ~26 + `test_tg_pnl_correction.py` ~15 + `test_tg_status_enhancement.py` ~14）。F-TG-001：`/resume` + `/force_resume` 同步清 root executor `_halted_symbols`(force_resume 走 audit warning + risk_alert);F-TG-002：新增 `/halts` 列锁的 symbol、`/resume_symbol` 通过 bus 解单 symbol;`/status` 增加 Per-symbol halt 行;F-TG-003：新增 `/pnl <SYMBOL> <NET_PNL>` 与 `/pnl_id <event_id> <NET_PNL>` 手动 PnL correction(共用 helper);F-TG-004：Orchestrator 写 `data/<ns_>agent_health.json`，TG `/status` 读它输出 Agents/Bus DLQ/Per-symbol halt 三行。详见 `docs/audit_remediation_tg_graceful_ops_acceptance.md`。
```

- [ ] **Step 2: 更新 docs/to-do-list.md**

把行 58 `/pnl` 与 行 64 `/status` agent health 从 OPEN 移到 "已关闭"，附 commit / 验收报告链接。

- [ ] **Step 3: 勾选 OpenSpec tasks.md**

把 `openspec/changes/tg-graceful-ops/tasks.md` 中本次落地的任务从 `- [ ]` 改为 `- [x]`：1.1-1.8 / 2.1-2.9 / 3.1-3.8 / 4.1-4.8 / 5.1-5.8 全部勾选。

- [ ] **Step 4: 提交**

```bash
git add CLAUDE.md docs/to-do-list.md openspec/changes/tg-graceful-ops/tasks.md
git commit -m "[TG-docs] 同步 CLAUDE.md / to-do-list / OpenSpec tasks

TG Graceful Ops 整改闭环,基线 860 → +35 case。
to-do-list 行 58 /pnl + 行 64 /status agent health 标已闭环。"
```

---

## 自检清单

- **Spec coverage**：
  - tg-symbol-halt-control 5 个 requirement → Task 1-2 + 6-7
  - tg-pnl-correction 5 个 requirement → Task 9-11
  - tg-status-enhancement 5 个 requirement → Task 3-5 + 8
  - 无遗漏

- **类型一致性**：
  - `clear_symbol_halt(symbol=None) -> int` 在 Task 1 定义后被 Task 2 / 7 全部按同名引用
  - `_halted_symbols / get_halted_symbols / _resolve_pending_for_pnl_correction / _apply_pnl_correction / _format_elapsed / _read_agent_health` 名称跨 Task 一致
  - 新增 risk_alert types 三种(symbol_halt_cleared / symbol_halt_not_found / force_resume_cleared_symbol_halts)在 Task 2 / 7 一致使用

- **占位符扫描**：无 TBD/TODO；每个 step 都有具体代码或命令

- **验证命令**：每个 Task 末尾都有 `pytest` 命令与预期输出




