import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.counterfactual_ledger import CounterfactualLedger


def _ledger(tmp_path):
    lg = CounterfactualLedger(enabled=True)
    lg._events_path = str(tmp_path / "rej.jsonl")
    lg._lifecycle_path = str(tmp_path / "rej_lifecycle.json")
    return lg


def test_record_rejection_includes_tech_context(tmp_path):
    lg = _ledger(tmp_path)
    plan = {"entry_price": 100.0, "stop_loss": 98.0, "take_profit": [103.0],
            "leverage": 5, "effective_risk_reward_ratio": 1.4}
    tc = {"direction": "bullish", "strength": 70, "higher_tf_bias": "neutral",
          "daily_bias": "neutral", "pre_12h_return_pct": 0.08, "position_in_24h_range": 0.6}
    lg.record_rejection("HYPE-USDT", "long", plan, "choppy", 60, 65,
                        "rr_below_floor:1.40<1.50", attribution=None, tech_context=tc)
    rec = list(lg._active.values())[-1]
    assert rec["tech_context"] == tc


def test_record_rejection_tech_context_defaults_empty(tmp_path):
    lg = _ledger(tmp_path)
    plan = {"entry_price": 100.0, "stop_loss": 98.0, "take_profit": [103.0], "leverage": 5}
    lg.record_rejection("ADA-USDT", "long", plan, "choppy", 60, 65, "rr_below_floor:1.40<1.50")
    rec = list(lg._active.values())[-1]
    assert rec.get("tech_context") == {}
