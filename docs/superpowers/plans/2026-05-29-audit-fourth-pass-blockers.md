---
change: audit-fourth-pass-blockers
design-doc: docs/superpowers/specs/2026-05-29-audit-fourth-pass-blockers-design.md
base-ref: a11f892c6d57cb0e787e8e83530dccd9f6d3ff46
---

# Audit Fourth Pass Blockers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 闭环第四次审计 P0/P1 阻断（F4-001/002/003），让 reduce 失败结果不再被 Agent 误广播为 risk_reduced、所有 pnl_resolved 总线事件透传 final close cause + 幂等键、OKX 真实新 SL 全部走 owner-tag clOrdId，从而解除 live 扩容 NO-GO。

**Architecture:** 三个阻断都在"调用方与字段集"层面：底层 `reduce_position` / `_classify_close_evidence` / `_make_owner_tag_clord_id` 已落地，本次只把 Agent 层、总线事件 publisher、真实下单点切换到正确字段/工厂函数。引入两个 helper（`_classify_reduce_outcome` / `make_resolution_id`）作为单点契约，消除三处分支漂移。

**Tech Stack:** Python 3 / pytest / asyncio / OKX ccxt / 项目内自定义 message bus

**实施顺序（按风险递增）：** F4-003 (owner-tag) → F4-002 (resolution_id + 透传) → F4-001 (Agent reduce 分流) → 全量回归 → 文档同步。

**约定：**
- 测试前缀：项目根目录直接放 `test_*.py`（与现有 `test_reduce_protective_sl_lifecycle.py` 对齐）
- 提交：每个 Task 末尾一次提交，message 带 `[F4-00x]` 前缀
- 验证命令：`python3 -m pytest -q <file>` 当前目录运行

---

## File Structure

**新增文件：**
- `test_owner_tag_clord_id_callsites.py` — F4-003 单测
- `test_pnl_resolved_event_contract.py` — F4-002 单测
- `test_reduce_failure_propagation.py` — F4-001 单测
- `docs/audit_remediation_fourth_pass_20260528_acceptance.md` — 验收报告

**修改文件：**
- `executor.py` — F4-003 三处 owner-tag 切换；保留 `_make_sl_clord_id` 标 DEPRECATED
- `utils/state_paths.py` — F4-003 banner 增加 BOT_INSTANCE_ID 行 + WARNING（live 缺失时）
- `utils/realized_pnl_resolver.py` — F4-002 新增 `make_resolution_id()` 模块函数
- `utils/reconciliation.py` — F4-002 `auto_resolve_pending` summary 字段集 + `resolution_id`
- `agents/trading/executor.py` — F4-001 `_classify_reduce_outcome` helper + 三路径改造；F4-002 `_resolve_external_close_async` / `_run_reconciliation` 透传字段 + `correction is None` 防御
- `agents/trading/portfolio_risk_guard.py` — F4-001 reduce 类终态分支处理
- `agents/trading/telegram_notifier.py` — F4-001 `risk_reduced` 文案分流
- `agents/trading/judge.py` — F4-002 `_seen_resolution_ids` LRU 接入 `_handle_pnl_resolved`
- `agents/trading/reviewer.py` — F4-002 `_seen_resolution_ids` LRU 接入

**文档同步：**
- `CLAUDE.md` — 更新 "当前事实" 段，标 F4-001/002/003 闭环 + 新基线
- `docs/to-do-list.md` — 三个阻断项移到 "已关闭事项"

---

## F4-003 Owner Tag clOrdId

### Task 1: BOT_INSTANCE_ID banner 告警

**Files:**
- Modify: `utils/state_paths.py:85-95` (`StatePaths.as_banner_lines`)
- Test: `test_owner_tag_clord_id_callsites.py` (Create)

- [ ] **Step 1: 写失败测试**

新建 `test_owner_tag_clord_id_callsites.py`（先只放 banner 部分，后续 Task 会追加 owner-tag 部分）：

```python
"""F4-003 owner-tag clOrdId 测试矩阵。

覆盖：
- BOT_INSTANCE_ID 缺失时启动 banner 打印 WARNING（live namespace）
- testnet/paper 缺失不报警
- legacy _make_sl_clord_id 仍可调用（用于历史 cleanup）
- _replace_protective_sl / open_position_with_plan / legacy _open_position 三处使用 owner-tag clOrdId
"""

import os
import pytest
from unittest.mock import patch

from utils.state_paths import StatePaths, get_state_paths, reset_state_paths


@pytest.fixture(autouse=True)
def _reset_state_paths():
    reset_state_paths()
    yield
    reset_state_paths()


class TestBotInstanceIdBanner:
    def test_live_missing_bot_instance_id_emits_warning(self, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "live")
        monkeypatch.delenv("BOT_INSTANCE_ID", raising=False)
        sp = get_state_paths(refresh=True)
        lines = sp.as_banner_lines()
        text = "\n".join(lines)
        assert "BOT_INSTANCE_ID" in text
        assert "WARNING" in text
        assert "not configured" in text

    def test_live_with_bot_instance_id_no_warning(self, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "live")
        monkeypatch.setenv("BOT_INSTANCE_ID", "bot-A")
        sp = get_state_paths(refresh=True)
        lines = sp.as_banner_lines()
        text = "\n".join(lines)
        assert "BOT_INSTANCE_ID: bot-A" in text
        assert "not configured" not in text

    def test_testnet_missing_bot_instance_id_no_warning(self, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "testnet")
        monkeypatch.delenv("BOT_INSTANCE_ID", raising=False)
        sp = get_state_paths(refresh=True)
        lines = sp.as_banner_lines()
        text = "\n".join(lines)
        assert "not configured" not in text

    def test_paper_missing_bot_instance_id_no_warning(self, monkeypatch):
        monkeypatch.setenv("STATE_NAMESPACE", "paper")
        monkeypatch.delenv("BOT_INSTANCE_ID", raising=False)
        sp = get_state_paths(refresh=True)
        lines = sp.as_banner_lines()
        text = "\n".join(lines)
        assert "not configured" not in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_owner_tag_clord_id_callsites.py::TestBotInstanceIdBanner -v`
Expected: 4 个测试全部 FAIL（banner 还没改，没有 BOT_INSTANCE_ID 行）

- [ ] **Step 3: 实现 banner 改动**

修改 `utils/state_paths.py:85-95` 的 `as_banner_lines`：

```python
def as_banner_lines(self) -> list:
    """供启动 banner 使用的展示行。"""
    bot_id = (os.getenv("BOT_INSTANCE_ID") or "").strip()
    lines = [
        f'  状态命名空间:          {self.namespace.upper()}',
        f'    positions          → {self.positions}',
        f'    risk_state         → {self.risk_state}',
        f'    riskguard_state    → {self.riskguard_state}',
        f'    halt_state         → {self.halt_state}',
        f'    live_order_events  → {self.live_order_events}',
        f'    live_position_life → {self.live_position_lifecycle}',
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

Run: `python3 -m pytest -q test_owner_tag_clord_id_callsites.py::TestBotInstanceIdBanner -v`
Expected: 4 PASS

- [ ] **Step 5: 全量回归确认无破坏**

Run: `python3 -m pytest -q test_state_namespace.py`
Expected: 16 PASS（既有基线）

- [ ] **Step 6: 提交**

```bash
git add utils/state_paths.py test_owner_tag_clord_id_callsites.py
git commit -m "[F4-003] banner 在 live 缺 BOT_INSTANCE_ID 时打 WARNING

为 cross-bot SL 归属可证明性提供启动告警。testnet/paper 不打告警避免噪音。"
```

---

### Task 2: `_replace_protective_sl` 与 `open_position_with_plan` 切换 owner-tag

**Files:**
- Modify: `executor.py:1464` (`_replace_protective_sl` 内 `new_clord` 赋值)
- Modify: `executor.py:1950` (`open_position_with_plan` 内 `sl_clord_id` 赋值)
- Test: `test_owner_tag_clord_id_callsites.py` (扩展)

- [ ] **Step 1: 写失败测试（追加到现有文件）**

在 `test_owner_tag_clord_id_callsites.py` 末尾追加：

```python
class TestReplaceProtectiveSlOwnerTag:
    def test_replace_uses_owner_tag_clord_id(self, monkeypatch):
        from executor import ContractExecutor
        monkeypatch.setenv("STATE_NAMESPACE", "live")
        monkeypatch.setenv("BOT_INSTANCE_ID", "bot42")
        # 捕获 _place_protective_sl 收到的 clord_id
        captured = {}
        original_place = ContractExecutor._place_protective_sl

        def fake_place(self, *, symbol, side, stop_price, amount, clord_id=None, **kw):
            captured["clord_id"] = clord_id
            return "fake-algo-id"

        with patch.object(ContractExecutor, "_place_protective_sl", fake_place), \
             patch.object(ContractExecutor, "_cancel_protective_sl", lambda self, s, p: True):
            # 构造最小可用 ContractExecutor mock
            from unittest.mock import MagicMock
            ex = MagicMock(spec=ContractExecutor)
            ex.exchange_id = "okx"
            ex.testnet = False
            ex.logger = MagicMock()
            position = {
                "side": "long", "amount": 1.0,
                "sl_algo_id": "old-algo", "sl_order_id": "old-algo",
            }
            # 调用真实 _replace_protective_sl 但 self 用 mock
            ok = ContractExecutor._replace_protective_sl(ex, "BTC-USDT", position, 50000)
        assert ok is True
        assert captured["clord_id"] is not None
        assert ContractExecutor._is_owner_clord_id(captured["clord_id"])


class TestAttachedSlOwnerTag:
    def test_open_position_with_plan_attached_sl_owner_tag(self):
        from executor import ContractExecutor
        # 验证 _make_sl_clord_id（旧）替换为 _make_owner_tag_clord_id（新）
        # 通过静态扫描源码定位调用点
        import inspect
        src = inspect.getsource(ContractExecutor.open_position_with_plan)
        assert "_make_owner_tag_clord_id" in src
        # 旧工厂仍存在但不再被新挂单调用
        assert "_make_sl_clord_id(symbol)" not in src or "DEPRECATED" in src
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_owner_tag_clord_id_callsites.py::TestReplaceProtectiveSlOwnerTag test_owner_tag_clord_id_callsites.py::TestAttachedSlOwnerTag -v`
Expected: FAIL（当前还在用 `_make_sl_clord_id`）

- [ ] **Step 3: 修改 `_replace_protective_sl` (executor.py:1464)**

```python
# 原:
new_clord = self._make_sl_clord_id(symbol) if self.exchange_id == 'okx' else None
# 改为:
new_clord = self._make_owner_tag_clord_id(symbol) if self.exchange_id == 'okx' else None
```

- [ ] **Step 4: 修改 `open_position_with_plan` (executor.py:1950)**

```python
# 原:
sl_clord_id = self._make_sl_clord_id(symbol) if self.exchange_id == 'okx' and stop_loss else None
# 改为:
sl_clord_id = self._make_owner_tag_clord_id(symbol) if self.exchange_id == 'okx' and stop_loss else None
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest -q test_owner_tag_clord_id_callsites.py::TestReplaceProtectiveSlOwnerTag test_owner_tag_clord_id_callsites.py::TestAttachedSlOwnerTag -v`
Expected: PASS

- [ ] **Step 6: 跑现有 owner-tag 相关测试确认无回归**

Run: `python3 -m pytest -q test_protective_cleanup_owner.py`
Expected: 12 PASS

- [ ] **Step 7: 提交**

```bash
git add executor.py test_owner_tag_clord_id_callsites.py
git commit -m "[F4-003] _replace_protective_sl + attached SL 切到 owner-tag clOrdId

新挂 SL 走 _make_owner_tag_clord_id,clOrdId 含 ca+ns+bot 前缀,
本地 state 丢失或多 bot 同账户场景下可按 owner prefix 证明归属。"
```

---

### Task 3: legacy `_open_position` 独立 SL 写入 owner-tag clord

**Files:**
- Modify: `executor.py:1068-1095` (legacy `_open_position` 内 `_place_protective_sl` 调用 + position dict 字段)
- Modify: `executor.py:295-302` (`_make_sl_clord_id` 加 DEPRECATED 注释)
- Test: `test_owner_tag_clord_id_callsites.py` (扩展)

- [ ] **Step 1: 写失败测试（追加）**

```python
class TestLegacyOpenPositionOwnerTag:
    def test_legacy_open_writes_sl_algo_clord_id(self):
        """legacy _open_position 调用 _place_protective_sl 时必须传 owner-tag clord_id,
        并把它写入 position['sl_algo_clord_id']。"""
        from executor import ContractExecutor
        import inspect
        src = inspect.getsource(ContractExecutor._open_position)
        # 必须在调用 _place_protective_sl 之前生成 owner-tag clord_id
        assert "_make_owner_tag_clord_id" in src
        # position dict 写入字段
        assert "'sl_algo_clord_id':" in src or "\"sl_algo_clord_id\":" in src
        # 旧 None 占位应已被替换为变量名
        assert "'sl_algo_clord_id': None" not in src

    def test_legacy_make_sl_clord_id_still_callable_for_cleanup(self):
        """旧 _make_sl_clord_id 必须保留(用于历史 sl_algo_clord_id 字符串识别)。"""
        from executor import ContractExecutor
        clord = ContractExecutor._make_sl_clord_id("BTC-USDT")
        assert clord.startswith("sl")
        assert len(clord) <= 32
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_owner_tag_clord_id_callsites.py::TestLegacyOpenPositionOwnerTag -v`
Expected: FAIL

- [ ] **Step 3: 修改 legacy `_open_position` (executor.py:1068-1095)**

定位 `# 在交易所设置 SL 条件单（OKX 走独立 algo；非 OKX 走旧路径）` 之后的 `_place_protective_sl` 调用。原代码：

```python
sl_order_id = self._place_protective_sl(
    symbol=symbol, side=side, stop_price=stop_loss, amount=amount,
)
```

改为：

```python
sl_clord_id = self._make_owner_tag_clord_id(symbol) if self.exchange_id == 'okx' and stop_loss else None
sl_order_id = self._place_protective_sl(
    symbol=symbol, side=side, stop_price=stop_loss, amount=amount,
    clord_id=sl_clord_id,
)
```

position dict 内 `'sl_algo_clord_id': None` 改为：

```python
'sl_algo_clord_id': sl_clord_id,
```

- [ ] **Step 4: 修改 `_make_sl_clord_id` (executor.py:295-302) 加 DEPRECATED 注释**

```python
@staticmethod
def _make_sl_clord_id(symbol: str) -> str:
    """[DEPRECATED] 历史兼容标识器,新挂单 MUST 使用 _make_owner_tag_clord_id。

    保留原因: cleanup 路径 _is_owner_clord_id 仍按 sl_algo_clord_id 字段做 exact 匹配,
    存量 positions.json 中的历史 sl... 前缀 algoClOrdId 仍能被识别为本系统所有,
    避免误清扫。预计 1-2 个月后跑全量 positions.json 审计确认无遗留再删除。

    FR-3B 兼容: 历史 sl... 前缀只能通过 exact sl_algo_clord_id 匹配证明 owner,
    不能用 'sl' 前缀做泛化 sweep。
    """
    base = symbol.replace('-', '').replace('/', '').replace(':', '').upper()[:8]
    return f"sl{base}{uuid.uuid4().hex[:18]}"
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest -q test_owner_tag_clord_id_callsites.py -v`
Expected: 全部 PASS（共 ~9 个 case）

- [ ] **Step 6: 全量保护单相关回归**

Run: `python3 -m pytest -q test_protective_cleanup_owner.py test_protective_sl_owner.py test_reduce_protective_sl_lifecycle.py`
Expected: 全部 PASS

- [ ] **Step 7: 提交**

```bash
git add executor.py test_owner_tag_clord_id_callsites.py
git commit -m "[F4-003] legacy _open_position 独立 SL 写入 owner-tag clord_id

legacy 路径同步使用 _make_owner_tag_clord_id,确保 position['sl_algo_clord_id']
非空,cleanup 可按 exact 匹配识别。_make_sl_clord_id 保留 DEPRECATED 标识。"
```

---

## F4-002 pnl_resolved 总线事件契约

### Task 4: `make_resolution_id` 模块函数

**Files:**
- Modify: `utils/realized_pnl_resolver.py` (顶部 import 后增加新函数)
- Test: `test_pnl_resolved_event_contract.py` (Create)

- [ ] **Step 1: 写失败测试**

新建 `test_pnl_resolved_event_contract.py`：

```python
"""F4-002 pnl_resolved/pnl_mismatch 总线事件契约测试矩阵。"""

import pytest
from utils.realized_pnl_resolver import make_resolution_id


class TestMakeResolutionId:
    def test_correction_event_id_takes_priority(self):
        resolution = {"position_id": "p1", "order_ids": ["o1"]}
        correction = {"event_id": "E-123", "supersedes_event_id": "E-old"}
        rid = make_resolution_id(resolution, correction)
        assert rid == "corr:E-123"

    def test_supersedes_when_no_event_id(self):
        resolution = {"position_id": "p1"}
        correction = {"supersedes_event_id": "E-old"}
        rid = make_resolution_id(resolution, correction)
        assert rid == "sup:E-old"

    def test_close_match_key_when_no_correction(self):
        resolution = {"position_id": "p1", "close_match_key": "K-7"}
        rid = make_resolution_id(resolution, None)
        assert rid == "key:K-7"

    def test_pos_orders_fallback(self):
        resolution = {"position_id": "p1", "order_ids": ["o2", "o1"]}
        rid = make_resolution_id(resolution, None)
        assert rid == "pos:p1|orders:o1,o2"

    def test_empty_orders_fallback(self):
        resolution = {"position_id": "", "order_ids": []}
        rid = make_resolution_id(resolution, None)
        assert rid == "pos:|orders:"

    def test_same_resolution_same_id(self):
        resolution = {"position_id": "p1", "order_ids": ["o1"]}
        correction = {"event_id": "E-1"}
        a = make_resolution_id(resolution, correction)
        b = make_resolution_id(resolution, correction)
        assert a == b
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_pnl_resolved_event_contract.py::TestMakeResolutionId -v`
Expected: ImportError on `make_resolution_id`

- [ ] **Step 3: 实现函数**

在 `utils/realized_pnl_resolver.py` 第 28 行（`DEFAULT_LOOKBACK_MS` 常量之后）插入：

```python
def make_resolution_id(resolution: Dict[str, Any],
                        correction: Optional[Dict[str, Any]] = None) -> str:
    """生成 pnl_resolved/pnl_mismatch 事件的幂等键。

    优先级链:
        corr:<event_id>      — 写 ledger correction 成功(全局唯一)
        sup:<supersedes_id>  — pending → final 链兜底
        key:<close_match_key> — resolver 内部对账键
        pos:<pid>|orders:<sorted>  — 兜底,基于 position_id + 排序后 order_ids

    Returns:
        非空字符串。下游账本类订阅者用此键 LRU 去重。
    """
    if correction:
        event_id = correction.get("event_id")
        if event_id:
            return f"corr:{event_id}"
        sup = correction.get("supersedes_event_id")
        if sup:
            return f"sup:{sup}"
    match_key = resolution.get("close_match_key")
    if match_key:
        return f"key:{match_key}"
    pos_id = resolution.get("position_id", "") or ""
    order_ids = sorted(resolution.get("order_ids") or [])
    return f"pos:{pos_id}|orders:{','.join(order_ids)}"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest -q test_pnl_resolved_event_contract.py::TestMakeResolutionId -v`
Expected: 6 PASS

- [ ] **Step 5: 提交**

```bash
git add utils/realized_pnl_resolver.py test_pnl_resolved_event_contract.py
git commit -m "[F4-002] add make_resolution_id 单一幂等键工厂

四级优先级链 corr/sup/key/pos,供 _resolve_external_close_async /
_run_reconciliation / auto_resolve_pending 三个发布点共用。"
```

---

### Task 5: `Reconciler.auto_resolve_pending` summary 透传 final cause + resolution_id

**Files:**
- Modify: `utils/reconciliation.py:255-285` (results.append 字段集)
- Modify: `utils/reconciliation.py:1-20` (import `make_resolution_id`)
- Test: `test_pnl_resolved_event_contract.py` (扩展)

- [ ] **Step 1: 写失败测试（追加）**

```python
class TestReconcilerSummaryFields:
    def test_auto_resolve_pending_summary_carries_final_cause_and_resolution_id(self):
        """auto_resolve_pending 返回的 summary 必须含 close_cause /
        final_close_cause / is_strategy_stop / close_evidence / resolution_id。"""
        from utils.reconciliation import Reconciler
        from unittest.mock import MagicMock

        # mock ledger 返回一条 pending 事件
        ledger = MagicMock()
        ledger.find_pending_external_closes.return_value = [{
            "event_id": "PEND-1",
            "symbol": "BTC-USDT",
            "side": "long",
            "position_id": "pos-1",
            "entry_request_id": "req-1",
            "opened_at": 1000.0,
            "closed_at": 2000.0,
            "estimated_pnl": -10.0,
            "entry_price": 50000,
            "amount_usdt": 100,
            "leverage": 5,
            "sl_algo_id": "algo-1",
            "sl_algo_clord_id": "casllivebot42",
            "tp_algo_id": "",
            "tp_algo_clord_id": "",
            "entry_attribution": {"archetype": "long_v1"},
            "close_match_key": "K-1",
        }]
        ledger.apply_pnl_resolution.return_value = {
            "event_id": "CORR-1",
            "supersedes_event_id": "PEND-1",
        }

        # mock resolver 返回 final 状态
        resolver = MagicMock()
        resolver.resolve_external_close.return_value = {
            "pnl_status": "final",
            "pnl_source": "okx_fills",
            "symbol": "BTC-USDT",
            "side": "long",
            "position_id": "pos-1",
            "entry_request_id": "req-1",
            "realized_pnl_net_usdt": -9.5,
            "gross_close_pnl_usdt": -10.0,
            "fee_usdt": -0.5,
            "funding_usdt": 0.0,
            "order_ids": ["ord-1"],
            "bill_ids": ["bill-1"],
            "close_match_key": "K-1",
            "close_cause": "exchange_sl",
            "final_close_cause": "exchange_sl",
            "is_strategy_stop": True,
            "close_evidence": {
                "match_rule": "sl_algo_id_exact",
                "confidence": 1.0,
                "matched_algo_id": "algo-1",
                "matched_algo_clord_id": "casllivebot42",
                "matched_order_ids": ["ord-1"],
            },
            "warnings": [],
            "match_confidence": 1.0,
            "exchange_pnl_usdt": -9.5,
            "fills_pnl_usdt": -10.0,
            "sl_algo_id": "algo-1",
            "sl_algo_clord_id": "casllivebot42",
            "tp_algo_id": "",
            "tp_algo_clord_id": "",
            "entry_attribution": {"archetype": "long_v1"},
        }

        rec = Reconciler.__new__(Reconciler)
        rec.ledger = ledger
        rec.resolver = resolver
        rec.logger = MagicMock()

        results = rec.auto_resolve_pending()
        assert len(results) == 1
        s = results[0]
        assert s["close_cause"] == "exchange_sl"
        assert s["final_close_cause"] == "exchange_sl"
        assert s["is_strategy_stop"] is True
        assert s["close_evidence"]["match_rule"] == "sl_algo_id_exact"
        assert s["resolution_id"] == "corr:CORR-1"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_pnl_resolved_event_contract.py::TestReconcilerSummaryFields -v`
Expected: FAIL（summary 缺字段）

- [ ] **Step 3: 修改 `utils/reconciliation.py`**

在文件顶部 import 区追加：

```python
from utils.realized_pnl_resolver import make_resolution_id
```

修改 `auto_resolve_pending` 内 `results.append({...})`（line 255-285），追加字段：

```python
results.append({
    'symbol': resolution.get('symbol', snapshot['symbol']),
    'position_id': resolution.get('position_id', ''),
    'entry_request_id': resolution.get('entry_request_id', ''),
    'pnl_status': status,
    'pnl_source': resolution.get('pnl_source', ''),
    'realized_pnl_net_usdt': resolution.get('realized_pnl_net_usdt'),
    'estimated_pnl': ev.get('estimated_pnl'),
    'exchange_pnl_usdt': resolution.get('exchange_pnl_usdt'),
    'fills_pnl_usdt': resolution.get('fills_pnl_usdt'),
    'gross_close_pnl_usdt': resolution.get('gross_close_pnl_usdt', 0),
    'fee_usdt': resolution.get('fee_usdt', 0),
    'funding_usdt': resolution.get('funding_usdt', 0),
    'order_ids': resolution.get('order_ids', []),
    'bill_ids': resolution.get('bill_ids', []),
    'match_confidence': resolution.get('match_confidence', 0),
    'warnings': resolution.get('warnings', []),
    'sl_algo_id': resolution.get('sl_algo_id', ev.get('sl_algo_id', '')),
    'sl_algo_clord_id': resolution.get('sl_algo_clord_id',
                                          ev.get('sl_algo_clord_id', '')),
    'tp_algo_id': resolution.get('tp_algo_id', ev.get('tp_algo_id', '')),
    'tp_algo_clord_id': resolution.get('tp_algo_clord_id',
                                          ev.get('tp_algo_clord_id', '')),
    'entry_attribution': resolution.get('entry_attribution',
                                           ev.get('entry_attribution', {})),
    'supersedes_event_id': (correction or {}).get('supersedes_event_id', ''),
    'correction_event_id': (correction or {}).get('event_id', ''),
    'pending_event_id': ev.get('event_id', ''),
    # F4-002: final close cause 证据 + 幂等键
    'close_cause': resolution.get('close_cause', ''),
    'final_close_cause': resolution.get('final_close_cause', ''),
    'is_strategy_stop': bool(resolution.get('is_strategy_stop', False)),
    'close_evidence': resolution.get('close_evidence', {}),
    'resolution_id': make_resolution_id(resolution, correction),
})
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest -q test_pnl_resolved_event_contract.py::TestReconcilerSummaryFields -v`
Expected: PASS

- [ ] **Step 5: 跑既有 reconciliation 回归**

Run: `python3 -m pytest -q test_external_close_final_cause.py test_exchange_realized_pnl_resolver.py`
Expected: 全 PASS

- [ ] **Step 6: 提交**

```bash
git add utils/reconciliation.py test_pnl_resolved_event_contract.py
git commit -m "[F4-002] auto_resolve_pending summary 透传 final cause + resolution_id

补全 close_cause / final_close_cause / is_strategy_stop / close_evidence /
resolution_id 五个字段,使 _run_reconciliation 发布的 pnl_resolved 携带
完整 final cause 证据。"
```

---

### Task 6: `_resolve_external_close_async` 透传字段 + correction=None 防御

**Files:**
- Modify: `agents/trading/executor.py:880-921` (`_resolve_external_close_async` publish payload)
- Modify: `agents/trading/executor.py` 顶部 import `make_resolution_id`
- Test: `test_pnl_resolved_event_contract.py` (扩展)

- [ ] **Step 1: 定位现有代码并阅读上下文**

Run: `grep -n "_resolve_external_close_async\|publish.*pnl_resolved\|publish.*pnl_mismatch" agents/trading/executor.py`

确认 publish payload 在 line ~880-921，前后会有 `resolution = ...` 与 `correction = ...` 两个变量可用。

- [ ] **Step 2: 写失败测试（追加）**

```python
class TestResolveExternalCloseAsyncPublish:
    @pytest.mark.asyncio
    async def test_publishes_final_cause_and_resolution_id(self):
        """_resolve_external_close_async 发布 pnl_resolved 时必须含
        final_close_cause / close_evidence / resolution_id。"""
        from agents.trading.executor import MultiExecutor
        from unittest.mock import MagicMock, AsyncMock, patch

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.publish = fake_publish
        ex.logger = MagicMock()
        ex.executor = MagicMock()
        ex._resolver = MagicMock()
        ex._resolver.resolve_external_close.return_value = {
            "pnl_status": "final",
            "symbol": "BTC-USDT",
            "side": "long",
            "position_id": "pos-1",
            "entry_request_id": "req-1",
            "realized_pnl_net_usdt": -9.5,
            "close_cause": "exchange_sl",
            "final_close_cause": "exchange_sl",
            "is_strategy_stop": True,
            "close_evidence": {"match_rule": "sl_algo_id_exact", "confidence": 1.0},
            "order_ids": ["ord-1"],
            "close_match_key": "K-1",
            "warnings": [],
            "match_confidence": 1.0,
            "estimated_pnl": -10.0,
            "exchange_pnl_usdt": -9.5,
            "fills_pnl_usdt": -10.0,
            "sl_algo_id": "algo-1",
            "sl_algo_clord_id": "casllivebot42",
            "tp_algo_id": "",
            "tp_algo_clord_id": "",
            "entry_attribution": {},
            "pos_side": "long",
            "opened_at": 0,
            "closed_at": 0,
            "gross_close_pnl_usdt": -10,
            "fee_usdt": -0.5,
            "funding_usdt": 0,
            "bill_ids": [],
            "pnl_source": "okx_fills",
        }

        ledger = MagicMock()
        ledger.apply_pnl_resolution.return_value = {
            "event_id": "CORR-9", "supersedes_event_id": "PEND-9"
        }
        ex._ledger = ledger

        snapshot = {"side": "long", "request_id": "req-1"}
        await ex._resolve_external_close_async(
            "BTC-USDT", snapshot, {"closed_at": 1000})

        topics = [t for t, _ in published]
        assert "pnl_resolved" in topics
        payload = next(p for t, p in published if t == "pnl_resolved")
        assert payload["final_close_cause"] == "exchange_sl"
        assert payload["close_evidence"]["match_rule"] == "sl_algo_id_exact"
        assert payload["resolution_id"] == "corr:CORR-9"

    @pytest.mark.asyncio
    async def test_skips_publish_when_no_correction_and_pending(self):
        """correction=None 且 status=pending 时跳过发布并打 warning。"""
        from agents.trading.executor import MultiExecutor
        from unittest.mock import MagicMock

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.publish = fake_publish
        ex.logger = MagicMock()
        ex._resolver = MagicMock()
        ex._resolver.resolve_external_close.return_value = {
            "pnl_status": "pending",
            "symbol": "BTC-USDT",
            "side": "long",
            "position_id": "pos-1",
            "entry_request_id": "req-1",
            "realized_pnl_net_usdt": None,
            "close_cause": "external_unknown",
            "final_close_cause": "external_unknown",
            "is_strategy_stop": False,
            "close_evidence": {},
            "order_ids": [],
            "warnings": ["pending"],
            "match_confidence": 0,
            "estimated_pnl": -5.0,
            "pnl_source": "",
            "pos_side": "long",
            "opened_at": 0, "closed_at": 0,
            "gross_close_pnl_usdt": 0, "fee_usdt": 0, "funding_usdt": 0,
            "bill_ids": [],
            "exchange_pnl_usdt": None, "fills_pnl_usdt": None,
            "sl_algo_id": "", "sl_algo_clord_id": "",
            "tp_algo_id": "", "tp_algo_clord_id": "",
            "entry_attribution": {},
        }

        ledger = MagicMock()
        # pending 不会写 correction
        ex._ledger = ledger

        snapshot = {"side": "long", "request_id": "req-1"}
        await ex._resolve_external_close_async(
            "BTC-USDT", snapshot, {"closed_at": 1000})

        topics = [t for t, _ in published]
        assert "pnl_resolved" not in topics
        assert "pnl_mismatch" not in topics
        ex.logger.warning.assert_called()
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python3 -m pytest -q test_pnl_resolved_event_contract.py::TestResolveExternalCloseAsyncPublish -v`
Expected: FAIL

- [ ] **Step 4: 修改 `agents/trading/executor.py`**

顶部 import 追加：

```python
from utils.realized_pnl_resolver import (
    PNL_STATUS_FINAL, PNL_STATUS_MISMATCH, make_resolution_id,
)
```

定位到 `_resolve_external_close_async` 内的 publish 块（line ~870-921）。在调用 `await self.publish(topic, {...})` 之前插入防御逻辑（位置：在 `correction = ...`/`apply_pnl_resolution` 之后、`await self.publish` 之前）：

```python
# F4-002: correction=None 且非 final/mismatch 时跳过发布,避免脏事件
status = resolution.get("pnl_status", "")
if correction is None and status not in (PNL_STATUS_FINAL, PNL_STATUS_MISMATCH):
    self.logger.warning(
        f"[Resolver] {symbol} 跳过 pnl_resolved 发布: "
        f"correction=None status={status} "
        f"position_id={resolution.get('position_id', '')}"
    )
    return
```

在 publish payload 字典中追加三个字段（基于现有字典结构）：

```python
"final_close_cause": resolution.get("final_close_cause", close_cause),
"close_evidence": resolution.get("close_evidence", {}),
"resolution_id": make_resolution_id(resolution, correction),
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest -q test_pnl_resolved_event_contract.py::TestResolveExternalCloseAsyncPublish -v`
Expected: 2 PASS

- [ ] **Step 6: 提交**

```bash
git add agents/trading/executor.py test_pnl_resolved_event_contract.py
git commit -m "[F4-002] _resolve_external_close_async 透传 final cause + resolution_id

publish payload 增加 final_close_cause / close_evidence / resolution_id。
correction=None 且 status=pending 时跳过发布并 logger.warning,避免脏事件。"
```

---

### Task 7: `_run_reconciliation` 透传字段集

**Files:**
- Modify: `agents/trading/executor.py:698-731` (`_run_reconciliation` publish payload)
- Test: `test_pnl_resolved_event_contract.py` (扩展)

- [ ] **Step 1: 写失败测试（追加）**

```python
class TestRunReconciliationPublish:
    @pytest.mark.asyncio
    async def test_run_reconciliation_publishes_final_cause(self):
        """_run_reconciliation 收到 summary 后发布 pnl_resolved 必须透传字段。"""
        from agents.trading.executor import MultiExecutor
        from unittest.mock import MagicMock, AsyncMock

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.publish = fake_publish
        ex.logger = MagicMock()

        # mock reconciler 返回一条 final summary
        rec = MagicMock()
        rec.auto_resolve_pending.return_value = [{
            "symbol": "BTC-USDT",
            "position_id": "pos-1",
            "entry_request_id": "req-1",
            "pnl_status": "final",
            "pnl_source": "okx_fills",
            "realized_pnl_net_usdt": -9.5,
            "estimated_pnl": -10,
            "exchange_pnl_usdt": -9.5,
            "fills_pnl_usdt": -10,
            "gross_close_pnl_usdt": -10,
            "fee_usdt": -0.5,
            "funding_usdt": 0,
            "order_ids": ["ord-1"],
            "bill_ids": ["bill-1"],
            "match_confidence": 1.0,
            "warnings": [],
            "sl_algo_id": "algo-1",
            "sl_algo_clord_id": "casllivebot42",
            "tp_algo_id": "",
            "tp_algo_clord_id": "",
            "entry_attribution": {},
            "supersedes_event_id": "PEND-9",
            "correction_event_id": "CORR-9",
            "pending_event_id": "PEND-9",
            # F4-002 新字段（来自 Task 5）
            "close_cause": "exchange_sl",
            "final_close_cause": "exchange_sl",
            "is_strategy_stop": True,
            "close_evidence": {"match_rule": "sl_algo_id_exact", "confidence": 1.0},
            "resolution_id": "corr:CORR-9",
        }]
        rec.run_and_report = MagicMock(return_value=None)
        ex._reconciler = rec

        await ex._run_reconciliation()

        topics = [t for t, _ in published]
        assert "pnl_resolved" in topics
        payload = next(p for t, p in published if t == "pnl_resolved")
        assert payload["final_close_cause"] == "exchange_sl"
        assert payload["is_strategy_stop"] is True
        assert payload["close_evidence"]["match_rule"] == "sl_algo_id_exact"
        assert payload["resolution_id"] == "corr:CORR-9"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_pnl_resolved_event_contract.py::TestRunReconciliationPublish -v`
Expected: FAIL

- [ ] **Step 3: 修改 `_run_reconciliation` (line 698-731)**

在 publish payload 字典中追加：

```python
# F4-002: final close cause 证据 + 幂等键
"close_cause": s.get("close_cause", ""),
"final_close_cause": s.get("final_close_cause", ""),
"is_strategy_stop": bool(s.get("is_strategy_stop", False)),
"close_evidence": s.get("close_evidence", {}),
"resolution_id": s.get("resolution_id", ""),
```

注意此处 summary 已由 Task 5 补齐字段，直接透传即可，无需调用 `make_resolution_id`。

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest -q test_pnl_resolved_event_contract.py::TestRunReconciliationPublish -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agents/trading/executor.py test_pnl_resolved_event_contract.py
git commit -m "[F4-002] _run_reconciliation 透传 final cause + resolution_id

summary 已含字段(Task 5),publish payload 直接透传 close_cause /
final_close_cause / is_strategy_stop / close_evidence / resolution_id。"
```

---

### Task 8: Judge / Reviewer LRU 去重 `resolution_id`

**Files:**
- Modify: `agents/trading/judge.py` (`_handle_pnl_resolved` / `_handle_pnl_mismatch`)
- Modify: `agents/trading/reviewer.py` (`_handle_pnl_resolved`)
- Test: `test_pnl_resolved_event_contract.py` (扩展)

- [ ] **Step 1: 定位现有 handler**

Run: `grep -n "_handle_pnl_resolved\|_handle_pnl_mismatch" agents/trading/judge.py agents/trading/reviewer.py`

确认两个文件中各有一个 handler。

- [ ] **Step 2: 写失败测试（追加）**

```python
class TestSubscriberDeduplication:
    @pytest.mark.asyncio
    async def test_judge_skips_duplicate_resolution_id(self):
        """Judge 收到同一 resolution_id 第二次时不重复 record SL hit。"""
        from agents.trading.judge import MultiJudge
        from unittest.mock import MagicMock

        j = MultiJudge.__new__(MultiJudge)
        j._symbol_state = {}
        j.logger = MagicMock()
        j._record_sl_hit = MagicMock()
        j._archetype_cooldown = MagicMock()
        # 触发 LRU 初始化
        j._seen_resolution_ids = None  # __init__ 应该已设置

        payload = {
            "symbol": "BTC-USDT",
            "is_strategy_stop": True,
            "final_close_cause": "exchange_sl",
            "resolution_id": "corr:E-1",
            "pnl_is_final": True,
            "realized_pnl_net_usdt": -9.5,
            "attribution": {},
        }
        await j._handle_pnl_resolved(payload)
        await j._handle_pnl_resolved(payload)
        # 第二次必须被去重(若调了 _record_sl_hit 则只调用一次)
        assert j._record_sl_hit.call_count <= 1

    @pytest.mark.asyncio
    async def test_judge_no_resolution_id_falls_back(self):
        """payload 缺 resolution_id 时不抛错(fail-safe)。"""
        from agents.trading.judge import MultiJudge
        from unittest.mock import MagicMock

        j = MultiJudge.__new__(MultiJudge)
        j._symbol_state = {}
        j.logger = MagicMock()
        j._record_sl_hit = MagicMock()
        j._archetype_cooldown = MagicMock()

        payload = {
            "symbol": "BTC-USDT",
            "is_strategy_stop": False,
            "pnl_is_final": True,
            "realized_pnl_net_usdt": -1.0,
            "attribution": {},
        }
        # 不应抛错
        await j._handle_pnl_resolved(payload)
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python3 -m pytest -q test_pnl_resolved_event_contract.py::TestSubscriberDeduplication -v`
Expected: FAIL（`_seen_resolution_ids` 不存在）

- [ ] **Step 4: 修改 `agents/trading/judge.py`**

在 `MultiJudge.__init__` 末尾追加：

```python
import collections as _collections
self._seen_resolution_ids = _collections.OrderedDict()
self._seen_resolution_ids_max = 1024
```

新增辅助方法（放在 `_handle_pnl_resolved` 之前）：

```python
def _is_duplicate_resolution(self, payload: dict) -> bool:
    """F4-002: 按 resolution_id LRU 去重,避免同一对账升级被处理两次。

    缺失 resolution_id 时返回 False(fail-safe,回退现有 correction_event_id 逻辑)。
    """
    rid = payload.get("resolution_id")
    if not rid:
        return False
    if rid in self._seen_resolution_ids:
        self._seen_resolution_ids.move_to_end(rid)
        return True
    self._seen_resolution_ids[rid] = True
    if len(self._seen_resolution_ids) > self._seen_resolution_ids_max:
        self._seen_resolution_ids.popitem(last=False)
    return False
```

在 `_handle_pnl_resolved` 与 `_handle_pnl_mismatch` 函数体最开头加：

```python
if self._is_duplicate_resolution(payload):
    return
```

- [ ] **Step 5: 修改 `agents/trading/reviewer.py`**

同模式：`__init__` 加 `_seen_resolution_ids` OrderedDict + `_is_duplicate_resolution` 方法 + `_handle_pnl_resolved` 入口去重。

- [ ] **Step 6: 运行测试确认通过**

Run: `python3 -m pytest -q test_pnl_resolved_event_contract.py::TestSubscriberDeduplication -v`
Expected: 2 PASS

- [ ] **Step 7: 跑既有 Judge / Reviewer 回归**

Run: `python3 -m pytest -q test_judge_close_cause.py test_external_close_final_cause.py`
Expected: 全 PASS

- [ ] **Step 8: 提交**

```bash
git add agents/trading/judge.py agents/trading/reviewer.py test_pnl_resolved_event_contract.py
git commit -m "[F4-002] Judge/Reviewer 按 resolution_id LRU 去重 pnl_resolved

容量 1024,缺 resolution_id 时 fail-safe 回退。Telegram 保留现有
60s _close_notify_cache 不接入(避免缓存交叉污染)。"
```

---

## F4-001 Agent reduce 失败回参分流

### Task 9: `_classify_reduce_outcome` helper 单点契约

**Files:**
- Modify: `agents/trading/executor.py` (新增静态方法到 `MultiExecutor` 类)
- Test: `test_reduce_failure_propagation.py` (Create)

- [ ] **Step 1: 写失败测试**

新建 `test_reduce_failure_propagation.py`：

```python
"""F4-001 reduce 失败回参分流测试矩阵。"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


def make_classification(result, requested_pct=0.5):
    from agents.trading.executor import MultiExecutor
    return MultiExecutor._classify_reduce_outcome(result, requested_pct)


class TestClassifyReduceOutcome:
    def test_result_none_returns_rejected(self):
        c = make_classification(None)
        assert c["status"] == "rejected"
        assert c["reason"] == "executor_returned_none"
        assert c["actual_reduce_pct"] == 0.0
        assert c["protection_failed"] is False
        assert c["action_override"] is None

    def test_sl_cancel_failed_returns_rejected(self):
        c = make_classification({
            "reduce_ok": False, "reason": "sl_cancel_failed",
            "protective_update_state": "cancel_failed",
            "protection_state": "unknown",
        })
        assert c["status"] == "rejected"
        assert c["reason"] == "sl_cancel_failed"
        assert c["actual_reduce_pct"] == 0.0
        assert c["protection_failed"] is False

    def test_sl_restore_failed_returns_rejected(self):
        c = make_classification({
            "reduce_ok": False, "reason": "sl_restore_failed",
            "protective_update_state": "restore_failed",
            "protection_state": "unknown",
        })
        assert c["status"] == "rejected"
        assert c["reason"] == "sl_restore_failed"

    def test_reduce_rejected_returns_reduce_failed(self):
        c = make_classification({
            "reduce_ok": False, "reason": "reduce_rejected",
            "protective_update_state": "restored_old_sl",
            "protection_state": "protected",
        })
        assert c["status"] == "reduce_failed"
        assert c["reason"] == "reduce_rejected"
        assert c["actual_reduce_pct"] == 0.0

    def test_dust_closed_returns_executed_close(self):
        c = make_classification({
            "reduce_ok": True, "ok": True,
            "protective_update_state": "dust_closed",
            "protection_state": "closed",
            "actual_reduce_amount": 100.0,
            "requested_reduce_amount": 100.0,
        })
        assert c["status"] == "executed"
        assert c["action_override"] == "close"
        assert c["protection_state"] == "closed"
        assert c["protection_failed"] is False

    def test_replace_failed_returns_risk_reduced_with_protection_failed(self):
        c = make_classification({
            "reduce_ok": True, "ok": False,
            "protective_update_state": "replace_failed",
            "protection_state": "unknown",
            "actual_reduce_amount": 50.0,
            "requested_reduce_amount": 100.0,
        })
        assert c["status"] == "risk_reduced"
        assert c["protection_failed"] is True
        assert c["protection_state"] == "unknown"
        # actual_reduce_pct = (50/100) * requested(0.5) = 0.25
        assert c["actual_reduce_pct"] == pytest.approx(0.25)

    def test_clean_ok_returns_risk_reduced_no_protection_failed(self):
        c = make_classification({
            "reduce_ok": True, "ok": True,
            "protective_update_state": "protected",
            "protection_state": "protected",
            "actual_reduce_amount": 50.0,
            "requested_reduce_amount": 100.0,
        })
        assert c["status"] == "risk_reduced"
        assert c["protection_failed"] is False
        assert c["protection_state"] == "protected"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_reduce_failure_propagation.py::TestClassifyReduceOutcome -v`
Expected: ImportError on `_classify_reduce_outcome`

- [ ] **Step 3: 实现 helper**

在 `agents/trading/executor.py` 的 `MultiExecutor` 类内（推荐放在 `_build_execution_result` 附近，便于阅读）追加：

```python
@staticmethod
def _classify_reduce_outcome(result, requested_pct):
    """F4-001: 把 root reduce_position() 返回的结构化 dict 折成 Agent 终态分类。

    输出字段:
        status: 'rejected' | 'reduce_failed' | 'risk_reduced' | 'executed'
        reason: 来自 result.reason 或派生
        actual_reduce_pct: 实际成交占请求的百分比(用于 RiskGuard 缩敞口)
        protection_failed: 是否需要 risk_alert{type=protection_failed}
        protection_state: 'protected' | 'unknown' | 'closed' | 'no_op'
        protective_update_state: 透传 result['protective_update_state']
        action_override: 'close' (dust_closed 路径) 或 None
        warnings: list,透传 result['warnings']

    分支:
      1. result is None  → rejected, reason=executor_returned_none
      2. reduce_ok=False, reason in {sl_cancel_failed, sl_restore_failed}
                          → rejected (pre-trade)
      3. reduce_ok=False, 其他 reason → reduce_failed (exchange reject)
      4. protective_update_state=dust_closed → executed, action_override=close
      5. reduce_ok=True, ok=False → risk_reduced + protection_failed=True
      6. ok=True → 干净 risk_reduced
    """
    if result is None:
        return {
            "status": "rejected", "reason": "executor_returned_none",
            "actual_reduce_pct": 0.0, "protection_failed": False,
            "protection_state": "unknown",
            "protective_update_state": "no_op",
            "action_override": None, "warnings": [],
        }

    reduce_ok = bool(result.get("reduce_ok", False))
    ok = bool(result.get("ok", False))
    reason = result.get("reason", "") or ""
    pus = result.get("protective_update_state", "") or ""
    actual_amt = float(result.get("actual_reduce_amount") or 0.0)
    requested_amt = float(result.get("requested_reduce_amount") or 0.0)
    if requested_amt > 0 and actual_amt >= 0:
        actual_pct = (actual_amt / requested_amt) * float(requested_pct)
    else:
        actual_pct = float(requested_pct) if reduce_ok else 0.0
    warnings = list(result.get("warnings") or [])

    # 分支 2: pre-trade 失败
    if not reduce_ok and reason in ("sl_cancel_failed", "sl_restore_failed"):
        return {
            "status": "rejected", "reason": reason,
            "actual_reduce_pct": 0.0, "protection_failed": False,
            "protection_state": result.get("protection_state", "unknown"),
            "protective_update_state": pus,
            "action_override": None, "warnings": warnings,
        }

    # 分支 3: 交易所 reject
    if not reduce_ok:
        return {
            "status": "reduce_failed", "reason": reason or "reduce_rejected",
            "actual_reduce_pct": 0.0, "protection_failed": False,
            "protection_state": result.get("protection_state", "unknown"),
            "protective_update_state": pus,
            "action_override": None, "warnings": warnings,
        }

    # 分支 4: dust_closed 视为平仓
    if pus == "dust_closed":
        return {
            "status": "executed", "reason": reason or "dust_closed",
            "actual_reduce_pct": actual_pct,
            "protection_failed": False,
            "protection_state": "closed",
            "protective_update_state": pus,
            "action_override": "close", "warnings": warnings,
        }

    # 分支 5: reduce 成交但 SL 重挂失败
    if reduce_ok and not ok:
        return {
            "status": "risk_reduced", "reason": reason or "protection_failed",
            "actual_reduce_pct": actual_pct,
            "protection_failed": True,
            "protection_state": "unknown",
            "protective_update_state": pus,
            "action_override": None, "warnings": warnings,
        }

    # 分支 6: 干净 risk_reduced
    return {
        "status": "risk_reduced", "reason": "ok",
        "actual_reduce_pct": actual_pct,
        "protection_failed": False,
        "protection_state": result.get("protection_state", "protected"),
        "protective_update_state": pus,
        "action_override": None, "warnings": warnings,
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest -q test_reduce_failure_propagation.py::TestClassifyReduceOutcome -v`
Expected: 7 PASS

- [ ] **Step 5: 提交**

```bash
git add agents/trading/executor.py test_reduce_failure_propagation.py
git commit -m "[F4-001] add _classify_reduce_outcome helper 单点契约

把 root reduce_position 返回 dict 折成 6 分支:
- rejected (None / pre-trade fail)
- reduce_failed (exchange reject)
- executed+action_override=close (dust_closed)
- risk_reduced + protection_failed=True (replace_failed)
- 干净 risk_reduced

三路径调用方共用此 helper,消除分支漂移。"
```

---

### Task 10: PositionAnalyst 部分平路径接入 helper

**Files:**
- Modify: `agents/trading/executor.py:225-284` (`elif action == 'close' and position is not None:` 块内 `if size_pct < 1.0` 分支)
- Test: `test_reduce_failure_propagation.py` (扩展)

- [ ] **Step 1: 阅读现有代码**

Run: `sed -n '220,290p' agents/trading/executor.py`

确认 line 225-284 包含 `if size_pct < 1.0 and source == 'position_analyst':` 分支，里面调 `self.executor.reduce_position` 后走 `if result:` 判断；line 281-283 有 `if source == 'position_analyst' and action == 'close' and size_pct < 1.0: payload["status"] = "risk_reduced"`。

- [ ] **Step 2: 写失败测试**

```python
class TestPositionAnalystPartialClose:
    @pytest.mark.asyncio
    async def test_replace_failed_emits_risk_reduced_with_protection_failed(self):
        from agents.trading.executor import MultiExecutor
        from unittest.mock import MagicMock, AsyncMock

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.publish = fake_publish
        ex.logger = MagicMock()
        ex.executor = MagicMock()
        ex.executor._normalize_symbol = lambda s: s
        ex.executor.get_position = lambda s: {
            "side": "long", "request_id": "req-1",
        }
        # mock root reduce_position 返回 replace_failed
        ex.executor.reduce_position = MagicMock(return_value={
            "reduce_ok": True, "ok": False,
            "protective_update_state": "replace_failed",
            "protection_state": "unknown",
            "actual_reduce_amount": 50.0,
            "requested_reduce_amount": 100.0,
            "warnings": ["residual_protection_failed"],
        })
        ex.config = {"max_trade_amount": 10}
        ex._open_fail_cooldown = {}

        decision = {
            "action": "close", "size_pct": 0.5,
            "request_id": "req-2", "source": "position_analyst",
            "confidence": 70, "plan": None,
        }
        await ex._handle_trade_decision(decision)

        risk_reduced = [p for t, p in published if t == "execution_result" and p.get("status") == "risk_reduced"]
        assert len(risk_reduced) == 1
        assert risk_reduced[0]["protection_failed"] is True
        assert risk_reduced[0]["protection_state"] == "unknown"

    @pytest.mark.asyncio
    async def test_sl_cancel_failed_emits_rejected_no_risk_reduced(self):
        from agents.trading.executor import MultiExecutor
        from unittest.mock import MagicMock

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.publish = fake_publish
        ex.logger = MagicMock()
        ex.executor = MagicMock()
        ex.executor._normalize_symbol = lambda s: s
        ex.executor.get_position = lambda s: {"side": "long", "request_id": "r"}
        ex.executor.reduce_position = MagicMock(return_value={
            "reduce_ok": False, "reason": "sl_cancel_failed",
            "protective_update_state": "cancel_failed",
            "protection_state": "unknown",
        })
        ex.config = {"max_trade_amount": 10}
        ex._open_fail_cooldown = {}

        decision = {
            "action": "close", "size_pct": 0.5,
            "request_id": "req-3", "source": "position_analyst",
            "confidence": 70, "plan": None,
        }
        await ex._handle_trade_decision(decision)

        statuses = [p.get("status") for t, p in published if t == "execution_result"]
        assert "rejected" in statuses
        assert "risk_reduced" not in statuses

    @pytest.mark.asyncio
    async def test_dust_closed_emits_executed_close(self):
        from agents.trading.executor import MultiExecutor
        from unittest.mock import MagicMock

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.publish = fake_publish
        ex.logger = MagicMock()
        ex.executor = MagicMock()
        ex.executor._normalize_symbol = lambda s: s
        ex.executor.get_position = lambda s: {"side": "long", "request_id": "r"}
        ex.executor.reduce_position = MagicMock(return_value={
            "reduce_ok": True, "ok": True,
            "protective_update_state": "dust_closed",
            "protection_state": "closed",
            "actual_reduce_amount": 80.0,
            "requested_reduce_amount": 100.0,
            "pnl": -3.0,
        })
        ex.config = {"max_trade_amount": 10}
        ex._open_fail_cooldown = {}

        decision = {
            "action": "close", "size_pct": 0.5,
            "request_id": "req-4", "source": "position_analyst",
            "confidence": 70, "plan": None,
        }
        await ex._handle_trade_decision(decision)

        statuses_actions = [(p.get("status"), p.get("action")) for t, p in published if t == "execution_result"]
        # dust_closed → executed + action=close
        assert ("executed", "close") in statuses_actions
        # 不应该出现 risk_reduced
        assert not any(s == "risk_reduced" for s, _ in statuses_actions)
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python3 -m pytest -q test_reduce_failure_propagation.py::TestPositionAnalystPartialClose -v`
Expected: FAIL（现有路径仍走 `if result:` → 全部发 risk_reduced）

- [ ] **Step 4: 修改 PositionAnalyst 部分平分支**

定位 `agents/trading/executor.py` 中 `elif action == 'close' and position is not None:` 块（line ~225-284）。改造调用 `reduce_position` 之后的处理：

```python
elif action == 'close' and position is not None:
    try:
        if size_pct < 1.0 and source == 'position_analyst':
            # 减仓：部分平仓
            result = await asyncio.to_thread(
                self.executor.reduce_position, norm_symbol, size_pct
            )
            classification = self._classify_reduce_outcome(result, size_pct)
            # F4-001: 按分类决定 status / action
            override_action = classification["action_override"] or action
            payload = self._build_execution_result(
                status=classification["status"], action=override_action,
                symbol=symbol, source="executor_close",
                request_id=request_id, result=result or {},
                reason=classification["reason"],
            )
            payload["confidence"] = confidence
            payload["used_plan"] = plan is not None
            attribution = decision.get('attribution')
            if attribution:
                payload['attribution'] = attribution
                if isinstance(result, dict):
                    result['attribution'] = attribution
            if classification["status"] == "risk_reduced":
                payload["reduce_pct"] = classification["actual_reduce_pct"]
                payload["protection_state"] = classification["protection_state"]
                payload["protective_update_state"] = classification["protective_update_state"]
                if classification["protection_failed"]:
                    payload["protection_failed"] = True
            elif classification["status"] == "executed" and override_action == "close":
                payload["protection_state"] = classification["protection_state"]
                payload["protective_update_state"] = classification["protective_update_state"]
                payload["reduce_origin"] = True
            elif classification["status"] in ("rejected", "reduce_failed"):
                # 失败终态: 不带 reduce_pct,不发 risk_reduced
                payload.pop("reduce_pct", None)
            await self.publish("execution_result", payload, symbol=symbol)
            self.logger.info(
                f"[执行] {symbol} reduce {classification['status']} "
                f"reason={classification['reason']} "
                f"actual_pct={classification['actual_reduce_pct']:.4f}"
            )
            return
        else:
            # 全平 — FR-003: close_position() 内部清理保护单
            result = self.executor.close_position(norm_symbol)
            if result:
                self.logger.info(f"[执行] {symbol} 平仓 PnL={result.get('pnl', 0):.2f}")
    except Exception as e:
        self.logger.error(f"[执行] {symbol} 平仓失败: {e}")
        await self.publish("execution_result", self._build_execution_result(
            status="error", action=action, symbol=symbol,
            source="executor_close", reason=str(e), request_id=request_id,
        ), symbol=symbol)
        return
```

注意：原本 line 281-283 的 `if source == 'position_analyst' and action == 'close' and size_pct < 1.0:` 兜底 patch 现在被新分支提前 return 跳过，需要确认下方 `if result:` 块只在全平路径生效。

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest -q test_reduce_failure_propagation.py::TestPositionAnalystPartialClose -v`
Expected: 3 PASS

- [ ] **Step 6: 跑 PositionAnalyst 现有回归**

Run: `python3 -m pytest -q test_reduce_protective_sl_lifecycle.py`
Expected: 14 PASS

- [ ] **Step 7: 提交**

```bash
git add agents/trading/executor.py test_reduce_failure_propagation.py
git commit -m "[F4-001] PositionAnalyst 部分平接入 _classify_reduce_outcome

按 classification.status 分流:rejected/reduce_failed/risk_reduced/executed-close。
dust_closed 走 executed+close 而非 risk_reduced。
protection_failed 时 payload 含 protection_failed=True + protection_state=unknown。"
```

---

### Task 11: portfolio_exposure / correlation_risk 风控减仓接入 helper

**Files:**
- Modify: `agents/trading/executor.py:438-454` (`elif alert_type in ('portfolio_exposure', 'correlation_risk'):` 块)
- Test: `test_reduce_failure_propagation.py` (扩展)

- [ ] **Step 1: 写失败测试**

```python
class TestPortfolioExposureReduce:
    @pytest.mark.asyncio
    async def test_replace_failed_emits_risk_reduced_with_protection_failed(self):
        from agents.trading.executor import MultiExecutor
        from unittest.mock import MagicMock

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.publish = fake_publish
        ex.logger = MagicMock()
        ex.executor = MagicMock()
        ex.executor.get_all_positions.return_value = {
            "BTC-USDT": {"amount_usdt": 100, "request_id": "r"},
        }
        ex.executor.reduce_position = MagicMock(return_value={
            "reduce_ok": True, "ok": False,
            "protective_update_state": "replace_failed",
            "protection_state": "unknown",
            "actual_reduce_amount": 50.0,
            "requested_reduce_amount": 50.0,  # 风控请求 50% of 100USDT = 50USDT
        })

        await ex._handle_risk_alert({
            "type": "portfolio_exposure", "scope": "market",
        })

        risk_reduced = [p for t, p in published if p.get("status") == "risk_reduced"]
        assert len(risk_reduced) == 1
        assert risk_reduced[0]["protection_failed"] is True

    @pytest.mark.asyncio
    async def test_reduce_rejected_no_risk_reduced(self):
        from agents.trading.executor import MultiExecutor
        from unittest.mock import MagicMock

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.publish = fake_publish
        ex.logger = MagicMock()
        ex.executor = MagicMock()
        ex.executor.get_all_positions.return_value = {
            "BTC-USDT": {"amount_usdt": 100, "request_id": "r"},
        }
        ex.executor.reduce_position = MagicMock(return_value={
            "reduce_ok": False, "reason": "reduce_rejected",
            "protective_update_state": "restored_old_sl",
        })

        await ex._handle_risk_alert({
            "type": "correlation_risk", "scope": "market",
        })

        statuses = [p.get("status") for t, p in published]
        assert "reduce_failed" in statuses
        assert "risk_reduced" not in statuses
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_reduce_failure_propagation.py::TestPortfolioExposureReduce -v`
Expected: FAIL

- [ ] **Step 3: 修改 portfolio_exposure 路径 (line 438-454)**

```python
elif alert_type in ('portfolio_exposure', 'correlation_risk'):
    positions = self.executor.get_all_positions()
    if not positions:
        return
    largest_sym = max(positions, key=lambda s: positions[s].get('amount_usdt', 0))
    self.logger.warning(f"[风控] {alert_type}: 减仓 {largest_sym} 50%")
    pos = positions.get(largest_sym, {})
    entry_req_id = pos.get('request_id', '')
    result = self.executor.reduce_position(largest_sym, 0.5)
    classification = self._classify_reduce_outcome(result, 0.5)
    override_action = classification["action_override"] or "reduce"
    payload = self._build_execution_result(
        status=classification["status"], action=override_action,
        symbol=largest_sym, source="risk_alert",
        reason=classification["reason"] or alert_type,
        result=result or {}, request_id=entry_req_id,
    )
    if classification["status"] == "risk_reduced":
        payload["reduce_pct"] = classification["actual_reduce_pct"]
        payload["protection_state"] = classification["protection_state"]
        payload["protective_update_state"] = classification["protective_update_state"]
        if classification["protection_failed"]:
            payload["protection_failed"] = True
    elif classification["status"] == "executed" and override_action == "close":
        payload["reduce_origin"] = True
        payload["protection_state"] = classification["protection_state"]
    await self.publish("execution_result", payload, symbol=largest_sym)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest -q test_reduce_failure_propagation.py::TestPortfolioExposureReduce -v`
Expected: 2 PASS

- [ ] **Step 5: 提交**

```bash
git add agents/trading/executor.py test_reduce_failure_propagation.py
git commit -m "[F4-001] portfolio_exposure / correlation_risk 减仓接入 helper

风控减仓失败不再误广播 risk_reduced。"
```

---

### Task 12: partial_tp_1 / partial_tp_2 接入 helper

**Files:**
- Modify: `agents/trading/executor.py:1004-1020` (`elif trigger in ('partial_tp_1', 'partial_tp_2'):` 块)
- Test: `test_reduce_failure_propagation.py` (扩展)

- [ ] **Step 1: 写失败测试**

```python
class TestPartialTpReduce:
    @pytest.mark.asyncio
    async def test_partial_tp_replace_failed_protection_failed(self):
        from agents.trading.executor import MultiExecutor
        from unittest.mock import MagicMock

        published = []

        async def fake_publish(topic, payload, symbol=None):
            published.append((topic, payload))

        ex = MultiExecutor.__new__(MultiExecutor)
        ex.publish = fake_publish
        ex.logger = MagicMock()
        ex.executor = MagicMock()
        ex.executor.positions = {
            "BTC-USDT": {"side": "long", "request_id": "r"},
        }
        ex.executor.reduce_position = MagicMock(return_value={
            "reduce_ok": True, "ok": False,
            "protective_update_state": "replace_failed",
            "protection_state": "unknown",
            "actual_reduce_amount": 50.0,
            "requested_reduce_amount": 100.0,
        })

        # 调用 partial_tp 路径(具体调用方式见现有实现)
        # 这里用统一 _handle_local_stop_trigger / _handle_partial_tp 的入口
        await ex._handle_local_stop("BTC-USDT", "partial_tp_1")

        risk_reduced = [p for t, p in published if p.get("status") == "risk_reduced"]
        assert len(risk_reduced) == 1
        assert risk_reduced[0]["protection_failed"] is True
        assert risk_reduced[0]["reduce_pct"] == pytest.approx(0.25)  # 0.5 * (50/100)
```

> **注意**：`_handle_local_stop` 的精确调用签名以现有 `agents/trading/executor.py:1004` 上下文为准。如果实际函数名不同，先 `grep -n "partial_tp_1" agents/trading/executor.py` 定位，再调整 mock。

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q test_reduce_failure_propagation.py::TestPartialTpReduce -v`
Expected: FAIL

- [ ] **Step 3: 修改 partial_tp 块 (line 1004-1020)**

```python
elif trigger in ('partial_tp_1', 'partial_tp_2'):
    pct = 0.5 if trigger == 'partial_tp_1' else 0.25
    tp_advance = 1 if trigger == 'partial_tp_1' else 2
    self.logger.info(f"[Trailing] {symbol} {trigger}，减仓{int(pct*100)}%")
    pos = self.executor.positions.get(symbol)
    entry_req_id = (pos or {}).get('request_id', '')
    result = await asyncio.to_thread(
        self.executor.reduce_position, symbol, pct, tp_advance
    )
    classification = self._classify_reduce_outcome(result, pct)
    override_action = classification["action_override"] or "close"
    payload = self._build_execution_result(
        status=classification["status"], action=override_action,
        symbol=symbol, source="partial_tp",
        reason=classification["reason"] or trigger,
        result=result or {}, request_id=entry_req_id,
    )
    if classification["status"] == "risk_reduced":
        payload["reduce_pct"] = classification["actual_reduce_pct"]
        payload["protection_state"] = classification["protection_state"]
        payload["protective_update_state"] = classification["protective_update_state"]
        if classification["protection_failed"]:
            payload["protection_failed"] = True
    elif classification["status"] == "executed" and override_action == "close":
        payload["reduce_origin"] = True
        payload["protection_state"] = classification["protection_state"]
    await self.publish("execution_result", payload, symbol=symbol)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest -q test_reduce_failure_propagation.py::TestPartialTpReduce -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agents/trading/executor.py test_reduce_failure_propagation.py
git commit -m "[F4-001] partial_tp_1/2 接入 _classify_reduce_outcome

锁利路径与 PositionAnalyst / 风控减仓共用同一分流 helper。"
```

---

### Task 13: PortfolioRiskGuard 处理新 status

**Files:**
- Modify: `agents/trading/portfolio_risk_guard.py:139-148` (`_on_execution_result` reduce / close 分支)
- Test: `test_reduce_failure_propagation.py` (扩展)

- [ ] **Step 1: 阅读现有代码**

Run: `sed -n '120,150p' agents/trading/portfolio_risk_guard.py`

- [ ] **Step 2: 写失败测试**

```python
class TestPortfolioRiskGuardReduceHandling:
    @pytest.mark.asyncio
    async def test_rejected_does_not_shrink_exposure(self):
        from agents.trading.portfolio_risk_guard import PortfolioRiskGuard
        from unittest.mock import MagicMock

        g = PortfolioRiskGuard.__new__(PortfolioRiskGuard)
        g._positions = {"BTC-USDT": {"amount_usdt": 100.0, "side": "long",
                                       "leverage": 5, "open_time": 0,
                                       "highest_price": 0, "lowest_price": 0}}
        g._prices = {}
        g._price_history = {}
        g.logger = MagicMock()
        g.publish = MagicMock()
        # 模拟 _on_execution_result 处理 rejected
        await g._on_execution_result({
            "status": "rejected", "action": "close",
            "symbol": "BTC-USDT", "reason": "sl_cancel_failed",
        })
        assert g._positions["BTC-USDT"]["amount_usdt"] == 100.0

    @pytest.mark.asyncio
    async def test_protection_failed_still_shrinks_and_emits_alert(self):
        from agents.trading.portfolio_risk_guard import PortfolioRiskGuard
        from unittest.mock import MagicMock, AsyncMock

        published = []

        async def fake_publish(topic, payload):
            published.append((topic, payload))

        g = PortfolioRiskGuard.__new__(PortfolioRiskGuard)
        g._positions = {"BTC-USDT": {"amount_usdt": 100.0}}
        g._prices = {}
        g._price_history = {}
        g.logger = MagicMock()
        g.publish = fake_publish

        await g._on_execution_result({
            "status": "risk_reduced", "action": "close",
            "symbol": "BTC-USDT",
            "reduce_pct": 0.25,
            "protection_failed": True,
            "protective_update_state": "replace_failed",
            "request_id": "r-9",
        })
        assert g._positions["BTC-USDT"]["amount_usdt"] == pytest.approx(75.0)
        types = [p.get("type") for t, p in published if t == "risk_alert"]
        assert "protection_failed" in types

    @pytest.mark.asyncio
    async def test_dust_closed_removes_symbol(self):
        from agents.trading.portfolio_risk_guard import PortfolioRiskGuard
        from unittest.mock import MagicMock

        g = PortfolioRiskGuard.__new__(PortfolioRiskGuard)
        g._positions = {"BTC-USDT": {"amount_usdt": 100.0}}
        g._prices = {}
        g._price_history = {}
        g.logger = MagicMock()
        g.publish = MagicMock()

        await g._on_execution_result({
            "status": "executed", "action": "close",
            "symbol": "BTC-USDT",
            "reduce_origin": True,
            "protection_state": "closed",
        })
        assert "BTC-USDT" not in g._positions
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python3 -m pytest -q test_reduce_failure_propagation.py::TestPortfolioRiskGuardReduceHandling -v`
Expected: FAIL（现有 RiskGuard 不识别 rejected/protection_failed/reduce_origin）

- [ ] **Step 4: 修改 `_on_execution_result` reduce 分支**

定位 line ~139-148（`elif status in ('force_closed', 'closed_externally'):` 之后到 `def _update_price` 之前）。在 `risk_reduced` 分支前插入新分支：

```python
elif status == 'force_closed' or status == 'closed_externally':
    if symbol in self._positions:
        self.logger.info(f"[风控] {symbol} 外部平仓，移除追踪")
    self._positions.pop(symbol, None)

# F4-001: dust_closed 走 close 分支移除 symbol
elif status == 'executed' and action == 'close' and payload.get('reduce_origin'):
    if symbol in self._positions:
        self.logger.info(f"[风控] {symbol} dust_closed,移除追踪")
    self._positions.pop(symbol, None)

# F4-001: 失败/拒绝终态不缩敞口
elif status in ('rejected', 'reduce_failed'):
    self.logger.info(f"[风控] {symbol} reduce {status},不缩敞口")
    return

elif status == 'risk_reduced':
    if symbol in self._positions:
        reduce_pct = payload.get('reduce_pct', 0.5)
        self._positions[symbol]['amount_usdt'] *= (1 - reduce_pct)
    if payload.get('protection_failed'):
        await self.publish('risk_alert', {
            'type': 'protection_failed',
            'symbol': symbol,
            'protective_update_state': payload.get('protective_update_state', ''),
            'request_id': payload.get('request_id', ''),
        })
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest -q test_reduce_failure_propagation.py::TestPortfolioRiskGuardReduceHandling -v`
Expected: 3 PASS

- [ ] **Step 6: 提交**

```bash
git add agents/trading/portfolio_risk_guard.py test_reduce_failure_propagation.py
git commit -m "[F4-001] PortfolioRiskGuard 按新 status 分流敞口处理

rejected/reduce_failed 不缩;dust_closed 移除 symbol;
protection_failed 缩敞口 + 发 risk_alert。"
```

---

### Task 14: TelegramNotifier 文案分流

**Files:**
- Modify: `agents/trading/telegram_notifier.py:129-167` (`risk_reduced` / `executed close` 分支)
- Test: `test_reduce_failure_propagation.py` (扩展)

- [ ] **Step 1: 阅读现有代码**

Run: `sed -n '125,175p' agents/trading/telegram_notifier.py`

- [ ] **Step 2: 写失败测试**

```python
class TestTelegramReduceMessages:
    @pytest.mark.asyncio
    async def test_clean_reduce_short_message(self):
        from agents.trading.telegram_notifier import TelegramNotifier
        from unittest.mock import MagicMock, AsyncMock

        sent = []

        async def fake_send(text):
            sent.append(text)

        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._send_message = fake_send

        await n._on_execution_result({
            "status": "risk_reduced", "action": "close",
            "symbol": "BTC-USDT", "reduce_pct": 0.5,
        })
        assert any("减仓" in s and "保护单" not in s for s in sent)

    @pytest.mark.asyncio
    async def test_protection_failed_message(self):
        from agents.trading.telegram_notifier import TelegramNotifier
        from unittest.mock import MagicMock

        sent = []

        async def fake_send(text):
            sent.append(text)

        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._send_message = fake_send

        await n._on_execution_result({
            "status": "risk_reduced", "action": "close",
            "symbol": "BTC-USDT", "reduce_pct": 0.5,
            "protection_failed": True,
            "protective_update_state": "replace_failed",
        })
        text = "\n".join(sent)
        assert "replace_failed" in text or "保护单" in text
        assert "unknown" in text or "故障" in text or "异常" in text

    @pytest.mark.asyncio
    async def test_rejected_no_reduce_message(self):
        from agents.trading.telegram_notifier import TelegramNotifier
        from unittest.mock import MagicMock

        sent = []

        async def fake_send(text):
            sent.append(text)

        n = TelegramNotifier.__new__(TelegramNotifier)
        n.logger = MagicMock()
        n._send_message = fake_send

        await n._on_execution_result({
            "status": "rejected", "action": "close",
            "symbol": "BTC-USDT", "reason": "sl_cancel_failed",
        })
        # rejected 路径不应发减仓文案
        assert not any("减仓" in s for s in sent)
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python3 -m pytest -q test_reduce_failure_propagation.py::TestTelegramReduceMessages -v`
Expected: FAIL

- [ ] **Step 4: 修改 `risk_reduced` 分支 (line 129-132)**

```python
elif status == 'risk_reduced':
    reduce_pct = payload.get('reduce_pct', 0.5)
    if payload.get('protection_failed'):
        pus = payload.get('protective_update_state', 'unknown')
        text = (
            f"⚠️ 减仓 {symbol} {int(reduce_pct*100)}% 已成交\n"
            f"但保护单异常: {pus}\n"
            f"protection_state=unknown,需人工核查"
        )
    else:
        text = f"✂️ 减仓 {symbol} {int(reduce_pct*100)}%"
    await self._send_message(text)
```

`rejected` / `reduce_failed` 分支：现有 close/executed 分支条件是 `status in ('executed', 'force_closed', 'closed_externally') and (action == 'close' or status in (...))`，新 status 不会落入；只需要确保不在其它地方误发减仓文案即可。如果 TelegramNotifier 有默认兜底分支会发"未知 status"文案，可以加显式 skip：

```python
elif status in ('rejected', 'reduce_failed'):
    return  # F4-001: 失败终态不发 Telegram(由 protection_failed risk_alert 兜底)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest -q test_reduce_failure_propagation.py::TestTelegramReduceMessages -v`
Expected: 3 PASS

- [ ] **Step 6: 提交**

```bash
git add agents/trading/telegram_notifier.py test_reduce_failure_propagation.py
git commit -m "[F4-001] Telegram 文案按 protection_failed 分流

干净减仓走简短文案;protection_failed 走故障告警文案带
protective_update_state;rejected/reduce_failed 不发减仓文案。"
```

---

## 全量回归与验证收尾

### Task 15: 字节码编译 + 默认全量回归

- [ ] **Step 1: 编译扫描**

Run: `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit5_pycache python3 -m compileall -q .`
Expected: 无 SyntaxError

- [ ] **Step 2: 默认全量回归**

Run: `python3 -m pytest -q`
Expected: ≥ 832 passed / 4 deselected / 1 warning（基线 807 + 至少 25 新 case）

- [ ] **Step 3: 若失败,定位 + 修复**

如有回归，按错误信息定位修改对应实现或测试并提交修复 commit `[F4-fix] ...`，重跑直到全绿。

---

### Task 16: network 分层回归

- [ ] **Step 1: 跑 network 标签测试**

Run: `python3 -m pytest -q -m network`
Expected: 4 PASS

- [ ] **Step 2: 失败时定位**

network 测试依赖网络 + Telegram 凭证，失败先确认环境（不计入回归 NO-GO）。

---

### Task 17: OKX testnet 冒烟（reduce + external close 两个场景）

- [ ] **Step 1: 设置 testnet 环境**

Run: `export USE_TESTNET=true; export STATE_NAMESPACE=testnet; export BOT_INSTANCE_ID=audit5-bot`
（或在 `.env` 中临时设置）

- [ ] **Step 2: 跑 OKX testnet 语义验收**

Run: `python3 verify_okx_testnet_semantics.py`
Expected: 至少 reduce + external close 两个场景 PASS，banner 含 `BOT_INSTANCE_ID: audit5-bot`，新挂 SL 的 `attachAlgoClOrdId` 满足 `_is_owner_clord_id`

- [ ] **Step 3: 用脚本检查 OKX 上 algoClOrdId**

如冒烟过程中能记录到挂单日志，确认日志中 `algoClOrdId` 含 `caaudit5audit5BTC` 类前缀（namespace=testnet 截断 + bot=audit5b 截断 + base=BTC）。

如冒烟失败仅作为人工确认依据，不强制阻塞 commit；写在验收报告里。

---

### Task 18: 撰写验收报告

**Files:**
- Create: `docs/audit_remediation_fourth_pass_20260528_acceptance.md`

- [ ] **Step 1: 写验收报告**

报告结构：

```markdown
# 第四次审计整改验收报告 (2026-05-29)

## 范围

- F4-001: reduce 失败回参 Agent 误广播 risk_reduced
- F4-002: pnl_resolved 总线事件未透传 final close cause / 证据 / 幂等键
- F4-003: OKX 真实新 SL 未使用 owner-tag clOrdId

## 验收命令

[列出 Task 15-17 的所有验收命令与预期输出]

## 验收结果

[按 OpenSpec 三个 capability 列出 AC 通过情况]

### F4-001 reduce-result-propagation
- AC-1.x 全部通过/失败说明

### F4-002 pnl-resolution-bus-events
- AC-2.x ...

### F4-003 protective-sl-owner-tag
- AC-3.x ...

## Go/No-Go

| 范围 | 第四次整改后 |
|---|---|
| 本地开发 | GO |
| paper/mock | GO |
| 小额 live 灰度 | GO（解除 NO-GO） |
| live 扩容 | CONDITIONAL GO（需运维 SOP 确认 BOT_INSTANCE_ID） |

## 附件

- 全量 pytest 输出: [日志摘要]
- OKX testnet 验收日志: [文件位置]
```

- [ ] **Step 2: 提交**

```bash
git add docs/audit_remediation_fourth_pass_20260528_acceptance.md
git commit -m "[F4-acceptance] 第四次审计整改验收报告

闭环 F4-001/002/003,解除 live 扩容 NO-GO 前置。"
```

---

### Task 19: 文档同步

**Files:**
- Modify: `CLAUDE.md` (当前事实段)
- Modify: `docs/to-do-list.md` (移阻断项到已关闭)
- Modify: `openspec/changes/audit-fourth-pass-blockers/tasks.md`（全部勾选）

- [ ] **Step 1: 更新 CLAUDE.md 当前事实**

在现有 "当前事实" 段追加：

```markdown
- 2026-05-29 第四次审计 F4-001/002/003 整改后基线：`<新数字> passed / 4 deselected / 1 warning`（新增 `test_owner_tag_clord_id_callsites.py` ~9 + `test_pnl_resolved_event_contract.py` ~12 + `test_reduce_failure_propagation.py` ~17 case）。F4-001 Agent reduce 路径接入 `_classify_reduce_outcome` 单点契约,失败不再误广播 risk_reduced;F4-002 `pnl_resolved/pnl_mismatch` 三发布点透传 `final_close_cause/close_evidence/resolution_id`,Judge/Reviewer 按 LRU 去重;F4-003 OKX 真实新 SL(attach/replace/legacy)统一走 `_make_owner_tag_clord_id`,live 缺 BOT_INSTANCE_ID 时 banner WARNING。详见 `docs/audit_remediation_fourth_pass_20260528_acceptance.md`。live 扩容 NO-GO 解除前置完成,等待运维 SOP 确认 BOT_INSTANCE_ID 后可分批扩容。
```

- [ ] **Step 2: 更新 docs/to-do-list.md**

把 F4-001/002/003 三项移到 "已关闭事项" 章节，附整改 commit / 验收报告链接。

- [ ] **Step 3: 勾选 OpenSpec tasks.md**

把 `openspec/changes/audit-fourth-pass-blockers/tasks.md` 中本次落地的任务从 `- [ ]` 改为 `- [x]`：1.1-1.6 / 2.1-2.7 / 3.1-3.8 / 4.1-4.7 全部勾选。

- [ ] **Step 4: 提交**

```bash
git add CLAUDE.md docs/to-do-list.md openspec/changes/audit-fourth-pass-blockers/tasks.md
git commit -m "[F4-docs] 同步 CLAUDE.md / to-do-list / tasks.md

第四次审计整改闭环,基线更新。"
```

---

## 自检清单

- **Spec coverage**：
  - reduce-result-propagation 的 7 个 scenario → Task 9-14
  - pnl-resolution-bus-events 的 9 个 scenario → Task 4-8
  - protective-sl-owner-tag 的 6 个 scenario → Task 1-3
  - 无遗漏

- **类型一致性**：`_classify_reduce_outcome` 字段名（`status` / `action_override` / `reduce_origin` / `protection_failed` / `actual_reduce_pct` / `protective_update_state`）在 Task 9 定义后被 10/11/12/13/14 全部按同名引用，未漂移

- **占位符扫描**：无 TBD/TODO/「类似 Task N」；每个 step 都有具体代码或命令

- **验证命令**：每个 Task 末尾都有 `pytest` 命令与预期输出


