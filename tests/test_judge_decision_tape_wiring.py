import inspect
import json
from agents.trading import judge as judge_mod
from agents.trading.judge import MultiJudge
from utils.decision_tape import DecisionTape


def test_judge_imports_decision_tape():
    src = inspect.getsource(judge_mod)
    assert "decision_tape" in src or "DecisionTape" in src
    assert src.count("record_decision") >= 2


class _RM:
    _effective_regime = "choppy"


class _Ledger:
    _enabled = True

    def record_rejection(self, *a, **k):
        pass


def _partial_judge(tape_path):
    """MultiJudge via __new__ (bypass __init__), wired with just what
    _record_rejected_plan needs + a real decision tape."""
    j = MultiJudge.__new__(MultiJudge)
    j._decision_tape = DecisionTape(path=tape_path, enabled=True)
    j._regime_manager = _RM()
    j._counterfactual_ledger = _Ledger()
    return j


def test_reject_path_writes_to_tape(tmp_path):
    tape_path = str(tmp_path / "tape.jsonl")
    j = _partial_judge(tape_path)
    j._record_rejected_plan(
        "BTC-USDT", "open_long",
        {"entry_ref": 100.0, "stop_loss": 95.0, "take_profit": [110.0], "leverage": 5},
        score=50, confidence=60, reason="rr_below_floor:1.2",
        attribution={"request_id": "req-x"},
    )
    rows = [json.loads(l) for l in open(tape_path) if l.strip()]
    assert rows[0]["decision"] == "reject"
    assert rows[0]["trade_decision_output"]["reject_reason"] == "rr_below_floor:1.2"
    assert rows[0]["request_id"] == "req-x"


def test_missing_tape_does_not_break_reject(tmp_path):
    # Partial construction without _decision_tape must NOT raise (guarded access).
    j = MultiJudge.__new__(MultiJudge)
    j._regime_manager = _RM()
    j._counterfactual_ledger = _Ledger()
    # no j._decision_tape set
    j._record_rejected_plan(
        "BTC-USDT", "open_short",
        {"entry_ref": 100.0, "stop_loss": 105.0, "take_profit": [90.0], "leverage": 5},
        score=-50, confidence=60, reason="short_gate", attribution={"request_id": "r2"},
    )  # must not raise
