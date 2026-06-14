import asyncio
import pytest
from utils.perturbation_replay import replay_with_perturbation, _decision_class


@pytest.fixture(autouse=True)
def _restore_loop():
    yield
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass


def _accept_record():
    from tests.test_decision_replay import _accept_fixture_record
    return _accept_fixture_record()


def test_decision_class():
    assert _decision_class({"action": "open_long"}) == "accept"
    assert _decision_class({"action": "open_short"}) == "accept"
    assert _decision_class({"action": "hold"}) == "reject"
    assert _decision_class({"action": None}) == "reject"
    assert _decision_class(None) == "reject"


def test_baseline_reproduces_accept_no_flip_when_same_config():
    rec = _accept_record()
    rec["decision"] = "accept"
    r = asyncio.run(replay_with_perturbation(rec, baseline_config={}, perturbed_config={}))
    assert r["status"] == "ok"
    assert r["flipped"] is False
    assert r["flip_kind"] == "none"


def test_perturb_tighten_rr_floor_flips_accept_to_reject():
    rec = _accept_record()
    rec["decision"] = "accept"
    r = asyncio.run(replay_with_perturbation(rec, baseline_config={},
                                             perturbed_config={"rr_floor_default": 10.0,
                                                               "rr_floor_long_bullish": 10.0,
                                                               "rr_floor_long_aligned_choppy": 10.0}))
    assert r["status"] == "ok"
    assert r["flipped"] is True
    assert r["flip_kind"] == "accept_to_reject"


def test_baseline_mismatch_excluded():
    rec = _accept_record()
    rec["decision"] = "reject"
    r = asyncio.run(replay_with_perturbation(rec, baseline_config={}, perturbed_config={}))
    assert r["status"] == "baseline_mismatch"
    assert r["flip_kind"] == "baseline_mismatch"


def test_not_replayable_returns_status():
    rec = {"replayable": False, "decision": "accept", "state_snapshot_before_decision": None}
    r = asyncio.run(replay_with_perturbation(rec, baseline_config={}, perturbed_config={}))
    assert r["status"] == "not_replayable"


def test_build_report_buckets_and_metadata():
    from utils.perturbation_replay import build_perturbation_report
    rec = _accept_record()
    rec["decision"] = "accept"
    rec.setdefault("trade_decision_output", {})
    rec["trade_decision_output"]["reject_reason"] = None
    rec["effective_regime"] = rec["regime_state"]
    rec["side"] = "long"
    recs = [dict(rec) for _ in range(3)]
    rep = asyncio.run(build_perturbation_report(
        recs, baseline_config={}, perturbed_config={"rr_floor_default": 10.0,
        "rr_floor_long_bullish": 10.0, "rr_floor_long_aligned_choppy": 10.0},
        min_sample=1, lowconf_sample=2))
    assert "buckets" in rep
    assert rep["metadata"]["perturbed_knobs"]["rr_floor_default"] == 10.0
    assert "fidelity_note" in rep["metadata"]
    some_bucket = next(iter(rep["buckets"].values()))
    assert some_bucket["flip_count"] == 3


def test_build_report_skips_not_replayable():
    from utils.perturbation_replay import build_perturbation_report
    recs = [{"replayable": False, "decision": "accept", "state_snapshot_before_decision": None}]
    rep = asyncio.run(build_perturbation_report(recs, baseline_config={},
                      perturbed_config={}, min_sample=1, lowconf_sample=2))
    assert rep["metadata"]["skipped_not_replayable"] == 1
