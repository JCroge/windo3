---
change: sequential-portfolio-perturbation
design-doc: docs/superpowers/specs/2026-06-14-sequential-portfolio-perturbation-design.md
base-ref: 9d1ed0f81d2a38cac3f50b02fd4579b242bfe7d3
---

# Sequential Portfolio Perturbation (L3b) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** 按时间序重放决策磁带 + 维护扰动后 CF 组合状态，给整策略 PnL/胜率/回撤 delta（两臂同估算 → delta 抵消系统偏差；baseline 序列保真自检作信任锚）。

**Architecture:** `utils/cf_portfolio.py`（CF 组合状态机）+ `utils/sequential_perturbation.py`（时间序 driver + delta 报表）。复用 L1 `resolve_counterfactual`、L2 `replay_decision`、`ArchetypeCooldown`、`cf_replay_driver.load_klines_window`。observability-only write-only，完全隔离。

**确认的 API:** `CfResult(outcome,exit_price,net_usdt,price_ambiguous,funding_approx,hold_hours,source)`；`ArchetypeCooldown.classify(attribution)/record_result(archetype,pnl)/is_cooled(archetype)`；Reviewer 阈值 `daily_pnl_hard_stop=-50.0`/`consecutive_loss_limit=3`；`resolve_counterfactual(record,bars,*,max_hold_sec,source,cost_model)`。

**时间因果:** CF 仓开仓即计算 `resolved_ts = open_ts + hold_hours*3600` + net_usdt（resolve_counterfactual 全窗口）；driver 每步先 `resolve_due(now)` 弹出 resolved_ts≤now 的退出按时间序反馈 → daily-stop/EV 因果正确。

**红线:** observability-only write-only（Task 4 守卫）；CF 决策绝不 publish 真实 bus。零回归：基线 1208 不降。

---

## Task 1: CF 组合状态机 utils/cf_portfolio.py

**Files:** Create `utils/cf_portfolio.py`, `tests/test_cf_portfolio.py`.

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cf_portfolio.py
from utils.cf_portfolio import CounterfactualPortfolio


def _price_loader_tp(symbol, created_at, window_sec=86400):
    # high 触 TP 110（long），1 分钟后
    return [{"open_time": int((created_at + 60) * 1000), "high": 111, "low": 109,
             "close": 110}]


def _open_decision(symbol="BTC-USDT"):
    return {"action": "open_long", "symbol": symbol, "plan": {
        "entry_ref": 100.0, "stop_loss": 95.0, "take_profit": [110.0],
        "leverage": 5, "size_usdt": 30.0}, "attribution": {"entry_type": "rule_signal"}}


def test_open_occupies_slot():
    cf = CounterfactualPortfolio(initial_equity=1000.0, max_slots=3,
                                 price_loader=_price_loader_tp)
    opened = cf.apply_decision(_open_decision(), created_at=1000.0,
                               funding_rate=0.0, regime="bullish")
    assert opened is True
    assert "BTC-USDT" in cf.open_symbols()
    assert cf.slot_count() == 1


def test_slot_full_blocks_open():
    cf = CounterfactualPortfolio(initial_equity=1000.0, max_slots=1,
                                 price_loader=_price_loader_tp)
    cf.apply_decision(_open_decision("BTC-USDT"), 1000.0, 0.0, "bullish")
    blocked = cf.apply_decision(_open_decision("ETH-USDT"), 1000.0, 0.0, "bullish")
    assert blocked is False
    assert cf.slot_count() == 1


def test_resolve_due_realizes_pnl_and_frees_slot():
    cf = CounterfactualPortfolio(initial_equity=1000.0, max_slots=3,
                                 price_loader=_price_loader_tp)
    cf.apply_decision(_open_decision(), created_at=1000.0, funding_rate=0.0, regime="bullish")
    # resolved_ts ~ 1000 + 60；推进到之后
    cf.resolve_due(now=2000.0)
    assert cf.slot_count() == 0
    assert cf.equity > 1000.0          # TP 命中，盈利
    assert cf._total_completed_trades == 1
    assert cf._recent_wins == 1


def test_to_snapshot_format():
    cf = CounterfactualPortfolio(initial_equity=1000.0, max_slots=3,
                                 price_loader=_price_loader_tp)
    cf.apply_decision(_open_decision(), 1000.0, 0.0, "bullish")
    snap = cf.to_snapshot(regime_snapshot={"effective_regime": "bullish", "confidence": 70})
    assert set(snap["_open_positions"]) == {"BTC-USDT"}
    assert snap["_available_balance"] == cf.equity
    assert snap["_regime_manager"]["effective_regime"] == "bullish"
    assert "_archetype_cooldown" in snap and "_recent_wins" in snap


def test_daily_stop_blocks_after_loss():
    # SL 命中亏损，跌破 daily_pnl_hard_stop 后当日停开
    def loss_loader(symbol, created_at, window_sec=86400):
        return [{"open_time": int((created_at + 60) * 1000), "high": 96, "low": 90,
                 "close": 94}]  # low 触 SL 95
    cf = CounterfactualPortfolio(initial_equity=1000.0, max_slots=5,
                                 price_loader=loss_loader, daily_pnl_hard_stop=-1.0)
    cf.apply_decision(_open_decision(), created_at=1000.0, funding_rate=0.0, regime="bullish")
    cf.resolve_due(now=2000.0)
    assert cf.equity < 1000.0
    # 当日再开应被 daily-stop 拦
    blocked = cf.apply_decision(_open_decision("ETH-USDT"), created_at=2100.0,
                                funding_rate=0.0, regime="bullish")
    assert blocked is False
```

- [ ] **Step 2: 运行确认失败** — `python3 -m pytest tests/test_cf_portfolio.py -q` → FAIL

- [ ] **Step 3: 实现 utils/cf_portfolio.py**

```python
"""反事实组合状态机（L3b）：维护扰动后的 CF 持仓/slot/资金/EV/cooldown/daily-stop，
独立于真实系统。CF 开仓用 L1 resolve_counterfactual 估算退出 + 反馈。
observability-only —— 严禁交易决策路径 import/调用本模块。"""
import time as _time_unused  # noqa（避免误用 time.time，全用传入 ts）
from collections import defaultdict
from utils.counterfactual_pnl import resolve_counterfactual
from utils.archetype_cooldown import ArchetypeCooldown


def _utc_day(ts):
    # 不调 time；纯算 UTC 日序号
    return int(ts // 86400)


class CounterfactualPortfolio:
    def __init__(self, initial_equity=1000.0, max_slots=3, price_loader=None,
                 daily_pnl_hard_stop=-50.0, consecutive_loss_limit=3, window_sec=86400):
        self.equity = float(initial_equity)
        self.max_slots = max_slots
        self.price_loader = price_loader      # (symbol, created_at, window_sec) -> bars
        self.daily_pnl_hard_stop = daily_pnl_hard_stop
        self.consecutive_loss_limit = consecutive_loss_limit
        self.window_sec = window_sec
        self._open = {}                       # symbol -> cf position dict
        self._recent_wins = 0
        self._total_completed_trades = 0
        self._cf_cooldown = ArchetypeCooldown(enabled=True, logger=None)
        self._daily_pnl = defaultdict(float)  # utc_day -> realized pnl
        self._consec_losses = 0
        self._halted_days = set()
        self.realized = []                    # 历史已实现 net_usdt（按时间序）

    # ── 查询 ──
    def open_symbols(self):
        return set(self._open.keys())

    def slot_count(self):
        return len(self._open)

    def _day_halted(self, ts):
        return _utc_day(ts) in self._halted_days

    # ── 开仓 ──
    def apply_decision(self, decision, created_at, funding_rate=0.0, regime=None):
        """开仓 → 占 slot + 计算退出。返回是否开了 CF 仓。"""
        action = (decision or {}).get("action")
        if action not in ("open_long", "open_short"):
            return False
        symbol = decision.get("symbol")
        if symbol is None or symbol in self._open:
            return False
        if self.slot_count() >= self.max_slots or self._day_halted(created_at):
            return False
        plan = decision.get("plan") or {}
        side = "long" if action == "open_long" else "short"
        rec = {"symbol": symbol, "side": side,
               "entry_price": plan.get("entry_ref"),
               "stop_loss": plan.get("stop_loss"),
               "take_profit": plan.get("take_profit") or [],
               "leverage": plan.get("leverage", 1),
               "size_usdt": plan.get("size_usdt"),
               "created_at": created_at, "funding_rate": funding_rate}
        bars = self.price_loader(symbol, created_at, self.window_sec) if self.price_loader else []
        r = resolve_counterfactual(rec, bars, max_hold_sec=self.window_sec)
        archetype = self._cf_cooldown.classify(decision.get("attribution") or {})
        self._open[symbol] = {
            "resolved_ts": created_at + r.hold_hours * 3600.0,
            "net_usdt": r.net_usdt if r.net_usdt is not None else 0.0,
            "archetype": archetype, "created_at": created_at,
        }
        return True

    # ── 退出推进 ──
    def resolve_due(self, now):
        """结算 resolved_ts <= now 的 CF 仓（按时间序），反馈。"""
        due = sorted([(p["resolved_ts"], s) for s, p in self._open.items()
                      if p["resolved_ts"] <= now])
        for _, symbol in due:
            p = self._open.pop(symbol)
            net = p["net_usdt"]
            self.equity += net
            self.realized.append(net)
            self._total_completed_trades += 1
            if net > 0:
                self._recent_wins += 1
                self._consec_losses = 0
            else:
                self._consec_losses += 1
            self._cf_cooldown.record_result(p["archetype"], net)
            day = _utc_day(p["resolved_ts"])
            self._daily_pnl[day] += net
            if (self._daily_pnl[day] <= self.daily_pnl_hard_stop
                    or self._consec_losses >= self.consecutive_loss_limit):
                self._halted_days.add(day)

    # ── 状态注入 ──
    def to_snapshot(self, regime_snapshot=None):
        """以 L2 restore_state 接受的快照格式导出 CF 策略状态 + 注入 regime（市场状态）。"""
        return {
            "_open_positions": list(self._open.keys()),
            "_pending_open_symbols": [],
            "_pending_open_ts": {},
            "_position_slots": {s: "main" for s in self._open},
            "_pending_open_slots": {},
            "_archetype_cooldown": {"_history": dict(self._cf_cooldown._history),
                                    "_cooldown_until": dict(self._cf_cooldown._cooldown_until)},
            "_recent_wins": self._recent_wins,
            "_total_completed_trades": self._total_completed_trades,
            "_recent_win_rate": (self._recent_wins / self._total_completed_trades
                                 if self._total_completed_trades else None),
            "_probe_short_active": None, "_probe_short_sl_count": 0,
            "_probe_short_cooldown_until": 0.0,
            "_symbol_state": {}, "_available_balance": self.equity,
            "_regime_manager": regime_snapshot or {"effective_regime": "mixed", "confidence": 50},
        }
```

- [ ] **Step 4: 运行通过** — `python3 -m pytest tests/test_cf_portfolio.py -q` → 6 passed
- [ ] **Step 5: 提交**

```bash
git add utils/cf_portfolio.py tests/test_cf_portfolio.py
git commit -m "feat(L3b): counterfactual portfolio sim (slot/equity/EV/independent cooldown/daily-stop + L1 PnL feedback)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: 序列 driver utils/sequential_perturbation.py :: run_arm

**Files:** Create `utils/sequential_perturbation.py`, `tests/test_sequential_perturbation.py`.

- [ ] **Step 1: 写失败测试**

```python
# tests/test_sequential_perturbation.py
import asyncio
import pytest
from utils.sequential_perturbation import run_arm


@pytest.fixture(autouse=True)
def _restore_loop():
    yield
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass


def _price_loader_tp(symbol, created_at, window_sec=86400):
    return [{"open_time": int((created_at + 60) * 1000), "high": 111, "low": 109, "close": 110}]


def _accept_rec(ts):
    from tests.test_decision_replay import _accept_fixture_record
    rec = _accept_fixture_record()
    rec["timestamp"] = ts
    rec["decision"] = "accept"
    return rec


def test_run_arm_opens_and_resolves():
    recs = [_accept_rec(1000.0)]
    arm = asyncio.run(run_arm(recs, config={}, price_loader=_price_loader_tp,
                              initial_equity=1000.0))
    # 真实 _make_decision 在强 bullish fixture 上开 open_long → CF 开仓 → 结算盈利
    assert arm["cf_open_count"] >= 1
    assert arm["final_equity"] >= 1000.0
    assert "decisions" in arm and len(arm["decisions"]) == 1


def test_run_arm_records_decisions_in_order():
    recs = [_accept_rec(1000.0), _accept_rec(100000.0)]
    arm = asyncio.run(run_arm(recs, config={}, price_loader=_price_loader_tp,
                              initial_equity=1000.0))
    assert [d["timestamp"] for d in arm["decisions"]] == [1000.0, 100000.0]
```

- [ ] **Step 2: 运行确认失败** — FAIL

- [ ] **Step 3: 实现 run_arm**

```python
"""序列扰动 driver（L3b）：时间序重放磁带 + CF 组合状态机 → 一臂结果。
observability-only —— CF 决策绝不进真实 bus；严禁交易决策路径 import 本模块。"""
from utils.decision_replay import replay_decision
from utils.cf_portfolio import CounterfactualPortfolio


def _inject_cf_state(record, cf):
    """把 CF 状态注入 record 的 state_snapshot（regime 取录下市场状态）。"""
    recorded_snap = record.get("state_snapshot_before_decision") or {}
    regime = recorded_snap.get("_regime_manager")
    snap = cf.to_snapshot(regime_snapshot=regime)
    new_rec = dict(record)
    new_rec["state_snapshot_before_decision"] = snap
    new_rec["replayable"] = True
    return new_rec


async def run_arm(records, config, price_loader, *, initial_equity=1000.0,
                  max_slots=3, daily_pnl_hard_stop=-50.0, consecutive_loss_limit=3):
    """时间序模拟一臂。返回 {final_equity, decisions, cf_open_count, realized, equity_curve}。"""
    recs = sorted(records, key=lambda r: r.get("timestamp", 0))
    cf = CounterfactualPortfolio(initial_equity=initial_equity, max_slots=max_slots,
                                 price_loader=price_loader,
                                 daily_pnl_hard_stop=daily_pnl_hard_stop,
                                 consecutive_loss_limit=consecutive_loss_limit)
    decisions = []
    cf_open_count = 0
    equity_curve = []
    for rec in recs:
        ts = rec.get("timestamp", 0)
        cf.resolve_due(ts)                       # 先结到期
        if rec.get("state_snapshot_before_decision"):
            injected = _inject_cf_state(rec, cf)
            decision = await replay_decision(injected, config)
        else:
            decision = None
        action = (decision or {}).get("action", "hold")
        decisions.append({"timestamp": ts, "symbol": rec.get("symbol"), "action": action})
        if decision:
            funding = (rec.get("state_snapshot_before_decision") or {}).get(
                "_funding_rate", 0.0)
            opened = cf.apply_decision(decision, created_at=ts, funding_rate=funding,
                                       regime=None)
            if opened:
                cf_open_count += 1
        equity_curve.append(cf.equity)
    # 收尾：结算所有剩余
    cf.resolve_due(float("inf"))
    return {"final_equity": cf.equity, "decisions": decisions,
            "cf_open_count": cf_open_count, "realized": list(cf.realized),
            "equity_curve": equity_curve + [cf.equity]}
```

- [ ] **Step 4: 运行通过 + 不回归** — `python3 -m pytest tests/test_sequential_perturbation.py -q` → 2 passed；`python3 -m pytest -q 2>&1 | tail -3` → ≥ 1208
- [ ] **Step 5: 提交**

```bash
git add utils/sequential_perturbation.py tests/test_sequential_perturbation.py
git commit -m "feat(L3b): sequential perturbation driver (time-ordered replay + CF portfolio, isolated)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: delta 报表 + baseline 序列保真自检

**Files:** Modify `utils/sequential_perturbation.py`（加 `build_delta_report`）, `tests/test_sequential_perturbation.py`（加测试）.

- [ ] **Step 1: 写失败测试**

```python
def test_delta_report_two_arms_and_fidelity():
    from utils.sequential_perturbation import build_delta_report
    recs = [_accept_rec(1000.0 + i * 100000.0) for i in range(3)]
    for r in recs:
        r["decision"] = "accept"  # 录下都是开仓
    rep = asyncio.run(build_delta_report(
        recs, baseline_config={}, perturbed_config={"rr_floor_default": 10.0,
        "rr_floor_long_bullish": 10.0, "rr_floor_long_aligned_choppy": 10.0},
        price_loader=_price_loader_tp, fidelity_threshold=0.5))
    assert "delta" in rep and "baseline" in rep and "perturbed" in rep
    assert "baseline_fidelity" in rep["metadata"]
    assert "divergence_ratio" in rep["metadata"]
    # baseline 全开（fidelity 高），perturbed 全拒（地板 10.0）→ delta 非零
    assert rep["metadata"]["baseline_fidelity"] >= 0.5


def test_delta_report_low_fidelity_untrustworthy():
    from utils.sequential_perturbation import build_delta_report
    recs = [_accept_rec(1000.0)]
    recs[0]["decision"] = "reject"   # 录下说 reject，但 baseline-sim 会 accept → fidelity 0
    rep = asyncio.run(build_delta_report(recs, baseline_config={}, perturbed_config={},
                      price_loader=_price_loader_tp, fidelity_threshold=0.8))
    assert rep["metadata"]["untrustworthy"] is True
    assert rep.get("delta") is None
```

- [ ] **Step 2: 运行确认失败** — FAIL

- [ ] **Step 3: 实现 build_delta_report**

```python
def _max_drawdown(curve):
    peak = curve[0] if curve else 0.0
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = max(mdd, peak - v)
    return mdd


def _decision_class(action):
    return "accept" if action in ("open_long", "open_short") else "reject"


_FIDELITY_NOTE = ("退出仅 SL/TP/24h（漏 trailing/partial/risk-close ~10-20%），误差沿序列累积；"
                  "两臂同估算 → 系统性偏差在 delta 抵消，结论以 delta 为主非绝对值。")


async def build_delta_report(records, baseline_config, perturbed_config, price_loader, *,
                             initial_equity=1000.0, max_slots=3, fidelity_threshold=0.8,
                             daily_pnl_hard_stop=-50.0, consecutive_loss_limit=3):
    recs = sorted(records, key=lambda r: r.get("timestamp", 0))
    kw = dict(price_loader=price_loader, initial_equity=initial_equity, max_slots=max_slots,
              daily_pnl_hard_stop=daily_pnl_hard_stop,
              consecutive_loss_limit=consecutive_loss_limit)
    base = await run_arm(recs, baseline_config, **kw)

    # baseline 序列保真自检：baseline-sim 决策 vs 录下决策
    agree = sum(1 for d, r in zip(base["decisions"], recs)
                if _decision_class(d["action"]) == r.get("decision"))
    fidelity = agree / len(recs) if recs else 0.0
    meta = {"perturbed_knobs": dict(perturbed_config or {}),
            "baseline_fidelity": fidelity, "sequence_len": len(recs),
            "fidelity_note": _FIDELITY_NOTE}
    if fidelity < fidelity_threshold:
        meta["untrustworthy"] = True
        return {"baseline": None, "perturbed": None, "delta": None, "metadata": meta}
    meta["untrustworthy"] = False

    pert = await run_arm(recs, perturbed_config, **kw)
    div = sum(1 for b, p in zip(base["decisions"], pert["decisions"])
              if b["action"] != p["action"])
    meta["divergence_ratio"] = div / len(recs) if recs else 0.0
    meta["baseline_cf_open_count"] = base["cf_open_count"]
    meta["perturbed_cf_open_count"] = pert["cf_open_count"]

    def _summ(arm):
        rl = arm["realized"]
        wins = sum(1 for x in rl if x > 0)
        return {"net_pnl": arm["final_equity"] - initial_equity,
                "trades": len(rl), "win_rate": wins / len(rl) if rl else 0.0,
                "max_drawdown": _max_drawdown(arm["equity_curve"])}
    b_s, p_s = _summ(base), _summ(pert)
    delta = {"net_pnl": p_s["net_pnl"] - b_s["net_pnl"],
             "win_rate": p_s["win_rate"] - b_s["win_rate"],
             "max_drawdown": p_s["max_drawdown"] - b_s["max_drawdown"]}
    return {"baseline": b_s, "perturbed": p_s, "delta": delta, "metadata": meta}
```

- [ ] **Step 4: 运行通过** — `python3 -m pytest tests/test_sequential_perturbation.py -q` → 4 passed
- [ ] **Step 5: 提交**

```bash
git add utils/sequential_perturbation.py tests/test_sequential_perturbation.py
git commit -m "feat(L3b): baseline-vs-perturbed delta report + baseline sequence fidelity gate

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: 红线守卫 + 文档

**Files:** Modify `tests/test_cf_red_line_guard.py`, `CLAUDE.md`, `docs/to-do-list.md`, memory.

- [ ] **Step 1: 扩展红线守卫** — 在 `test_decision_paths_do_not_read_replay_products` 循环体加：
```python
        assert "cf_portfolio" not in src, mp
        assert "sequential_perturbation" not in src, mp
```
- [ ] **Step 2: 运行通过** — `python3 -m pytest tests/test_cf_red_line_guard.py -q` → PASS
- [ ] **Step 3: 文档 + 记忆** — CLAUDE.md 红线补 L3b 声明（cf_portfolio/sequential_perturbation observability-only；CF 完全隔离绝不进真实 bus/不读真实 cooldown/daily-stop；两臂同估算 delta 抵消偏差；baseline 序列保真自检低一致率拒答；退出近似+误差累积观测）。docs/to-do-list.md 路线图（#3 L3 完成 = L3a+L3b，L4 待做）。memory roadmap 标 L3 完成。
- [ ] **Step 4: 提交**

```bash
git add tests/test_cf_red_line_guard.py CLAUDE.md docs/to-do-list.md
git commit -m "docs(L3b): red-line guard + roadmap update (L3 complete)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: 全量验证

- [ ] **Step 1: 编译** — `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q .` → exit 0
- [ ] **Step 2: 全量** — `python3 -m pytest -q` → ≥ 1208 + 新增（~12），无 failure
- [ ] **Step 3: tasks.md 全勾 + 最终提交** — `git add -A && git commit -m "chore(L3b): full regression green"`

---

## Self-Review

- **Spec 覆盖**：counterfactual-portfolio-sim（Task 1：状态隔离/to_snapshot/开退/反馈/daily-stop）、sequential-perturbation-driver（Task 2：时间序/注入/隔离）、perturbation-delta-report（Task 3：两臂 delta + baseline 序列保真自检 + divergence + 诚实标注）、红线守卫（Task 4）、零回归（Task 5）。
- **类型一致**：`CounterfactualPortfolio`/`apply_decision`/`resolve_due`/`to_snapshot`/`run_arm`/`build_delta_report` 跨 task 一致。复用 `resolve_counterfactual`/`replay_decision`/`ArchetypeCooldown`/CfResult 真实签名。
- **无 placeholder**：每步真实代码。YAGNI：只模拟 _make_decision 读的状态；退出只 SL/TP/24h；daily-stop 只阈值比较。
- **红线**：CF 完全隔离，绝不 publish 真实 bus、绝不读真实 cooldown/daily-stop（独立 CF 实例）。
