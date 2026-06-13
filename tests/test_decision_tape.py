import json, os, time
from utils.decision_tape import DecisionTape, build_bundle


def _read(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def test_accept_record_written(tmp_path):
    p = str(tmp_path / "tape.jsonl")
    dt = DecisionTape(path=p, enabled=True)
    b = build_bundle(symbol="BTC-USDT", decision="accept", request_id="20260613-BTC-aa11",
                     tech_analysis={"momentum": {"rsi": 55}}, price_at_decision=50000.0,
                     regime_state="bullish", llm_output={"action": "open_long", "confidence": 70},
                     llm_audit_ref="aud-1", trade_decision_output={"plan": {"leverage": 5}})
    dt.record_decision(b)
    rows = _read(p)
    assert rows[0]["decision"] == "accept"
    assert rows[0]["symbol"] == "BTC-USDT"
    assert rows[0]["llm_output_inline"]["action"] == "open_long"
    assert rows[0]["schema_version"] == "decision_replay_record.v1"


def test_reject_record_written(tmp_path):
    p = str(tmp_path / "tape.jsonl")
    dt = DecisionTape(path=p, enabled=True)
    b = build_bundle(symbol="ETH-USDT", decision="reject", request_id="r2",
                     tech_analysis={}, price_at_decision=3000.0, regime_state="choppy",
                     llm_output=None, llm_audit_ref=None,
                     trade_decision_output={"reject_reason": "rr_below_floor:1.2"})
    dt.record_decision(b)
    rows = _read(p)
    assert rows[0]["decision"] == "reject"
    assert rows[0]["llm_output_inline"] is None
    assert rows[0]["trade_decision_output"]["reject_reason"] == "rr_below_floor:1.2"


def test_writer_failure_does_not_raise(tmp_path):
    bad = str(tmp_path / "afile")
    open(bad, "w").close()  # create a regular file
    dt = DecisionTape(path=bad + "/tape.jsonl", enabled=True)  # parent is a file, not a dir
    b = build_bundle(symbol="BTC-USDT", decision="accept", request_id="r3",
                     tech_analysis={}, price_at_decision=1.0, regime_state="x",
                     llm_output=None, llm_audit_ref=None, trade_decision_output={})
    dt.record_decision(b)  # must not raise
    assert dt.drop_count == 1


def test_flag_off_writes_nothing(tmp_path):
    p = str(tmp_path / "tape.jsonl")
    dt = DecisionTape(path=p, enabled=False)
    b = build_bundle(symbol="BTC-USDT", decision="accept", request_id="r4",
                     tech_analysis={}, price_at_decision=1.0, regime_state="x",
                     llm_output=None, llm_audit_ref=None, trade_decision_output={})
    dt.record_decision(b)
    assert not os.path.exists(p)


def test_retention_prunes_old(tmp_path):
    p = str(tmp_path / "tape.jsonl")
    dt = DecisionTape(path=p, enabled=True, retention_days=1)
    old = build_bundle(symbol="A-USDT", decision="accept", request_id="old",
                       tech_analysis={}, price_at_decision=1.0, regime_state="x",
                       llm_output=None, llm_audit_ref=None, trade_decision_output={})
    old["timestamp"] = time.time() - 3 * 86400
    dt._append_raw(old)
    fresh = build_bundle(symbol="B-USDT", decision="accept", request_id="new",
                         tech_analysis={}, price_at_decision=1.0, regime_state="x",
                         llm_output=None, llm_audit_ref=None, trade_decision_output={})
    dt.record_decision(fresh)  # triggers prune
    rows = _read(p)
    ids = {r["request_id"] for r in rows}
    assert "new" in ids and "old" not in ids
