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
