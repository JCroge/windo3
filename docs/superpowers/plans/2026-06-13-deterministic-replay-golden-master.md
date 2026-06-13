---
change: deterministic-replay-golden-master
design-doc: docs/superpowers/specs/2026-06-13-deterministic-replay-golden-master-design.md
base-ref: ad24914fa7e5d75d647ac77c452d80d415926172
archived-with: 2026-06-14-deterministic-replay-golden-master
---

# Deterministic Replay + Golden Master (L2) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 让真实 MultiJudge 代码成为回测引擎——扩磁带存决策时状态快照，建确定性回放 harness 证明 bit 级复现历史决策（golden master），补端到端被拒单报表 driver。

**Architecture:** 决策磁带扩存 ~14 个跨决策状态白名单快照；`utils/decision_replay.py` 用 `MultiJudge.__new__` + 还原状态 + monkeypatch 仅 3 个外部 await（`_update_balance`/`_ask_llm`/`publish`）+ patch `time.time` → 跑真实 `_make_decision` → 截获 publish → 三层比对。observability-only write-only。

**Tech Stack:** Python 3, unittest.mock, asyncio, sqlite3, pytest；复用 L1 的 decision_tape/counterfactual_pnl/build_cf_report 与真实 MultiJudge。

**关键事实（探查确认）:** `_make_decision` 路径外部 await 仅 3 个——`_update_balance`(judge.py:645)、`_ask_llm`(judge.py:1218)、`publish`(judge.py:1993)。决策唯一输出口是 `await self.publish("trade_decision", payload)`，`_make_decision` 返回 None。

**红线（贯穿）:** observability-only write-only；Task 5 守卫。零回归：`DECISION_TAPE_ENABLED=false` == L1 行为，基线 1185 不降。

archived-with: 2026-06-14-deterministic-replay-golden-master
---

## Task 1: 决策状态快照采集

**Files:**
- Modify: `utils/decision_tape.py`（`_jsonable` helper + `build_bundle` 加 `state_snapshot` 参数 + `replayable`）
- Modify: `agents/trading/judge.py`（`_capture_state_snapshot` + accept/reject 接线）
- Test: `tests/test_decision_state_snapshot.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_decision_state_snapshot.py
import json
from utils.decision_tape import build_bundle, _jsonable


def test_jsonable_set_to_sorted_list():
    assert _jsonable({"b", "a"}) == ["a", "b"]
    assert _jsonable({"x": {"c", "a"}}) == {"x": ["a", "c"]}
    assert _jsonable([1, {"a"}]) == [1, ["a"]]


def test_build_bundle_with_snapshot_marks_replayable():
    snap = {"_open_positions": ["BTC-USDT"], "_available_balance": 1000.0}
    b = build_bundle(symbol="BTC-USDT", decision="accept", request_id="r1",
                     tech_analysis={}, price_at_decision=1.0, regime_state="bullish",
                     llm_output=None, llm_audit_ref=None, trade_decision_output={},
                     state_snapshot=snap)
    assert b["state_snapshot_before_decision"] == snap
    assert b["replayable"] is True
    json.dumps(b)  # must be serializable


def test_build_bundle_without_snapshot_not_replayable():
    b = build_bundle(symbol="BTC-USDT", decision="reject", request_id="r2",
                     tech_analysis={}, price_at_decision=1.0, regime_state="x",
                     llm_output=None, llm_audit_ref=None, trade_decision_output={})
    assert b["state_snapshot_before_decision"] is None
    assert b["replayable"] is False


def test_capture_state_snapshot_whitelist():
    # Judge.__new__ + 手设 14 字段，_capture_state_snapshot 返回白名单 dict
    from agents.trading.judge import MultiJudge
    from utils.archetype_cooldown import ArchetypeCooldown
    j = MultiJudge.__new__(MultiJudge)
    j._open_positions = {"BTC-USDT"}
    j._pending_open_symbols = set()
    j._position_slots = {"BTC-USDT": "main"}
    j._pending_open_slots = {}
    ac = ArchetypeCooldown(enabled=True, logger=None)
    ac._history = {"standard": [{"pnl": -1.0, "timestamp": 100.0}]}
    ac._cooldown_until = {"standard": 200.0}
    j._archetype_cooldown = ac
    j._recent_wins = 3
    j._total_completed_trades = 10
    j._recent_win_rate = 0.3
    j._probe_short_active = None
    j._probe_short_sl_count = 1
    j._probe_short_cooldown_until = 0.0
    j._symbol_state = {"BTC-USDT": {"trend_streak": 2}}
    j._available_balance = 1234.5

    class _RM:
        def snapshot(self):
            return {"effective_regime": "bullish", "confidence": 70, "basis": {}}
    j._regime_manager = _RM()

    snap = j._capture_state_snapshot("BTC-USDT")
    assert snap["_open_positions"] == ["BTC-USDT"]
    assert snap["_position_slots"] == {"BTC-USDT": "main"}
    assert snap["_archetype_cooldown"]["_history"]["standard"][0]["pnl"] == -1.0
    assert snap["_archetype_cooldown"]["_cooldown_until"] == {"standard": 200.0}
    assert snap["_recent_wins"] == 3 and snap["_total_completed_trades"] == 10
    assert snap["_available_balance"] == 1234.5
    assert snap["_regime_manager"]["effective_regime"] == "bullish"
    assert snap["_symbol_state"] == {"trend_streak": 2}  # 只取当前 symbol
    import json
    json.dumps(snap)  # serializable
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_decision_state_snapshot.py -q`
Expected: FAIL（`_jsonable`/`state_snapshot` 不存在）

- [ ] **Step 3: 实现 decision_tape 扩展**

在 `utils/decision_tape.py` 顶部加：
```python
def _jsonable(v):
    """递归把 set→sorted list，保证 JSON 可序列化。"""
    if isinstance(v, set):
        return sorted(_jsonable(x) for x in v)
    if isinstance(v, dict):
        return {k: _jsonable(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v
```
把 `build_bundle` 签名末尾加 `state_snapshot=None`，并在返回 dict 加两字段：
```python
        "state_snapshot_before_decision": _jsonable(state_snapshot) if state_snapshot is not None else None,
        "replayable": state_snapshot is not None,
```

- [ ] **Step 4: 实现 Judge._capture_state_snapshot**

在 `agents/trading/judge.py` MultiJudge 加方法（放 `_record_rejected_plan` 附近）：
```python
    def _capture_state_snapshot(self, symbol: str) -> dict:
        """白名单采集决策时跨决策可变状态（observability-only）。不 pickle 整个对象。"""
        ac = getattr(self, "_archetype_cooldown", None)
        rm = getattr(self, "_regime_manager", None)
        return {
            "_open_positions": list(getattr(self, "_open_positions", set())),
            "_pending_open_symbols": list(getattr(self, "_pending_open_symbols", set())),
            "_position_slots": dict(getattr(self, "_position_slots", {})),
            "_pending_open_slots": dict(getattr(self, "_pending_open_slots", {})),
            "_archetype_cooldown": {
                "_history": getattr(ac, "_history", {}),
                "_cooldown_until": getattr(ac, "_cooldown_until", {}),
            } if ac is not None else None,
            "_recent_wins": getattr(self, "_recent_wins", 0),
            "_total_completed_trades": getattr(self, "_total_completed_trades", 0),
            "_recent_win_rate": getattr(self, "_recent_win_rate", None),
            "_probe_short_active": getattr(self, "_probe_short_active", None),
            "_probe_short_sl_count": getattr(self, "_probe_short_sl_count", 0),
            "_probe_short_cooldown_until": getattr(self, "_probe_short_cooldown_until", 0.0),
            "_symbol_state": dict(getattr(self, "_symbol_state", {}).get(symbol, {})),
            "_available_balance": getattr(self, "_available_balance", 0.0),
            "_regime_manager": rm.snapshot() if rm is not None and hasattr(rm, "snapshot") else None,
        }
```
注：`_history` 里是 list[dict]，`_jsonable` 会递归处理；`defaultdict` 转普通 dict 由 `_jsonable` 的 dict 分支兜住（dict 子类同样命中 isinstance dict）。

- [ ] **Step 5: Judge 接线两处传入快照**

在 accept 接线点（judge.py ~1979 的 `build_bundle(...)`）加参数：
```python
                state_snapshot=self._capture_state_snapshot(symbol),
```
在 reject 接线点（judge.py ~3001 的 `build_bundle(...)`）同样加：
```python
                state_snapshot=self._capture_state_snapshot(symbol),
```
（两处都在已有的 `getattr(self, "_decision_tape", None) is not None` guard 内，部分构造安全。）

- [ ] **Step 6: 运行通过 + 不回归**

Run: `python3 -m pytest tests/test_decision_state_snapshot.py tests/test_decision_tape.py tests/test_judge_decision_tape_wiring.py -q`
Expected: PASS。再 `python3 -m pytest -q 2>&1 | tail -3` 确认基线不降（>=1185）。

- [ ] **Step 7: 提交**

```bash
git add utils/decision_tape.py agents/trading/judge.py tests/test_decision_state_snapshot.py
git commit -m "feat(replay): capture pre-decision state snapshot into tape (whitelist, replayable flag)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

archived-with: 2026-06-14-deterministic-replay-golden-master
---

## Task 2: 回放 harness — 状态还原 + mock + replay_decision

**Files:**
- Create: `utils/decision_replay.py`
- Test: `tests/test_decision_replay.py`

- [ ] **Step 1: 写失败测试（合成 fixture：reject 路径最易确定性复现）**

```python
# tests/test_decision_replay.py
import asyncio
from utils.decision_replay import restore_state, replay_decision


def _fixture_record():
    """一条带状态快照的 reject record（合成）。"""
    snap = {
        "_open_positions": [], "_pending_open_symbols": [],
        "_position_slots": {}, "_pending_open_slots": {},
        "_archetype_cooldown": {"_history": {}, "_cooldown_until": {}},
        "_recent_wins": 0, "_total_completed_trades": 0, "_recent_win_rate": None,
        "_probe_short_active": None, "_probe_short_sl_count": 0,
        "_probe_short_cooldown_until": 0.0,
        "_symbol_state": {}, "_available_balance": 1000.0,
        "_regime_manager": {"effective_regime": "bullish", "confidence": 70, "basis": {}},
    }
    return {
        "schema_version": "decision_replay_record.v1",
        "request_id": "rep-1", "timestamp": 1700000000.0, "symbol": "BTC-USDT",
        "decision": "reject", "tech_analysis": {"indicators": {"price": 50000.0}},
        "price_at_decision": 50000.0, "regime_state": "bullish",
        "llm_output_inline": {"action": "hold", "confidence": 0, "reasoning": "x",
                              "key_factors": [], "risk_warnings": []},
        "llm_audit_ref": None,
        "trade_decision_output": {"reject_reason": "synthetic", "attribution": {}},
        "state_snapshot_before_decision": snap, "replayable": True,
    }


def test_restore_state_sets_fields():
    from agents.trading.judge import MultiJudge
    j = MultiJudge.__new__(MultiJudge)
    restore_state(j, _fixture_record()["state_snapshot_before_decision"])
    assert j._open_positions == set()
    assert j._available_balance == 1000.0
    assert j._archetype_cooldown._cooldown_until == {}
    assert j._regime_manager.snapshot()["effective_regime"] == "bullish"


def test_replay_captures_published_decision():
    # 回放一条 record，截获 publish 的 trade_decision payload，不打网络
    rec = _fixture_record()
    captured = asyncio.run(replay_decision(rec, config={}))
    assert captured is not None
    assert captured["symbol"] == "BTC-USDT"
    assert captured["action"] in ("open_long", "open_short", "hold", "close")
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_decision_replay.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 utils/decision_replay.py**

```python
"""确定性决策回放 harness：用真实 MultiJudge 代码重放历史决策。
observability-only —— 严禁交易决策路径 import/调用本模块。"""
from unittest import mock
from utils.archetype_cooldown import ArchetypeCooldown


class _RegimeStub:
    """还原 regime snapshot；_make_decision 经 .snapshot() 与 ._effective_regime 读 regime。"""
    def __init__(self, snap):
        self._snap = snap or {}
        self._effective_regime = self._snap.get("effective_regime")
        self._raw_regime = self._snap.get("raw_regime", self._effective_regime)
        self._confidence = self._snap.get("confidence", 0)

    def snapshot(self):
        return dict(self._snap)

    def is_probe_short_eligible(self, *a, **k):
        return False


def restore_state(judge, snap):
    """白名单还原 judge.* （list→set 等）。"""
    judge._open_positions = set(snap.get("_open_positions", []))
    judge._pending_open_symbols = set(snap.get("_pending_open_symbols", []))
    judge._position_slots = dict(snap.get("_position_slots", {}))
    judge._pending_open_slots = dict(snap.get("_pending_open_slots", {}))
    ac_snap = snap.get("_archetype_cooldown") or {"_history": {}, "_cooldown_until": {}}
    ac = ArchetypeCooldown(enabled=True, logger=None)
    ac._history = dict(ac_snap.get("_history", {}))
    ac._cooldown_until = dict(ac_snap.get("_cooldown_until", {}))
    judge._archetype_cooldown = ac
    judge._recent_wins = snap.get("_recent_wins", 0)
    judge._total_completed_trades = snap.get("_total_completed_trades", 0)
    judge._recent_win_rate = snap.get("_recent_win_rate")
    judge._probe_short_active = snap.get("_probe_short_active")
    judge._probe_short_sl_count = snap.get("_probe_short_sl_count", 0)
    judge._probe_short_cooldown_until = snap.get("_probe_short_cooldown_until", 0.0)
    judge._symbol_state = {snap_symbol_key(snap): dict(snap.get("_symbol_state", {}))} \
        if snap.get("_symbol_state") else {}
    judge._available_balance = snap.get("_available_balance", 0.0)
    judge._regime_manager = _RegimeStub(snap.get("_regime_manager"))


def snap_symbol_key(snap):
    # _symbol_state 快照按 symbol 还原；symbol 由 record 提供（见 replay_decision）
    return snap.get("__symbol__", "__cur__")


async def replay_decision(record, config=None):
    """重放一条带状态快照的 record，返回截获的 trade_decision payload（或 None）。"""
    from agents.trading.judge import MultiJudge
    if not record.get("replayable") or not record.get("state_snapshot_before_decision"):
        return None
    symbol = record["symbol"]
    snap = dict(record["state_snapshot_before_decision"])
    snap["__symbol__"] = symbol  # 让 _symbol_state 按真实 symbol 还原

    judge = MultiJudge.__new__(MultiJudge)
    # 最小必需的非状态属性（_make_decision 读取的配置/开关），用 config 默认
    judge.config = config or {}
    judge.logger = mock.MagicMock()
    _install_config_flags(judge, config or {})
    restore_state(judge, snap)

    captured = []

    async def _capture_publish(msg_type, payload, to="broadcast", symbol=None):
        if msg_type == "trade_decision":
            captured.append(payload)

    async def _noop_balance():
        return None

    async def _inject_llm(sym, tech, score):
        return record.get("llm_output_inline") or {"action": "hold", "confidence": 0,
                                                    "reasoning": "", "key_factors": [],
                                                    "risk_warnings": []}

    judge.publish = _capture_publish
    judge._update_balance = _noop_balance
    judge._ask_llm = _inject_llm
    # 决策磁带/反事实写入在回放中禁用（不应再写）
    judge._decision_tape = None

    ts = record["timestamp"]
    with mock.patch("time.time", return_value=ts):
        await judge._make_decision(symbol, record["tech_analysis"])

    return captured[0] if captured else None


def _install_config_flags(judge, config):
    """还原 _make_decision 读取的配置开关（白名单，缺省用 L1 默认）。"""
    judge._short_regime_guard_enabled = config.get("short_regime_guard_enabled", True)
    judge._probe_short_enabled = config.get("probe_short_enabled", True)
    # 其余开关在 build 阶段按 _make_decision 实际读取补齐（见 Step 4 迭代）
```

> 注：`_install_config_flags` 是白名单兜底——build 时跑 fixture 测试，若 `_make_decision` 抛 AttributeError 缺某 `self._xxx` 开关/阈值，逐个补进此函数（用 `MultiJudge.__init__` 同名默认值）。这是 harness 跑通的迭代点。

- [ ] **Step 4: 跑测试，迭代补齐缺失属性**

Run: `python3 -m pytest tests/test_decision_replay.py -q`
若 `replay_decision` 抛 `AttributeError: 'MultiJudge' object has no attribute '_xxx'`，对照 `agents/trading/judge.py` `__init__` 里 `self._xxx = <default>`，把该默认值加进 `_install_config_flags`。重复直到 fixture record 能跑完 `_make_decision` 并 capture 到 payload。
Expected 最终: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add utils/decision_replay.py tests/test_decision_replay.py
git commit -m "feat(replay): deterministic replay harness (restore state + mock 3 external awaits + capture)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

archived-with: 2026-06-14-deterministic-replay-golden-master
---

## Task 3: golden-master 三层比对

**Files:**
- Modify: `utils/decision_replay.py`（加 `compare_decision`）
- Test: `tests/test_golden_compare.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_golden_compare.py
from utils.decision_replay import compare_decision


def _dec(**kw):
    base = {"action": "open_long", "confidence": 70, "dispatch_path": "main_direct",
            "reasoning": "foo", "plan": {"size_usdt": 30.0, "entry_ref": 100.0,
            "stop_loss": 95.0, "take_profit": [110.0], "leverage": 5},
            "attribution": {"slot_type": "main", "is_probe": False, "rr_policy": "default"}}
    base.update(kw); return base


def test_identical_matches():
    r = compare_decision(_dec(), _dec())
    assert r["match"] is True and r["diffs"] == []


def test_discrete_mismatch_fails():
    r = compare_decision(_dec(confidence=70), _dec(confidence=60))
    assert r["match"] is False
    assert any(d["field"] == "confidence" for d in r["diffs"])


def test_continuous_within_tolerance_matches():
    a = _dec(); b = _dec()
    b["plan"]["size_usdt"] = 30.0 * 1.003  # 0.3% < 0.5%
    r = compare_decision(a, b)
    assert r["match"] is True


def test_continuous_beyond_tolerance_fails():
    a = _dec(); b = _dec()
    b["plan"]["stop_loss"] = 95.0 * 1.02  # 2% > 0.5%
    r = compare_decision(a, b)
    assert r["match"] is False
    assert any("stop_loss" in d["field"] for d in r["diffs"])


def test_reasoning_diff_is_informational_only():
    r = compare_decision(_dec(reasoning="A"), _dec(reasoning="B"))
    assert r["match"] is True  # reasoning 不判负
    assert any(d["field"] == "reasoning" and d.get("informational") for d in r["diffs"])
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_golden_compare.py -q` → FAIL（compare_decision 不存在）

- [ ] **Step 3: 实现 compare_decision**

在 `utils/decision_replay.py` 加：
```python
_DISCRETE = ("action", "confidence", "dispatch_path")
_DISCRETE_ATTR = ("entry_type", "slot_type", "is_probe", "is_low_rr",
                  "short_gate_decision", "short_gate_reason", "rr_policy", "rr_floor_used",
                  "entry_position_status", "entry_position_block_reason", "blocked_by")
_CONTINUOUS = ("size_usdt", "entry_ref", "stop_loss", "leverage")  # take_profit 单列
_INFORMATIONAL = ("reasoning", "key_factors", "risk_warnings")
_TOL = 0.005


def _rel_close(a, b, tol=_TOL):
    if a is None or b is None:
        return a == b
    if a == 0:
        return abs(b) <= tol
    return abs(a - b) / abs(a) <= tol


def compare_decision(recorded, replayed):
    """三层比对：离散字节级 fail / 连续 <0.5% fail / reasoning 仅信息。"""
    diffs = []
    match = True
    for f in _DISCRETE:
        if recorded.get(f) != replayed.get(f):
            diffs.append({"field": f, "recorded": recorded.get(f), "replayed": replayed.get(f)})
            match = False
    ra, pa = recorded.get("attribution") or {}, replayed.get("attribution") or {}
    for f in _DISCRETE_ATTR:
        if ra.get(f) != pa.get(f):
            diffs.append({"field": f"attribution.{f}", "recorded": ra.get(f), "replayed": pa.get(f)})
            match = False
    rp, pp = recorded.get("plan") or {}, replayed.get("plan") or {}
    for f in _CONTINUOUS:
        if not _rel_close(rp.get(f), pp.get(f)):
            diffs.append({"field": f"plan.{f}", "recorded": rp.get(f), "replayed": pp.get(f)})
            match = False
    rtp, ptp = rp.get("take_profit") or [], pp.get("take_profit") or []
    if len(rtp) != len(ptp) or any(not _rel_close(x, y) for x, y in zip(rtp, ptp)):
        diffs.append({"field": "plan.take_profit", "recorded": rtp, "replayed": ptp})
        match = False
    for f in _INFORMATIONAL:
        if recorded.get(f) != replayed.get(f):
            diffs.append({"field": f, "recorded": recorded.get(f),
                          "replayed": replayed.get(f), "informational": True})
    return {"match": match, "diffs": diffs}
```

- [ ] **Step 4: 运行通过**

Run: `python3 -m pytest tests/test_golden_compare.py -q` → PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add utils/decision_replay.py tests/test_golden_compare.py
git commit -m "feat(replay): golden-master 3-tier compare (discrete byte / continuous 0.5% / reasoning info-only)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

archived-with: 2026-06-14-deterministic-replay-golden-master
---

## Task 4: 端到端 replay-report driver

**Files:**
- Create: `cf_replay_driver.py`
- Test: `tests/test_cf_replay_driver.py`

- [ ] **Step 1: 写失败测试（用临时 sqlite + 临时 jsonl）**

```python
# tests/test_cf_replay_driver.py
import json, sqlite3
from cf_replay_driver import load_klines_window, build_report_from_rejected


def _mk_klines_db(path, symbol, bars):
    conn = sqlite3.connect(path)
    conn.execute('''CREATE TABLE klines (symbol TEXT, interval TEXT, open_time INTEGER,
        open REAL, high REAL, low REAL, close REAL, volume REAL,
        UNIQUE(symbol, interval, open_time))''')
    for t, hi, lo in bars:
        conn.execute("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?)",
                     (symbol, "1m", t, (hi+lo)/2, hi, lo, (hi+lo)/2, 0))
    conn.commit(); conn.close()


def test_load_klines_window_filters_24h(tmp_path):
    db = str(tmp_path / "k.db")
    base = 1_700_000_000_000  # ms
    _mk_klines_db(db, "BTC-USDT", [(base, 101, 99), (base + 25*3600*1000, 200, 1)])
    bars = load_klines_window(db, "BTC-USDT", created_at=base/1000, window_sec=86400)
    assert len(bars) == 1  # 第二根超 24h 被排除
    assert bars[0]["high"] == 101


def test_build_report_from_rejected_end_to_end(tmp_path):
    db = str(tmp_path / "k.db")
    base = 1_700_000_000_000
    _mk_klines_db(db, "BTC-USDT", [(base + 60_000, 111, 109)])  # high 触 TP 110
    events = str(tmp_path / "rejected.jsonl")
    rec = {"event_type": "rejected_plan_created", "record": {
        "symbol": "BTC-USDT", "side": "long", "entry_price": 100.0, "stop_loss": 95.0,
        "take_profit": [110.0], "leverage": 5, "size_usdt": 30.0,
        "created_at": base/1000, "funding_rate": 0.0,
        "reject_reason": "rr_below_floor", "effective_regime": "choppy"}}
    with open(events, "w") as f:
        f.write(json.dumps(rec) + "\n")
    rep = build_report_from_rejected(events, klines_1s_db="/nonexistent", klines_db=db,
                                     min_sample=1, lowconf_sample=2)
    bucket = rep["buckets"]["rr_below_floor|choppy|long"]
    assert bucket["n"] >= 1
    assert rep["skipped_no_data"] == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_cf_replay_driver.py -q` → FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 cf_replay_driver.py**

```python
"""端到端被拒单反事实报表 driver：rejected_signal_events.jsonl + klines → resolve → build_cf_report。
observability-only —— 输出严禁交易决策读取。"""
import json
import os
import sqlite3
from utils.counterfactual_pnl import resolve_counterfactual
from replay_report import build_cf_report


def load_klines_window(db_path, symbol, created_at, window_sec=86400):
    """取 [created_at, created_at+window_sec] 的 bars（升序）。open_time 单位 ms。"""
    if not db_path or not os.path.exists(db_path):
        return []
    lo_ms = int(created_at * 1000)
    hi_ms = int((created_at + window_sec) * 1000)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT open_time, high, low, close FROM klines "
            "WHERE symbol=? AND open_time>=? AND open_time<=? ORDER BY open_time",
            (symbol, lo_ms, hi_ms)).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    return [{"open_time": t, "high": h, "low": l, "close": c} for t, h, l, c in rows]


def build_report_from_rejected(events_path, *, klines_1s_db, klines_db,
                               min_sample=30, lowconf_sample=100, window_sec=86400):
    rows = []
    skipped = 0
    if not os.path.exists(events_path):
        return {"buckets": {}, "total": 0, "skipped_no_data": 0}
    with open(events_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except Exception:
                continue
            if evt.get("event_type") != "rejected_plan_created":
                continue
            rec = evt.get("record") or {}
            sym, created = rec.get("symbol"), rec.get("created_at")
            if not sym or created is None:
                skipped += 1
                continue
            bars = load_klines_window(klines_1s_db, sym, created, window_sec)
            source = "tape_exact"
            if not bars:
                bars = load_klines_window(klines_db, sym, created, window_sec)
                source = "attribution_reconstructed"
            if not bars:
                skipped += 1
                continue
            r = resolve_counterfactual(rec, bars, source=source)
            rows.append({
                "reject_reason": rec.get("reject_reason"),
                "effective_regime": rec.get("effective_regime"),
                "side": rec.get("side"),
                "outcome": r.outcome, "net_usdt": r.net_usdt,
                "price_ambiguous": r.price_ambiguous, "source": r.source,
            })
    report = build_cf_report(rows, min_sample=min_sample, lowconf_sample=lowconf_sample)
    report["skipped_no_data"] = skipped
    return report
```

- [ ] **Step 4: 运行通过**

Run: `python3 -m pytest tests/test_cf_replay_driver.py -q` → PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add cf_replay_driver.py tests/test_cf_replay_driver.py
git commit -m "feat(replay): end-to-end rejected-signal report driver (klines 24h window + resolve + build_cf_report)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

archived-with: 2026-06-14-deterministic-replay-golden-master
---

## Task 5: 红线守卫扩展 + 文档 + 记忆

**Files:**
- Modify: `tests/test_cf_red_line_guard.py`
- Modify: `CLAUDE.md`、`docs/to-do-list.md`
- Modify: memory `counterfactual_replay_lab_roadmap.md`

- [ ] **Step 1: 扩展红线守卫**

在 `tests/test_cf_red_line_guard.py` 加：
```python
def test_decision_paths_do_not_read_replay_products():
    import inspect
    for mp in ["agents.trading.judge", "agents.trading.executor", "executor",
               "agents.trading.portfolio_risk_guard", "agents.trading.reviewer"]:
        mod = __import__(mp, fromlist=["x"])
        src = inspect.getsource(mod)
        assert "decision_replay" not in src, mp
        assert "cf_replay_driver" not in src, mp
        assert "state_snapshot_before_decision" not in src, mp
```

- [ ] **Step 2: 运行通过**

Run: `python3 -m pytest tests/test_cf_red_line_guard.py -q` → PASS。
注：Judge 会有 `_capture_state_snapshot`（写快照，允许），但不得含 `decision_replay`/`cf_replay_driver` import 或读 `state_snapshot_before_decision`。若失败说明决策路径误读回放产物，必须移除。

- [ ] **Step 3: 文档 + 记忆**

CLAUDE.md 风控红线追加一条 L2 声明（决策状态快照 / 回放 harness `utils/decision_replay.py` / driver `cf_replay_driver.py` 均 observability-only write-only，golden-master 复现钉决策逻辑、reasoning 仅信息，守卫 `test_cf_red_line_guard.py`；真实数据终验 N≥50 待累积 = follow-up）。docs/to-do-list.md 反事实实验室条目更新 #2 完成、#3/#4 待做。memory `counterfactual_replay_lab_roadmap.md` 标 L2 完成。

- [ ] **Step 4: 提交**

```bash
git add tests/test_cf_red_line_guard.py CLAUDE.md docs/to-do-list.md
git commit -m "docs(replay): L2 red-line guard + roadmap update

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

archived-with: 2026-06-14-deterministic-replay-golden-master
---

## Task 6: 全量验证与零回归

- [ ] **Step 1: 编译**

Run: `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .` → exit 0

- [ ] **Step 2: 全量测试**

Run: `python3 -m pytest -q` → PASS，总数 ≥ 1185 + 新增（约 +17），无 failure

- [ ] **Step 3: 零回归（flag 全关）**

Run: `DECISION_TAPE_ENABLED=false python3 -m pytest -q -k "judge or decision_tape or decision_state" 2>&1 | tail -3` → PASS；确认 flag 关停时不采集状态快照、决策不变

- [ ] **Step 4: 最终提交**

```bash
git add -A
git commit -m "chore(replay): L2 full regression green — deterministic replay + golden master complete"
```

archived-with: 2026-06-14-deterministic-replay-golden-master
---

## Self-Review 结论

- **Spec 覆盖**：decision-state-snapshot（Task 1）、deterministic-replay-harness（Task 2 还原+mock+replay，Task 3 三层比对）、replay-report-driver（Task 4）、红线守卫（Task 5）、零回归（Task 6）—— 3 capability 全覆盖。
- **类型一致**：`_capture_state_snapshot`/`_jsonable`/`build_bundle(state_snapshot=)`/`restore_state`/`replay_decision`/`compare_decision`/`load_klines_window`/`build_report_from_rejected` 跨 task 签名一致。
- **关键风险已处理**：Task 2 Step 4 显式迭代补齐 `_make_decision` 缺失属性（3 外部 await 已枚举 stub：`_update_balance`/`_ask_llm`/`publish` + `time.time` patch）。
- **无 placeholder**：每步真实代码 + 命令。YAGNI：harness 只 mock 已知 3 外部 await，缺啥补啥不预设。
