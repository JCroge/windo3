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
    assert r["match"] is True
    assert any(d["field"] == "reasoning" and d.get("informational") for d in r["diffs"])
