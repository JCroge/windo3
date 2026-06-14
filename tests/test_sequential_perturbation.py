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
