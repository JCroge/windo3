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
    # TP-hit loader scaled to the L2 _accept_fixture_record entry (~50000, TP 53360):
    # the real _make_decision produces a 50000-scale plan, so the bar must clear TP at
    # that scale (high > 53360, low above SL 47760) for a genuine win. (The plan's
    # literal 109/110/111 values target a ~100-scale entry like Task1's cf-portfolio
    # unit fixture; here the entry comes from the real fixture, so we scale to match.)
    return [{"open_time": int((created_at + 60) * 1000),
             "high": 53400.0, "low": 49900.0, "close": 53400.0}]


def _accept_rec(ts):
    from tests.test_decision_replay import _accept_fixture_record
    rec = _accept_fixture_record()
    rec["timestamp"] = ts
    rec["decision"] = "accept"
    return rec


def test_run_arm_opens_and_resolves():
    recs = [_accept_rec(1000.0)]
    arm = asyncio.run(run_arm(recs, config={}, price_loader=_price_loader_tp, initial_equity=1000.0))
    assert arm["cf_open_count"] >= 1
    assert arm["final_equity"] >= 1000.0
    assert "decisions" in arm and len(arm["decisions"]) == 1


def test_run_arm_records_decisions_in_order():
    recs = [_accept_rec(1000.0), _accept_rec(100000.0)]
    arm = asyncio.run(run_arm(recs, config={}, price_loader=_price_loader_tp, initial_equity=1000.0))
    assert [d["timestamp"] for d in arm["decisions"]] == [1000.0, 100000.0]


def test_delta_report_two_arms_and_fidelity():
    from utils.sequential_perturbation import build_delta_report
    recs = [_accept_rec(1000.0 + i * 100000.0) for i in range(3)]
    for r in recs:
        r["decision"] = "accept"
    rep = asyncio.run(build_delta_report(
        recs, baseline_config={}, perturbed_config={"rr_floor_default": 10.0,
        "rr_floor_long_bullish": 10.0, "rr_floor_long_aligned_choppy": 10.0},
        price_loader=_price_loader_tp, fidelity_threshold=0.5))
    assert "delta" in rep and "baseline" in rep and "perturbed" in rep
    assert "baseline_fidelity" in rep["metadata"]
    assert "divergence_ratio" in rep["metadata"]
    assert rep["metadata"]["baseline_fidelity"] >= 0.5


def test_delta_report_low_fidelity_untrustworthy():
    from utils.sequential_perturbation import build_delta_report
    recs = [_accept_rec(1000.0)]
    recs[0]["decision"] = "reject"
    rep = asyncio.run(build_delta_report(recs, baseline_config={}, perturbed_config={},
                      price_loader=_price_loader_tp, fidelity_threshold=0.8))
    assert rep["metadata"]["untrustworthy"] is True
    assert rep.get("delta") is None


from utils.cf_portfolio import CounterfactualPortfolio
from utils.sequential_perturbation import _seed_cf_prior
from utils.sequential_perturbation import _gate_of_recorded, _gate_of_replayed


def test_gate_extraction_prefix():
    rec = {"decision": "reject",
           "trade_decision_output": {"reject_reason": "rr_below_floor:1.37<1.50"}}
    assert _gate_of_recorded(rec) == "rr_below_floor"
    rec_acc = {"decision": "accept", "trade_decision_output": {}}
    assert _gate_of_recorded(rec_acc) == "accept"
    assert _gate_of_replayed({"action": "open_long"}) == "accept"
    d = {"action": "hold", "attribution": {"blocked_by": "ev_gate:EV=-0.41"}}
    assert _gate_of_replayed(d) == "ev_gate"


def test_changed_gate_counts_as_non_reproduction():
    recorded = {"action": "hold", "attribution": {"blocked_by": "ev_gate:x"}}
    rec = {"decision": "reject",
           "trade_decision_output": {"reject_reason": "rr_below_floor:1.37<1.50"}}
    assert _gate_of_replayed(recorded) != _gate_of_recorded(rec)


def test_seed_warms_rolling_window_from_recorded_rate():
    cf = CounterfactualPortfolio(initial_equity=1000.0, rolling_window_size=20)
    rec = {"state_snapshot_before_decision": {
        "_recent_win_rate": 0.45, "_recent_wins": 9, "_total_completed_trades": 52,
        "_archetype_cooldown": {"_history": {}, "_cooldown_until": {}}}}
    _seed_cf_prior(cf, rec)
    assert len(cf._cf_win_window) == 20
    assert cf.to_snapshot()["_recent_win_rate"] == 0.45


def test_seed_window_evicted_by_cf_results_after_full_turnover():
    cf = CounterfactualPortfolio(initial_equity=1000.0, rolling_window_size=20)
    rec = {"state_snapshot_before_decision": {
        "_recent_win_rate": 0.45, "_recent_wins": 9, "_total_completed_trades": 52,
        "_archetype_cooldown": {"_history": {}, "_cooldown_until": {}}}}
    _seed_cf_prior(cf, rec)
    for _ in range(20):
        cf._open["X-USDT"] = {"resolved_ts": 1.0, "net_usdt": 1.0,
                              "archetype": "t", "created_at": 0.0}
        cf.resolve_due(2.0)
    assert cf.to_snapshot()["_recent_win_rate"] == 1.0


import json
import os as _os
from utils.sequential_perturbation import build_delta_report as _build_delta_report

_TAPE = _os.path.join(_os.path.dirname(__file__), "..", "data", "decision_replay_tape.jsonl")
_KLINES = _os.path.join(_os.path.dirname(__file__), "..", "data", "klines_1s.db")


def _load_v2_rr(limit=None):
    if not _os.path.exists(_TAPE):
        pytest.skip("no live tape available")
    out = []
    for line in open(_TAPE):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("schema_version") != "decision_replay_record.v2":
            continue
        if not (r.get("tech_analysis") or {}):
            continue
        out.append(r)
        if limit is not None and len(out) >= limit:
            break
    return out


def _e2e_loader(symbol, created_at, window_sec):
    import sqlite3
    if not _os.path.exists(_KLINES):
        return []
    lo, hi = int(created_at * 1000), int((created_at + window_sec) * 1000)
    conn = sqlite3.connect(_KLINES)
    try:
        rows = conn.execute(
            "SELECT open_time,high,low,close FROM klines WHERE symbol=? "
            "AND open_time>=? AND open_time<=? ORDER BY open_time",
            (symbol, lo, hi)).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    return [{"open_time": t, "high": h, "low": l, "close": c} for t, h, l, c in rows]


def test_relaxing_floor_breaks_deadlock_perturbed_opens():
    # End-to-end on the real live tape: prove the EV cold-start deadlock is broken.
    # Relaxing ONLY `rr_floor_default` (and leaving the EV gate fully intact) is the
    # DISCRIMINATING test. Pre-fix, the CF EV gate cold-started at the 40% fallback win
    # rate, so EV<floor blocked every perturbed open no matter how far R:R was relaxed
    # -> perturbed_cf_open_count == 0 and the assertion fails. Post-fix, the sequence is
    # warm-seeded from the recorded prior (0.45 rolling rate via `_seed_cf_prior`), so
    # once the R:R floor is relaxed the +EV setups clear the EV gate and open CF
    # positions -> perturbed_cf_open_count > 0 and the assertion passes.
    #
    # Do NOT disable the EV gate here: setting ev_min_threshold=-999 makes the test pass
    # even on UNFIXED code (cold p_win becomes irrelevant when the threshold is -999),
    # so it would no longer validate the EV cold-start fix at all. The floor-only
    # perturbation is exactly what couples the assertion to the seeded warm prior.
    #
    # Replays the FULL set of v2-with-tech records (~630) x 2 arms, so this takes
    # 1-2 minutes; the baseline window contains no opens (baseline_cf_open_count == 0).
    recs = _load_v2_rr()
    if not recs:
        pytest.skip("no v2 tape records")
    perturbed = {"rr_floor_default": 0.3}
    rep = asyncio.run(_build_delta_report(
        recs, {}, perturbed, _e2e_loader, fidelity_threshold=0.0))
    print("perturbed_cf_open_count=", rep["metadata"]["perturbed_cf_open_count"])
    assert rep["metadata"]["perturbed_cf_open_count"] > 0


def test_perturbation_overlays_on_production_base_only_target():
    from utils.decision_replay import production_base_config
    base = production_base_config()
    perturb = {"rr_floor_default": 0.3}
    effective = {**base, **perturb}
    assert effective["rr_floor_default"] == 0.3
    assert effective["phase2_signal_confidence_split_enabled"] is True
    assert effective["min_confidence"] == base["min_confidence"]
