---
change: counterfactual-replay-foundation
design-doc: docs/superpowers/specs/2026-06-13-counterfactual-replay-foundation-design.md
base-ref: 7ea92f6078657a39f696ca3bb6d534f978d782dc
archived-with: 2026-06-13-counterfactual-replay-foundation
---

# Counterfactual Replay Foundation 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付反事实策略实验室的 L1 + 原料地基——决策磁带埋点、可信被拒单净 PnL、1s tick 采集、诚实性 gate，全部 observability-only write-only。

**Architecture:** 4 个独立单元（`utils/decision_tape.py` / 独立 1s 采集→`klines_1s.db` / `utils/counterfactual_pnl.py` / 报表层诚实 gate），经 `state_paths` + `config_loader` 接线，复用 `CostModel`，feature flag 全关即回到现状。

**Tech Stack:** Python 3, sqlite3, pytest, 现有 utils（cost_model/state_paths/config_loader/counterfactual_ledger）。

**红线（贯穿全程）:** observability-only write-only——任何 gate/veto/halt/rank/daily-stop 严禁读决策磁带/反事实 PnL/tick；Task 8 守卫测试强制。零回归：flag 全关 == 现状，基线 1149 不降。

archived-with: 2026-06-13-counterfactual-replay-foundation
---

## Task 1: 配置项与状态路径接线

**Files:**
- Modify: `utils/config_loader.py`（DEFAULTS / HARD_LIMITS / env_map）
- Modify: `utils/state_paths.py`（StatePaths dataclass + for_namespace）
- Test: `tests/test_cf_foundation_config.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cf_foundation_config.py
from utils.config_loader import DEFAULTS, HARD_LIMITS
from utils.state_paths import get_state_paths


def test_cf_config_defaults():
    assert DEFAULTS["decision_tape_enabled"] is True
    assert DEFAULTS["tick_capture_enabled"] is True
    assert DEFAULTS["cf_min_sample"] == 30
    assert DEFAULTS["cf_lowconf_sample"] == 100
    assert DEFAULTS["decision_tape_retention_days"] == 90
    assert DEFAULTS["tick_capture_retention_days"] == 30


def test_cf_hard_limits():
    assert HARD_LIMITS["cf_min_sample"] == (1, 1000)
    assert HARD_LIMITS["cf_lowconf_sample"] == (1, 5000)


def test_state_paths_new_files_live():
    sp = get_state_paths("live", refresh=True)
    assert sp.decision_replay_tape == "data/decision_replay_tape.jsonl"
    assert sp.klines_1s == "data/klines_1s.db"


def test_state_paths_new_files_testnet():
    sp = get_state_paths("testnet", refresh=True)
    assert sp.decision_replay_tape == "data/testnet_decision_replay_tape.jsonl"
    assert sp.klines_1s == "data/testnet_klines_1s.db"
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_cf_foundation_config.py -q`
Expected: FAIL（KeyError / AttributeError）

- [ ] **Step 3: 加配置项**（`utils/config_loader.py`）

在 `DEFAULTS` 追加：
```python
    "decision_tape_enabled": True,
    "tick_capture_enabled": True,
    "cf_min_sample": 30,
    "cf_lowconf_sample": 100,
    "decision_tape_retention_days": 90,
    "tick_capture_retention_days": 30,
```
在 `HARD_LIMITS` 追加：
```python
    "cf_min_sample": (1, 1000),
    "cf_lowconf_sample": (1, 5000),
    "decision_tape_retention_days": (1, 3650),
    "tick_capture_retention_days": (1, 3650),
```
在 `_read_env_overrides` 的 `env_map` 追加：
```python
        "DECISION_TAPE_ENABLED": ("decision_tape_enabled", _to_bool),
        "TICK_CAPTURE_ENABLED": ("tick_capture_enabled", _to_bool),
        "CF_MIN_SAMPLE": ("cf_min_sample", int),
        "CF_LOWCONF_SAMPLE": ("cf_lowconf_sample", int),
        "DECISION_TAPE_RETENTION_DAYS": ("decision_tape_retention_days", int),
        "TICK_CAPTURE_RETENTION_DAYS": ("tick_capture_retention_days", int),
```

- [ ] **Step 4: 加状态路径**（`utils/state_paths.py`）

在 `StatePaths` dataclass 字段末尾追加：
```python
    decision_replay_tape: str
    klines_1s: str
```
在 `for_namespace` 的 `return cls(...)` 追加：
```python
        decision_replay_tape=f'data/{p}decision_replay_tape.jsonl',
        klines_1s=f'data/{p}klines_1s.db',
```

- [ ] **Step 5: 运行通过**

Run: `python3 -m pytest tests/test_cf_foundation_config.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add utils/config_loader.py utils/state_paths.py tests/test_cf_foundation_config.py
git commit -m "feat(cf): config + state paths for replay foundation (decision tape / tick / honesty gate)"
```

archived-with: 2026-06-13-counterfactual-replay-foundation
---

## Task 2: 决策磁带 writer（utils/decision_tape.py）

**Files:**
- Create: `utils/decision_tape.py`
- Test: `tests/test_decision_tape.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_decision_tape.py
import json, os, time
from utils.decision_tape import DecisionTape, build_bundle


def _read(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def test_accept_record_written(tmp_path):
    p = str(tmp_path / "tape.jsonl")
    dt = DecisionTape(path=p, enabled=True)
    b = build_bundle(symbol="BTC-USDT", decision="accept", request_id="20260613-BTC-aa11",
                     tech_analysis={"momentum": {"rsi": 55}}, price_at_decision=50000.0,
                     regime_state="bullish", llm_output={"action": "open_long", "confidence": 70},
                     llm_audit_ref="aud-1", trade_decision_output={"plan": {"leverage": 5}})
    dt.record_decision(b)
    rows = _read(p)
    assert rows[0]["decision"] == "accept"
    assert rows[0]["symbol"] == "BTC-USDT"
    assert rows[0]["llm_output_inline"]["action"] == "open_long"
    assert rows[0]["schema_version"] == "decision_replay_record.v1"


def test_reject_record_written(tmp_path):
    p = str(tmp_path / "tape.jsonl")
    dt = DecisionTape(path=p, enabled=True)
    b = build_bundle(symbol="ETH-USDT", decision="reject", request_id="r2",
                     tech_analysis={}, price_at_decision=3000.0, regime_state="choppy",
                     llm_output=None, llm_audit_ref=None,
                     trade_decision_output={"reject_reason": "rr_below_floor:1.2"})
    dt.record_decision(b)
    rows = _read(p)
    assert rows[0]["decision"] == "reject"
    assert rows[0]["llm_output_inline"] is None
    assert rows[0]["trade_decision_output"]["reject_reason"] == "rr_below_floor:1.2"


def test_writer_failure_does_not_raise(tmp_path):
    # 不可写路径：record_decision 不得抛
    dt = DecisionTape(path="/nonexistent_dir_xyz/tape.jsonl", enabled=True)
    b = build_bundle(symbol="BTC-USDT", decision="accept", request_id="r3",
                     tech_analysis={}, price_at_decision=1.0, regime_state="x",
                     llm_output=None, llm_audit_ref=None, trade_decision_output={})
    dt.record_decision(b)  # must not raise
    assert dt.drop_count == 1


def test_flag_off_writes_nothing(tmp_path):
    p = str(tmp_path / "tape.jsonl")
    dt = DecisionTape(path=p, enabled=False)
    b = build_bundle(symbol="BTC-USDT", decision="accept", request_id="r4",
                     tech_analysis={}, price_at_decision=1.0, regime_state="x",
                     llm_output=None, llm_audit_ref=None, trade_decision_output={})
    dt.record_decision(b)
    assert not os.path.exists(p)


def test_retention_prunes_old(tmp_path):
    p = str(tmp_path / "tape.jsonl")
    dt = DecisionTape(path=p, enabled=True, retention_days=1)
    old = build_bundle(symbol="A-USDT", decision="accept", request_id="old",
                       tech_analysis={}, price_at_decision=1.0, regime_state="x",
                       llm_output=None, llm_audit_ref=None, trade_decision_output={})
    old["timestamp"] = time.time() - 3 * 86400  # 3 天前
    dt._append_raw(old)
    fresh = build_bundle(symbol="B-USDT", decision="accept", request_id="new",
                         tech_analysis={}, price_at_decision=1.0, regime_state="x",
                         llm_output=None, llm_audit_ref=None, trade_decision_output={})
    dt.record_decision(fresh)  # 触发 prune
    rows = _read(p)
    ids = {r["request_id"] for r in rows}
    assert "new" in ids and "old" not in ids
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_decision_tape.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 writer**

```python
# utils/decision_tape.py
"""决策磁带：Judge 决策点全量输入+输出 bundle 的 append-only 落盘。
observability-only write-only —— 严禁任何交易决策路径读取本模块产物。"""
import json
import os
import time
import logging

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "decision_replay_record.v1"


def build_bundle(*, symbol, decision, request_id, tech_analysis, price_at_decision,
                 regime_state, llm_output, llm_audit_ref, trade_decision_output):
    """构建一条 decision_replay_record。llm_output 内联（self-contained），
    llm_audit_ref 仅 best-effort 指针。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "timestamp": time.time(),
        "symbol": symbol,
        "decision": decision,                  # "accept" | "reject"
        "tech_analysis": tech_analysis,         # 9 维全量快照
        "price_at_decision": price_at_decision,
        "regime_state": regime_state,
        "llm_output_inline": llm_output,        # parsed LLM 输出 or None
        "llm_audit_ref": llm_audit_ref,         # 7 天内可取原始 prompt or None
        "trade_decision_output": trade_decision_output,
    }


class DecisionTape:
    def __init__(self, path, enabled=True, retention_days=90):
        self.path = path
        self.enabled = enabled
        self.retention_days = retention_days
        self.drop_count = 0

    def record_decision(self, bundle):
        if not self.enabled:
            return
        try:
            self._maybe_prune()
            self._append_raw(bundle)
        except Exception as e:  # fail-safe：绝不抛进调用方（Judge 决策路径）
            self.drop_count += 1
            logger.warning(f"[DecisionTape] drop (#{self.drop_count}): {e}")

    def _append_raw(self, bundle):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps(bundle, ensure_ascii=False) + "\n")

    def _maybe_prune(self):
        if not os.path.exists(self.path):
            return
        cutoff = time.time() - self.retention_days * 86400
        rows = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("timestamp", 0) >= cutoff:
                    rows.append(line)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            for line in rows:
                f.write(line + "\n")
        os.replace(tmp, self.path)
```

> 注：`_maybe_prune` 每次写都全量重写，规模大时可优化为按天/按大小触发；L1 先求正确，体积监控见 Task 10。

- [ ] **Step 4: 运行通过**

Run: `python3 -m pytest tests/test_decision_tape.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add utils/decision_tape.py tests/test_decision_tape.py
git commit -m "feat(cf): decision tape writer (self-contained LLM inline, fail-safe, retention)"
```

archived-with: 2026-06-13-counterfactual-replay-foundation
---

## Task 3: Judge 决策点接线（accept + reject）

**Files:**
- Modify: `agents/trading/judge.py`（init 装 DecisionTape；accept 发布点 line ~1969；`_record_rejected_plan` line ~2963）
- Test: `tests/test_judge_decision_tape_wiring.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_judge_decision_tape_wiring.py
# 验证 Judge 在 accept 发布与 reject 记录两处调用 decision tape，且关停时不调用。
import inspect
from agents.trading import judge as judge_mod


def test_judge_imports_decision_tape():
    src = inspect.getsource(judge_mod)
    assert "decision_tape" in src or "DecisionTape" in src
    # accept 与 reject 两处都接线
    assert src.count("record_decision") >= 2
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_judge_decision_tape_wiring.py -q`
Expected: FAIL（record_decision 不在源码）

- [ ] **Step 3: Judge 接线**

在 Judge `__init__`（counterfactual_ledger 初始化附近）加：
```python
from utils.decision_tape import DecisionTape, build_bundle
from utils.state_paths import get_state_paths
self._decision_tape = DecisionTape(
    path=get_state_paths().decision_replay_tape,
    enabled=self._config.get("decision_tape_enabled", True),
    retention_days=self._config.get("decision_tape_retention_days", 90),
)
```
在 accept 发布点（`await self.publish("trade_decision", decision, symbol=symbol)` 之前，line ~1969）加：
```python
self._decision_tape.record_decision(build_bundle(
    symbol=symbol, decision="accept", request_id=req_id,
    tech_analysis=tech, price_at_decision=price,
    regime_state=self._regime_manager._effective_regime,
    llm_output=getattr(self, "_last_llm_output", None),
    llm_audit_ref=getattr(self, "_last_llm_audit_ref", None),
    trade_decision_output={"plan": decision.get("plan"),
                           "attribution": decision.get("attribution")},
))
```
在 `_record_rejected_plan` 内（落 ledger 之后）加：
```python
self._decision_tape.record_decision(build_bundle(
    symbol=symbol, decision="reject", request_id=(attr or {}).get("request_id"),
    tech_analysis=(plan or {}).get("_tech_snapshot") or {},
    price_at_decision=(plan or {}).get("entry_price"),
    regime_state=regime,
    llm_output=getattr(self, "_last_llm_output", None),
    llm_audit_ref=getattr(self, "_last_llm_audit_ref", None),
    trade_decision_output={"reject_reason": reason, "attribution": attr},
))
```

> 注：`_last_llm_output` / `_last_llm_audit_ref` 在 `_ask_llm` 返回处缓存（若不存在则加一行 `self._last_llm_output = result; self._last_llm_audit_ref = <audit_id>`）。reject 路径若手头无完整 tech 快照，先用 plan 内可得字段，缺失 fail-safe 空 dict（不阻断）。

- [ ] **Step 4: 运行通过 + 不回归 Judge 现有测试**

Run: `python3 -m pytest tests/test_judge_decision_tape_wiring.py -q && python3 -m pytest tests/ -q -k judge`
Expected: PASS，judge 相关测试不回归

- [ ] **Step 5: 提交**

```bash
git add agents/trading/judge.py tests/test_judge_decision_tape_wiring.py
git commit -m "feat(cf): wire decision tape at Judge accept publish + reject record"
```

archived-with: 2026-06-13-counterfactual-replay-foundation
---

## Task 4: 1s tick 采集 → klines_1s.db

**Files:**
- Create: `utils/tick_capture.py`
- Test: `tests/test_tick_capture.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_tick_capture.py
import os, sqlite3
from utils.tick_capture import OneSecBarStore


def test_writes_1s_bar(tmp_path):
    db = str(tmp_path / "klines_1s.db")
    store = OneSecBarStore(db_path=db, enabled=True)
    store.record_bar("BTC-USDT", open_time_ms=1_700_000_000_000,
                     o=100, h=101, l=99, c=100.5, v=12.3)
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT symbol, interval, open, high, low, close FROM klines").fetchall()
    conn.close()
    assert rows == [("BTC-USDT", "1s", 100.0, 101.0, 99.0, 100.5)]


def test_upsert_dedup(tmp_path):
    db = str(tmp_path / "klines_1s.db")
    store = OneSecBarStore(db_path=db, enabled=True)
    store.record_bar("BTC-USDT", 1_700_000_000_000, 100, 101, 99, 100.5, 1)
    store.record_bar("BTC-USDT", 1_700_000_000_000, 100, 102, 98, 100.7, 2)  # 同 open_time
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0]
    last = conn.execute("SELECT high FROM klines").fetchone()[0]
    conn.close()
    assert n == 1 and last == 102.0  # INSERT OR REPLACE


def test_flag_off_no_db(tmp_path):
    db = str(tmp_path / "klines_1s.db")
    store = OneSecBarStore(db_path=db, enabled=False)
    store.record_bar("BTC-USDT", 1_700_000_000_000, 100, 101, 99, 100.5, 1)
    assert not os.path.exists(db)


def test_failure_isolated(tmp_path):
    store = OneSecBarStore(db_path="/nonexistent_xyz/k.db", enabled=True)
    store.record_bar("BTC-USDT", 1, 1, 1, 1, 1, 1)  # must not raise
    assert store.drop_count == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_tick_capture.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现（复用 kline schema pattern）**

```python
# utils/tick_capture.py
"""独立 1 秒聚合 bar 采集 → klines_1s.db（interval='1s'）。
复用 kline schema，写独立 db 不污染主 klines.db。
observability-only：仅供反事实回放价格精度，严禁交易决策读取。"""
import os
import sqlite3
import logging

logger = logging.getLogger(__name__)


class OneSecBarStore:
    def __init__(self, db_path, enabled=True):
        self.db_path = db_path
        self.enabled = enabled
        self.drop_count = 0
        if self.enabled:
            try:
                self._init_db()
            except Exception as e:
                logger.warning(f"[TickCapture] init failed: {e}")

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS klines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL, interval TEXT NOT NULL,
                    open_time INTEGER NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                    close REAL NOT NULL, volume REAL NOT NULL,
                    UNIQUE(symbol, interval, open_time)
                )''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_sit ON klines(symbol, interval, open_time)')
            conn.commit()
        finally:
            conn.close()

    def record_bar(self, symbol, open_time_ms, o, h, l, c, v=0.0):
        if not self.enabled:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute('''INSERT OR REPLACE INTO klines
                    (symbol, interval, open_time, open, high, low, close, volume)
                    VALUES (?, '1s', ?, ?, ?, ?, ?, ?)''',
                    (symbol, open_time_ms, float(o), float(h), float(l), float(c), float(v)))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            self.drop_count += 1
            logger.warning(f"[TickCapture] drop (#{self.drop_count}): {e}")
```

> 注：采集源接线（从 collector 的 price_tick 聚合成 1s bar）在 Task 7 集成；本 task 只交付独立可测存储单元。retention 清理复用按天 prune（与决策磁带同 pattern，集成时配 `tick_capture_retention_days`）。

- [ ] **Step 4: 运行通过**

Run: `python3 -m pytest tests/test_tick_capture.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add utils/tick_capture.py tests/test_tick_capture.py
git commit -m "feat(cf): 1s aggregated bar store -> klines_1s.db (isolated, fail-safe)"
```

archived-with: 2026-06-13-counterfactual-replay-foundation
---

## Task 5: 反事实 PnL 引擎（utils/counterfactual_pnl.py）

**Files:**
- Create: `utils/counterfactual_pnl.py`
- Test: `tests/test_counterfactual_pnl.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_counterfactual_pnl.py
from utils.counterfactual_pnl import resolve_counterfactual, CfResult


def _rec(**kw):
    base = dict(symbol="BTC-USDT", side="long", entry_price=100.0, stop_loss=95.0,
                take_profit=[110.0], leverage=5, size_usdt=30.0,
                created_at=1000.0, funding_rate=0.0001)
    base.update(kw); return base


def _bars(seq):
    # seq: list of (open_time_ms, high, low) -> close 取 (h+l)/2
    return [{"open_time": t, "high": h, "low": l, "close": (h + l) / 2} for t, h, l in seq]


def test_single_tp_hit_net_usdt_positive():
    rec = _rec()
    bars = _bars([(1_001_000, 111, 109)])  # high>=TP 110, low 不破 SL
    r = resolve_counterfactual(rec, bars)
    assert r.outcome == "tp"
    assert r.price_ambiguous is False
    assert r.net_usdt > 0  # 扣费后仍为正（10% 毛利 * 5x notional）


def test_single_sl_hit_net_usdt_negative():
    rec = _rec()
    bars = _bars([(1_001_000, 99, 94)])  # low<=SL 95
    r = resolve_counterfactual(rec, bars)
    assert r.outcome == "sl"
    assert r.net_usdt < 0


def test_same_bar_conflict_takes_sl_first():
    rec = _rec()
    bars = _bars([(1_001_000, 111, 94)])  # 同根：high 触 TP 且 low 触 SL
    r = resolve_counterfactual(rec, bars)
    assert r.outcome == "sl"            # 保守 SL-first
    assert r.price_ambiguous is True


def test_expired_mark_to_market():
    rec = _rec()
    bars = _bars([(1_001_000, 101, 99)])  # 不触发；24h 后过期
    r = resolve_counterfactual(rec, bars, max_hold_sec=86400)
    assert r.outcome == "expired"


def test_funding_flagged_approx():
    rec = _rec()
    bars = _bars([(1_001_000, 111, 109)])
    r = resolve_counterfactual(rec, bars)
    assert r.funding_approx is True


def test_short_side_symmetry():
    rec = _rec(side="short", stop_loss=105.0, take_profit=[90.0])
    bars = _bars([(1_001_000, 91, 89)])  # low<=TP 90
    r = resolve_counterfactual(rec, bars)
    assert r.outcome == "tp" and r.net_usdt > 0
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_counterfactual_pnl.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现（复用 CostModel）**

```python
# utils/counterfactual_pnl.py
"""被拒单反事实净 PnL：CostModel 真实成本 + K 线 SL/TP 触发判定 +
同根 SL-first 保守 + 偏差带 + 资金费近似标注。
observability-only：严禁交易决策读取。"""
from dataclasses import dataclass
from typing import Optional, List
from utils.cost_model import get_default_cost_model


@dataclass
class CfResult:
    outcome: str            # "tp" | "sl" | "expired"
    exit_price: float
    gross_return_pct: float
    net_usdt: Optional[float]
    net_return_pct: float
    price_ambiguous: bool
    funding_approx: bool
    hold_hours: float
    source: str             # "attribution_reconstructed" | "tape_exact"


def resolve_counterfactual(record: dict, bars: List[dict], *, max_hold_sec: int = 86400,
                           source: str = "attribution_reconstructed",
                           cost_model=None) -> CfResult:
    cm = cost_model or get_default_cost_model()
    side = record["side"]
    entry = float(record["entry_price"])
    sl = float(record.get("stop_loss") or 0)
    tp_list = record.get("take_profit") or []
    tp = float(tp_list[0]) if tp_list else 0
    created = float(record.get("created_at", 0))

    outcome, exit_price, ambiguous, resolved_t = "expired", entry, False, created
    for bar in bars:
        if (bar["open_time"] / 1000.0) - created > max_hold_sec:
            break
        hi, lo = float(bar["high"]), float(bar["low"])
        hit_sl = sl and (lo <= sl if side == "long" else hi >= sl)
        hit_tp = tp and (hi >= tp if side == "long" else lo <= tp)
        if hit_sl and hit_tp:                 # 同根冲突 → SL-first 保守
            outcome, exit_price, ambiguous = "sl", sl, True
            resolved_t = bar["open_time"] / 1000.0
            break
        if hit_sl:
            outcome, exit_price = "sl", sl
            resolved_t = bar["open_time"] / 1000.0
            break
        if hit_tp:
            outcome, exit_price = "tp", tp
            resolved_t = bar["open_time"] / 1000.0
            break
        exit_price = float(bar["close"])      # 过期 mark-to-market
        resolved_t = bar["open_time"] / 1000.0

    if side == "long":
        gross_pct = (exit_price - entry) / entry if entry else 0.0
    else:
        gross_pct = (entry - exit_price) / entry if entry else 0.0

    leverage = float(record.get("leverage") or 1)
    size_usdt = record.get("size_usdt")
    funding_rate = float(record.get("funding_rate") or 0.0)
    hold_hours = max(0.0, (resolved_t - created) / 3600.0)

    net_usdt = None
    if size_usdt is not None:
        notional = float(size_usdt) * leverage
        gross_usdt = notional * gross_pct
        cost = cm.round_trip_cost(notional=notional, funding_rate=funding_rate,
                                  hold_hours=hold_hours, side=side)
        net_usdt = gross_usdt - cost["total_cost"]
        net_return_pct = net_usdt / float(size_usdt) if size_usdt else gross_pct
    else:
        net_return_pct = gross_pct  # 旧数据缺 size：退化毛 return%

    return CfResult(outcome=outcome, exit_price=exit_price,
                    gross_return_pct=gross_pct * 100,
                    net_usdt=net_usdt, net_return_pct=net_return_pct * 100,
                    price_ambiguous=ambiguous, funding_approx=(funding_rate != 0.0),
                    hold_hours=hold_hours, source=source)
```

> 注：`net_usdt` 仅在 record 含 `size_usdt` 时可算（新磁带/新被拒单带；旧 jsonl 缺则退化 `net_return_pct`）。资金费用 `funding_rate` 决策时点拍平近似，`funding_approx` 标注。

- [ ] **Step 4: 运行通过**

Run: `python3 -m pytest tests/test_counterfactual_pnl.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add utils/counterfactual_pnl.py tests/test_counterfactual_pnl.py
git commit -m "feat(cf): counterfactual PnL engine (CostModel + SL-first + bias band + funding approx)"
```

archived-with: 2026-06-13-counterfactual-replay-foundation
---

## Task 6: 诚实性 gate（Wilson + bootstrap + 三档）

**Files:**
- Create: `utils/cf_honesty_gate.py`
- Test: `tests/test_cf_honesty_gate.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cf_honesty_gate.py
from utils.cf_honesty_gate import summarize_bucket


def test_thin_sample_refuses():
    v = summarize_bucket(wins=3, losses=2, net_usdt_samples=[1, -1, 2, -1, 0],
                         min_sample=30, lowconf_sample=100)
    assert v["verdict"] == "INSUFFICIENT_SAMPLE"
    assert "direction" not in v or v.get("direction") is None


def test_mid_sample_low_confidence():
    n = 50
    v = summarize_bucket(wins=30, losses=20, net_usdt_samples=[1.0] * 30 + [-1.0] * 20,
                         min_sample=30, lowconf_sample=100)
    assert v["verdict"] == "low_confidence"
    assert "win_rate_ci" in v and "net_pnl_ci" in v


def test_actionable_when_ci_excludes_zero():
    # 120 笔，净 PnL 稳定为正且窄区间 → actionable
    samples = [2.0] * 80 + [1.0] * 40
    v = summarize_bucket(wins=120, losses=0, net_usdt_samples=samples,
                         min_sample=30, lowconf_sample=100)
    assert v["verdict"] == "actionable"
    assert v["net_pnl_ci"][0] > 0  # 区间下界 > 0


def test_single_trade_dominance_not_actionable():
    # 120 笔但净值几乎全靠 1 笔暴利 → bootstrap CI 跨 0 → 非 actionable
    samples = [ -0.1 ] * 119 + [ 500.0 ]
    v = summarize_bucket(wins=1, losses=119, net_usdt_samples=samples,
                         min_sample=30, lowconf_sample=100)
    assert v["verdict"] != "actionable"
    assert v["net_pnl_ci"][0] <= 0 <= v["net_pnl_ci"][1]


def test_wilson_handles_extreme():
    v = summarize_bucket(wins=100, losses=0, net_usdt_samples=[1.0] * 100,
                         min_sample=30, lowconf_sample=100)
    lo, hi = v["win_rate_ci"]
    assert 0.0 <= lo <= hi <= 1.0 and lo < 1.0  # Wilson 不爆到 1.0
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_cf_honesty_gate.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现（单点收口；确定性 bootstrap 用固定种子）**

```python
# utils/cf_honesty_gate.py
"""诚实性 gate：胜率 Wilson 区间 + 净 PnL bootstrap 区间 + 三档样本量。
所有方向/PnL 结论的单一收口；薄样本拒答，防过拟合噪声。
observability-only。"""
import math
import random

_Z = 1.96  # 95%


def wilson_interval(wins: int, n: int, z: float = _Z):
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def bootstrap_mean_ci(samples, iters: int = 2000, seed: int = 1234, z: float = _Z):
    if not samples:
        return (0.0, 0.0)
    rng = random.Random(seed)  # 固定种子 → 确定性，可测
    n = len(samples)
    means = []
    for _ in range(iters):
        s = sum(samples[rng.randrange(n)] for _ in range(n)) / n
        means.append(s)
    means.sort()
    lo = means[int(0.025 * iters)]
    hi = means[int(0.975 * iters) - 1]
    return (lo, hi)


def summarize_bucket(*, wins: int, losses: int, net_usdt_samples,
                     min_sample: int = 30, lowconf_sample: int = 100):
    n = wins + losses
    wr_ci = wilson_interval(wins, n)
    pnl_ci = bootstrap_mean_ci(list(net_usdt_samples))
    out = {
        "n": n,
        "win_rate": (wins / n) if n else 0.0,
        "win_rate_ci": wr_ci,
        "net_pnl_mean": (sum(net_usdt_samples) / len(net_usdt_samples)) if net_usdt_samples else 0.0,
        "net_pnl_ci": pnl_ci,
    }
    if n < min_sample:
        out["verdict"] = "INSUFFICIENT_SAMPLE"
        out["direction"] = None
    elif n < lowconf_sample:
        out["verdict"] = "low_confidence"
    else:
        actionable = pnl_ci[0] > 0 or pnl_ci[1] < 0  # CI 不跨 0
        out["verdict"] = "actionable" if actionable else "inconclusive"
    return out
```

- [ ] **Step 4: 运行通过**

Run: `python3 -m pytest tests/test_cf_honesty_gate.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add utils/cf_honesty_gate.py tests/test_cf_honesty_gate.py
git commit -m "feat(cf): honesty gate (Wilson + deterministic bootstrap + 3-tier sample)"
```

archived-with: 2026-06-13-counterfactual-replay-foundation
---

## Task 7: 集成——tick 采集接线 + 被拒单报表

**Files:**
- Modify: `agents/trading/multi_data_collector.py`（price_tick → 1s bar 聚合喂 OneSecBarStore）或独立订阅；按现有 collector pattern
- Modify: `replay_report.py`（被拒单按 gate×regime×source 分桶 + counterfactual_pnl + honesty gate + 偏差带）
- Test: `tests/test_cf_replay_report.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cf_replay_report.py
from replay_report import build_cf_report


def test_cf_report_buckets_and_gate():
    # 构造一批已解析被拒单结果，验证报表按桶聚合并经诚实 gate
    rows = [{"reject_reason": "rr_below_floor", "effective_regime": "choppy",
             "side": "long", "outcome": "tp", "net_usdt": 2.0,
             "price_ambiguous": False, "source": "tape_exact"}] * 5
    rep = build_cf_report(rows, min_sample=30, lowconf_sample=100)
    bucket = rep["buckets"]["rr_below_floor|choppy|long"]
    assert bucket["verdict"] == "INSUFFICIENT_SAMPLE"  # 5 笔 < 30
    assert "bias_band" in bucket  # 偏差带随报告


def test_cf_report_bias_band_counts_ambiguous():
    rows = [{"reject_reason": "ev_gate", "effective_regime": "bullish", "side": "long",
             "outcome": "sl", "net_usdt": -1.0, "price_ambiguous": True,
             "source": "attribution_reconstructed"}] * 3
    rep = build_cf_report(rows, min_sample=1, lowconf_sample=2)
    bucket = rep["buckets"]["ev_gate|bullish|long"]
    assert bucket["bias_band"]["ambiguous_count"] == 3
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_cf_replay_report.py -q`
Expected: FAIL（ImportError: build_cf_report）

- [ ] **Step 3: 实现报表聚合**

在 `replay_report.py` 追加：
```python
from utils.cf_honesty_gate import summarize_bucket


def build_cf_report(resolved_rows, *, min_sample=30, lowconf_sample=100):
    """按 reject_reason|regime|side 分桶，每桶过诚实 gate + 偏差带。
    resolved_rows: 每条含 reject_reason/effective_regime/side/outcome/net_usdt/
                   price_ambiguous/source。observability-only。"""
    from collections import defaultdict
    groups = defaultdict(list)
    for r in resolved_rows:
        key = f"{r.get('reject_reason')}|{r.get('effective_regime')}|{r.get('side')}"
        groups[key].append(r)
    buckets = {}
    for key, rows in groups.items():
        wins = sum(1 for r in rows if r.get("outcome") == "tp")
        losses = sum(1 for r in rows if r.get("outcome") == "sl")
        samples = [r["net_usdt"] for r in rows if r.get("net_usdt") is not None]
        verdict = summarize_bucket(wins=wins, losses=losses, net_usdt_samples=samples,
                                   min_sample=min_sample, lowconf_sample=lowconf_sample)
        ambiguous = sum(1 for r in rows if r.get("price_ambiguous"))
        verdict["bias_band"] = {
            "ambiguous_count": ambiguous,
            "ambiguous_pct": (ambiguous / len(rows)) if rows else 0.0,
        }
        verdict["sources"] = sorted({r.get("source") for r in rows})
        buckets[key] = verdict
    return {"buckets": buckets, "total": len(resolved_rows)}
```

tick 采集接线（collector）：在 `multi_data_collector` 装 `OneSecBarStore`（flag/path/retention 从 config + state_paths），在 price_tick 处理处把价聚合成 1s bar 调 `record_bar`（按现有 collector 的 tick 处理 pattern，秒边界 flush）。

- [ ] **Step 4: 运行通过**

Run: `python3 -m pytest tests/test_cf_replay_report.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add replay_report.py agents/trading/multi_data_collector.py tests/test_cf_replay_report.py
git commit -m "feat(cf): rejected-signal report (buckets + honesty gate + bias band) + 1s tick wiring"
```

archived-with: 2026-06-13-counterfactual-replay-foundation
---

## Task 8: 红线守卫测试（observability-only）

**Files:**
- Test: `tests/test_cf_red_line_guard.py`

- [ ] **Step 1: 写测试（直接即最终断言）**

```python
# tests/test_cf_red_line_guard.py
"""红线：任何交易决策路径严禁读决策磁带/反事实 PnL/tick 产物。"""
import inspect


def _src(modpath):
    mod = __import__(modpath, fromlist=["x"])
    return inspect.getsource(mod)


def test_judge_does_not_read_cf_products():
    src = _src("agents.trading.judge")
    # 决策可以 *写* 磁带（record_decision），但不得 *读* 反事实 PnL / honesty gate / tick
    assert "counterfactual_pnl" not in src
    assert "cf_honesty_gate" not in src
    assert "OneSecBarStore" not in src
    assert "klines_1s" not in src


def test_executor_does_not_read_cf_products():
    for mp in ["agents.trading.executor", "executor",
               "agents.trading.portfolio_risk_guard", "agents.trading.reviewer"]:
        src = _src(mp)
        assert "counterfactual_pnl" not in src, mp
        assert "cf_honesty_gate" not in src, mp
        assert "decision_replay_tape" not in src, mp
        assert "klines_1s" not in src, mp


def test_halt_and_riskguard_do_not_read_tape():
    for mp in ["utils.halt_state", "agents.trading.portfolio_risk_guard"]:
        src = _src(mp)
        assert "decision_tape" not in src, mp
        assert "DecisionTape" not in src, mp
```

- [ ] **Step 2: 运行通过**

Run: `python3 -m pytest tests/test_cf_red_line_guard.py -q`
Expected: PASS（3 passed）。若失败说明某决策路径误读了 CF 产物，必须移除。

- [ ] **Step 3: 提交**

```bash
git add tests/test_cf_red_line_guard.py
git commit -m "test(cf): red-line guard — decision/risk paths must not read CF products"
```

archived-with: 2026-06-13-counterfactual-replay-foundation
---

## Task 9: 文档与红线声明

**Files:**
- Modify: `CLAUDE.md`（风控红线追加 observability-only 声明）
- Modify: `docs/to-do-list.md`（OPEN「shadow-replay 回测器」更新为 #1 进行中 + #2/#3/#4 路线图）

- [ ] **Step 1: CLAUDE.md 追加红线**

在「风控红线」末尾追加一条：
```markdown
- 反事实回放产物（决策磁带 `decision_replay_tape` / 反事实 PnL `utils/counterfactual_pnl.py` / 1s tick `klines_1s.db`）是 **observability-only write-only**（2026-06-13，change `counterfactual-replay-foundation`，与 `data-source-provenance` / `agent-health-supervisor` 同性质）：严禁任何 gate/veto/halt/rank/daily-stop 读取做交易决策；`tests/test_cf_red_line_guard.py` 守卫。决策磁带内联存 parsed LLM 输出（self-contained，抗 llm_audit 7 天过期）；反事实 PnL 复用 executor `CostModel`、同根 K 线 SL/TP 冲突取 SL-first 并量化偏差带、资金费用决策时点 funding_rate 近似标 `funding_approx`；诚实性 gate（Wilson 胜率 + bootstrap 净 PnL + 三档样本，`n<30` 拒答）单点收口于 `utils/cf_honesty_gate.py::summarize_bucket`。详见 design `docs/superpowers/specs/2026-06-13-counterfactual-replay-foundation-design.md`。
```

- [ ] **Step 2: to-do-list.md 更新 OPEN 条目**

把「shadow-replay 回测器」OPEN 行改为：进行中（#1 `counterfactual-replay-foundation` L1+原料地基），并列出后续路线图 #2 L2 全带回放+golden master / #3 L3 组合态扰动 / #4 L4 扫描+置信度门。

- [ ] **Step 3: 提交**

```bash
git add CLAUDE.md docs/to-do-list.md
git commit -m "docs(cf): red-line declaration + to-do-list roadmap for replay lab"
```

archived-with: 2026-06-13-counterfactual-replay-foundation
---

## Task 10: 全量验证与零回归

- [ ] **Step 1: 编译**

Run: `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .`
Expected: exit 0

- [ ] **Step 2: 全量测试**

Run: `python3 -m pytest -q`
Expected: PASS，总数 ≥ 1149 + 新增用例（约 +25），无 failure

- [ ] **Step 3: 零回归确认（flag 全关 == 现状）**

Run: `DECISION_TAPE_ENABLED=false TICK_CAPTURE_ENABLED=false python3 -m pytest -q -k "judge or collector or executor"`
Expected: PASS；确认 flag 关停时无新文件生成、决策行为不变

- [ ] **Step 4: 体积/边界抽查**

确认：磁带 retention prune 生效；klines_1s.db 与主 klines.db 物理隔离；CostModel 单例复用（无第二份成本实现）。

- [ ] **Step 5: 最终提交**

```bash
git add -A
git commit -m "chore(cf): full regression green — replay foundation L1 complete"
```

archived-with: 2026-06-13-counterfactual-replay-foundation
---

## Self-Review 结论

- **Spec 覆盖**：decision-replay-tape（Task 2/3）、counterfactual-pnl 含诚实 gate（Task 5/6）、tick-snapshot-capture（Task 4/7）、红线守卫（Task 8）、retention（Task 2）、source 标注（Task 5）、偏差带（Task 5/7）—— 三份 delta spec 全部有对应 task。
- **类型一致**：`build_bundle`/`DecisionTape.record_decision`/`OneSecBarStore.record_bar`/`resolve_counterfactual→CfResult`/`summarize_bucket`/`build_cf_report` 跨 task 签名一致。
- **无 placeholder**：每步含真实代码与命令。
- **YAGNI**：1s bar 不用逐 trade；honesty gate 用固定种子 bootstrap 保证可测；net_usdt 缺 size 退化 net_return_pct，不强造数据。
