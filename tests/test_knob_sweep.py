import asyncio
import pytest
from utils.knob_sweep import sweep_knob


@pytest.fixture(autouse=True)
def _restore_loop():
    yield
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass


def _price_loader_tp(symbol, created_at, window_sec=86400):
    return [{"open_time": int((created_at + 60) * 1000), "high": 53400, "low": 49900, "close": 53400}]


def _accept_rec(ts):
    from tests.test_decision_replay import _accept_fixture_record
    rec = _accept_fixture_record()
    rec["timestamp"] = ts
    rec["decision"] = "accept"
    return rec


def test_sweep_collects_per_value():
    recs = [_accept_rec(1000.0 + i * 100000.0) for i in range(2)]
    result = asyncio.run(sweep_knob(recs, knob="rr_floor_long_bullish",
                                    values=[1.3, 10.0], price_loader=_price_loader_tp,
                                    fidelity_threshold=0.5))
    assert len(result) == 2
    assert {r["value"] for r in result} == {1.3, 10.0}
    for r in result:
        assert "delta" in r and "baseline_fidelity" in r and "untrustworthy" in r
        assert "sequence_len" in r


def test_sweep_explicit_value_list_order_preserved():
    recs = [_accept_rec(1000.0)]
    result = asyncio.run(sweep_knob(recs, knob="rr_floor_long_bullish",
                                    values=[1.4, 1.3, 1.6], price_loader=_price_loader_tp,
                                    fidelity_threshold=0.5))
    assert [r["value"] for r in result] == [1.4, 1.3, 1.6]


def _row(value, net_pnl, untrustworthy=False, fidelity=1.0, n=100, div=0.5):
    return {"value": value, "delta": {"net_pnl": net_pnl, "win_rate": 0.0, "max_drawdown": 0.0},
            "baseline_fidelity": fidelity, "untrustworthy": untrustworthy,
            "divergence_ratio": div, "sequence_len": n, "fidelity_note": "note"}


def test_recommend_coherent_trend():
    from utils.knob_sweep import recommend_direction
    sweep = [_row(1.3, 1.0), _row(1.4, 3.0), _row(1.5, 6.0)]
    rec = recommend_direction(sweep, min_sample=30, actionable_min_pnl=1.0)
    assert rec["verdict"] == "recommend"
    assert rec["recommended_value"] == 1.5
    assert "all_values" in rec and len(rec["all_values"]) == 3
    assert "confidence" in rec and "baseline_fidelity" in rec


def test_recommend_isolated_spike_refused():
    from utils.knob_sweep import recommend_direction
    sweep = [_row(1.3, -1.0), _row(1.4, 20.0), _row(1.5, -1.0)]
    rec = recommend_direction(sweep, min_sample=30, actionable_min_pnl=1.0)
    assert rec["verdict"] == "no_actionable_direction"
    assert rec.get("isolated_spike") is True


def test_recommend_no_trustworthy_refused():
    from utils.knob_sweep import recommend_direction
    sweep = [_row(1.3, 5.0, untrustworthy=True), _row(1.4, 6.0, n=5)]
    rec = recommend_direction(sweep, min_sample=30, actionable_min_pnl=1.0)
    assert rec["verdict"] == "no_actionable_direction"


def test_recommend_below_threshold_refused():
    from utils.knob_sweep import recommend_direction
    sweep = [_row(1.3, 0.1), _row(1.4, 0.2), _row(1.5, 0.3)]
    rec = recommend_direction(sweep, min_sample=30, actionable_min_pnl=5.0)
    assert rec["verdict"] == "no_actionable_direction"
