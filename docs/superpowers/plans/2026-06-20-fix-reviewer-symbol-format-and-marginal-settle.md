---
change: fix-reviewer-symbol-format-and-marginal-settle
design-doc: docs/superpowers/specs/2026-06-20-fix-reviewer-symbol-format-and-marginal-settle-design.md
base-ref: c3b201c27902cd90f36a656de927053c8ba4356a
---

# Reviewer symbol 格式根治 + 边缘单从权威源结算 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** reviewer 写 trade_record/日志的 symbol 经 `to_internal` 归一为内部 `BASE-USDT`（根治格式混乱），`track_marginal60.py` 结算源改读权威 `live_position_lifecycle.json`。

**Architecture:** 消费侧收口——reviewer 3 处 symbol 入口套 `to_internal`（record-field/log-only，无 key-lookup 风险）；tracker fill 与 lifecycle 都归一后按 symbol+side+opened_at≈fill_ts 容差 join 取 `total_realized_pnl`。不碰 close path/PnL 来源/历史数据。

**Tech Stack:** Python 3.9, pytest；复用 `utils/symbol.py::to_internal`、`data/live_position_lifecycle.json`。

---

## Task 1: reviewer 入口 symbol 归一

**Files:**
- Modify: `agents/trading/reviewer.py`（import + 3 处 symbol 取值 ~112/151/216）
- Test: `tests/test_reviewer_symbol_canonical.py`

**实施者须先做**：Read `agents/trading/reviewer.py` 确认 3 处 `symbol = msg.get('symbol') or payload.get('symbol')` 的精确行号（设计标 ~112/151/216，以实读为准）+ 顶部 import 区。

- [ ] **Step 1: Write the failing test**

创建 `tests/test_reviewer_symbol_canonical.py`：

```python
"""fix-reviewer-symbol-format: reviewer trade record symbol 归一为内部格式。"""
import asyncio
from unittest import mock

from agents.trading.reviewer import ReviewerAgent


def _bare_reviewer():
    r = ReviewerAgent.__new__(ReviewerAgent)
    r.logger = mock.MagicMock()
    r.trade_history = []
    r._save_trade_history = mock.MagicMock()
    return r


def test_trade_record_symbol_normalized_swap():
    # execution_result close payload 带 -SWAP → trade_record['symbol'] 归一为 BASE-USDT
    r = _bare_reviewer()
    msg = {"timestamp": 1.0, "symbol": "XRP-USDT-SWAP",
           "payload": {"symbol": "XRP-USDT-SWAP", "action": "close",
                       "result": {"realized_pnl_net_usdt": -0.58, "pnl_is_final": True,
                                  "side": "short", "attribution": {}}}}
    asyncio.run(r._handle_execution_result(msg))
    recs = [t for t in r.trade_history if t.get("symbol")]
    assert recs, "应记录一笔"
    assert all(t["symbol"] == "XRP-USDT" for t in recs)   # 归一, 无 -SWAP


def test_trade_record_symbol_idempotent_and_none_safe():
    from utils.symbol import to_internal
    assert to_internal("XRP-USDT") == "XRP-USDT"            # 幂等
    assert to_internal(None) in (None, "")                 # None fail-safe 不抛
```

注：`_handle_execution_result` 方法名/入参以实读为准（reviewer 消费 `execution_result.v2` 的入口）；若入口名不同，测试改调真实入口，断言仍是"trade_record['symbol'] 归一"。

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_reviewer_symbol_canonical.py -v`
Expected: FAIL（`test_trade_record_symbol_normalized_swap` 得到 `XRP-USDT-SWAP`）

- [ ] **Step 3: Write minimal implementation**

`agents/trading/reviewer.py` 顶部 import 区加：

```python
from utils.symbol import to_internal
```

3 处 `symbol = msg.get('symbol') or payload.get('symbol')` 改为：

```python
symbol = to_internal(msg.get('symbol') or payload.get('symbol'))
```

（共 ~112/151/216 三处；逐字符串替换。`to_internal` 对 None 返回原值不抛，外层 `or` 兜底保留。）

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_reviewer_symbol_canonical.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/trading/reviewer.py tests/test_reviewer_symbol_canonical.py
git commit -m "feat(reviewer-symbol): trade record/日志 symbol 经 to_internal 归一为 BASE-USDT"
```

---

## Task 2: track_marginal60 结算源改读 lifecycle

**Files:**
- Modify: `scripts/track_marginal60.py`
- Test: `tests/test_reviewer_symbol_canonical.py`

**实施者须先做**：Read `scripts/track_marginal60.py` 全文，确认现 PnL 收集（grep reviewer 日志 `记录交易`）与配对逻辑的精确位置；fill/tier 收集（judge 日志）保留不动。

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_reviewer_symbol_canonical.py`（测纯函数 settle，不依赖真实文件）：

```python
def test_settle_from_lifecycle_normalizes_and_joins():
    from scripts.track_marginal60 import settle_fill_from_lifecycle
    lifecycle = {
        "ETH-USDT-SWAP-aaa-long": {"symbol": "ETH-USDT-SWAP", "side": "long",
            "opened_at": 1000.0, "status": "closed",
            "total_realized_pnl": 0.86, "reconcile_status": "matched"},
    }
    # fill: ETH-USDT @ ts=1010 (窗内, 归一后 symbol 匹配)
    pnl, used = settle_fill_from_lifecycle("ETH-USDT", "long", 1010.0, lifecycle, set(), tol=300)
    assert abs(pnl - 0.86) < 1e-9
    assert used   # 标记已消费的 lifecycle key


def test_settle_pending_or_out_of_window_unsettled():
    from scripts.track_marginal60 import settle_fill_from_lifecycle
    lc_pending = {"X-USDT-SWAP-bbb-long": {"symbol": "X-USDT-SWAP", "side": "long",
        "opened_at": 1000.0, "status": "open", "total_realized_pnl": None,
        "reconcile_status": "pending"}}
    pnl, _ = settle_fill_from_lifecycle("X-USDT", "long", 1010.0, lc_pending, set(), tol=300)
    assert pnl is None                                   # pending → 未结算
    # 窗外
    pnl2, _ = settle_fill_from_lifecycle("X-USDT", "long", 9999.0, lc_pending, set(), tol=300)
    assert pnl2 is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_reviewer_symbol_canonical.py -k settle -v`
Expected: FAIL（`settle_fill_from_lifecycle` 不存在）

- [ ] **Step 3: Write minimal implementation**

在 `scripts/track_marginal60.py` 加纯函数 + 改结算源：

```python
import json
from utils.symbol import to_internal

LIFECYCLE = os.path.join(ROOT, "data", "live_position_lifecycle.json")


def load_lifecycle(path=LIFECYCLE):
    if not os.path.exists(path):
        return {}
    try:
        return json.load(open(path))
    except Exception:
        return {}


def settle_fill_from_lifecycle(sym_internal, side, fill_ts, lifecycle, used_keys, tol=300):
    """按 symbol(归一) + side + |opened_at-fill_ts|<=tol join lifecycle, 取 total_realized_pnl。

    返回 (pnl_or_None, matched_key_or_None)。已用 key 在 used_keys 中跳过, 避免重复消费。
    """
    best_key, best_dt = None, None
    for k, v in lifecycle.items():
        if not isinstance(v, dict) or k in used_keys:
            continue
        if to_internal(v.get("symbol", "")) != sym_internal:
            continue
        if v.get("side") != side:
            continue
        op = v.get("opened_at")
        if op is None:
            continue
        dt = abs(op - fill_ts)
        if dt <= tol and (best_dt is None or dt < best_dt):
            best_key, best_dt = k, dt
    if best_key is None:
        return None, None
    v = lifecycle[best_key]
    pnl = v.get("total_realized_pnl")
    if pnl is None or v.get("status") not in ("closed",):
        return None, None
    return float(pnl), best_key
```

在 `main()`：删除/替换原 grep reviewer 日志收集 `pnls` 与按 `used_pnl` 时序配对的逻辑；改为 `lifecycle = load_lifecycle()`，对每个 fill 调 `settle_fill_from_lifecycle(to_internal(sym), side, fill_ts, lifecycle, used_keys, tol=300)`，命中即 `used_keys.add(key)`。fill 的 `sym`/`side` 来源：fill 仍从 judge 日志取（symbol 经 `to_internal` 归一）；side 默认 long（脚本本就只跟踪 long 边缘单，RE_DECISION 已筛 long）。

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_reviewer_symbol_canonical.py -k settle -v`
Expected: PASS

- [ ] **Step 5: 真跑验证 + Commit**

Run: `python3 scripts/track_marginal60.py`
Expected: 原未结算的 ETH/UNI/XRP 现已结算（从 lifecycle）；XLM 用权威 −10.09；不报错。

```bash
git add scripts/track_marginal60.py tests/test_reviewer_symbol_canonical.py
git commit -m "feat(marginal-settle): track_marginal60 结算源改读权威 lifecycle.json + 归一 join"
```

---

## Task 3: 回归 + 真跑

**Files:** 无（验证 only）

- [ ] **Step 1: reviewer 相关既有测试不回归**

Run: `python3 -m pytest tests/test_reviewer_symbol_canonical.py test_external_close_final_cause.py test_pnl_resolved_event_contract.py test_phase15_observability.py test_execution_result_contract.py -v`
Expected: PASS（新用例 + reviewer/pnl_resolution/segmented metrics 既有测试零回归）

- [ ] **Step 2: 编译 + 全量回归**

Run: `python3 -m compileall -q agents/trading/reviewer.py scripts/track_marginal60.py && python3 -m pytest -q`
Expected: PASS 数 ≥ 1338 基线 + 新用例；8 failed 仅既有 round2 asyncio flaky，零新退化

- [ ] **Step 3: Commit (若有收尾)**

```bash
git add -A
git commit -m "test(reviewer-symbol): 全量回归零退化 + reviewer 既有测试不回归" || echo "nothing to commit"
```

---

## Task 4: 更新 CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 追加约定/红线**

在 `CLAUDE.md` 合适位置（消息契约红线 symbol 约定附近，或风控红线）追加：

```
- Reviewer trade record / `[复盘] 记录交易` 日志的 symbol 必须经 `utils/symbol.py::to_internal` 归一为内部 `BASE-USDT`（2026-06-20 `fix-reviewer-symbol-format-and-marginal-settle`）：reviewer 入口 3 处 `symbol = msg.get('symbol') or payload.get('symbol')` 套 `to_internal` 收口，防上游 leak 的 `-SWAP`/ccxt 格式污染 trade_history 与下游分桶/工具（实证致 `track_marginal60.py` 配对失败 8 单未结算）。`track_marginal60.py` 结算源改读权威 `data/live_position_lifecycle.json` 的 `total_realized_pnl`（统一键 + reconcile 后值），fill 与 lifecycle 都经 `to_internal` 归一后按 symbol+side+opened_at≈fill_ts 容差 join。不回填历史 `trade_history.json`。
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(reviewer-symbol): CLAUDE.md 加 reviewer symbol 归一约定 + tracker 读 lifecycle"
```

---

## Self-Review 结论

- **Spec coverage**：delta spec 2 requirement 全覆盖——reviewer symbol 归一(Task 1)、边缘单从 lifecycle 结算(Task 2)。
- **Placeholder scan**：无 TBD；代码 step 含完整代码。两处"以实读为准"是给实施者的精确定位指令（reviewer 入口名/行号、track_marginal60 结算段位置）。
- **Type consistency**：`to_internal(symbol)` / `settle_fill_from_lifecycle(sym_internal, side, fill_ts, lifecycle, used_keys, tol)` / `load_lifecycle()` 跨任务一致。
