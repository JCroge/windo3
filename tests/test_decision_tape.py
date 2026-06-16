import json, os, time
from utils.decision_tape import DecisionTape, build_bundle, SCHEMA_VERSION


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
    assert rows[0]["schema_version"] == "decision_replay_record.v3"


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
    dt = DecisionTape(path=p, enabled=True, retention_days=1, prune_every=1)
    old = build_bundle(symbol="A-USDT", decision="accept", request_id="old",
                       tech_analysis={}, price_at_decision=1.0, regime_state="x",
                       llm_output=None, llm_audit_ref=None, trade_decision_output={})
    old["timestamp"] = time.time() - 3 * 86400
    dt._append_raw(old)
    fresh = build_bundle(symbol="B-USDT", decision="accept", request_id="new",
                         tech_analysis={}, price_at_decision=1.0, regime_state="x",
                         llm_output=None, llm_audit_ref=None, trade_decision_output={})
    dt.record_decision(fresh)  # prune_every=1 → triggers prune
    rows = _read(p)
    ids = {r["request_id"] for r in rows}
    assert "new" in ids and "old" not in ids


def test_prune_throttled_not_every_write(tmp_path):
    # prune runs at most once per prune_every writes — old record survives until then.
    p = str(tmp_path / "tape.jsonl")
    dt = DecisionTape(path=p, enabled=True, retention_days=1, prune_every=3)
    old = build_bundle(symbol="A-USDT", decision="accept", request_id="old",
                       tech_analysis={}, price_at_decision=1.0, regime_state="x",
                       llm_output=None, llm_audit_ref=None, trade_decision_output={})
    old["timestamp"] = time.time() - 3 * 86400
    dt._append_raw(old)
    for i in range(2):  # 2 writes < prune_every=3 → no prune yet
        dt.record_decision(build_bundle(symbol="B-USDT", decision="accept", request_id=f"w{i}",
                                        tech_analysis={}, price_at_decision=1.0, regime_state="x",
                                        llm_output=None, llm_audit_ref=None, trade_decision_output={}))
    assert "old" in {r["request_id"] for r in _read(p)}  # still present
    dt.record_decision(build_bundle(symbol="B-USDT", decision="accept", request_id="w2",
                                    tech_analysis={}, price_at_decision=1.0, regime_state="x",
                                    llm_output=None, llm_audit_ref=None, trade_decision_output={}))
    assert "old" not in {r["request_id"] for r in _read(p)}  # 3rd write triggers prune


def _snap():
    return {"_available_balance": 1000.0}


def test_replayable_requires_nonempty_tech():
    # 有快照但 tech 空 -> 不可回放
    b = build_bundle(symbol="BTC-USDT", decision="reject", request_id="r",
                     tech_analysis={}, price_at_decision=1.0, regime_state="choppy",
                     llm_output=None, llm_audit_ref=None,
                     trade_decision_output={}, state_snapshot=_snap())
    assert b["replayable"] is False
    # 有快照且 tech 非空 -> 可回放
    b2 = build_bundle(symbol="BTC-USDT", decision="reject", request_id="r",
                      tech_analysis={"indicators": {"price": 1.0}}, price_at_decision=1.0,
                      regime_state="choppy", llm_output={"action": "hold"}, llm_audit_ref=None,
                      trade_decision_output={}, state_snapshot=_snap())
    assert b2["replayable"] is True


def test_missing_snapshot_not_replayable():
    b = build_bundle(symbol="BTC-USDT", decision="reject", request_id="r",
                     tech_analysis={"indicators": {}}, price_at_decision=1.0,
                     regime_state="choppy", llm_output=None, llm_audit_ref=None,
                     trade_decision_output={}, state_snapshot=None)
    assert b["replayable"] is False


def test_schema_version_is_v3():
    assert SCHEMA_VERSION == "decision_replay_record.v3"


def test_build_bundle_records_config_snapshot():
    b = build_bundle(
        symbol="X-USDT", decision="reject", request_id=None,
        tech_analysis={"rule_signal": {}}, price_at_decision=1.0,
        regime_state="mixed", llm_output=None, llm_audit_ref=None,
        trade_decision_output={}, state_snapshot={"_recent_wins": 1},
        config_snapshot={"rr_floor_default": 1.5, "phase2_bucketed_ev_enabled": True},
    )
    assert b["config_snapshot"] == {"rr_floor_default": 1.5, "phase2_bucketed_ev_enabled": True}
    assert b["schema_version"] == "decision_replay_record.v3"


def test_build_bundle_config_snapshot_optional():
    b = build_bundle(
        symbol="X-USDT", decision="reject", request_id=None,
        tech_analysis={}, price_at_decision=1.0, regime_state="mixed",
        llm_output=None, llm_audit_ref=None, trade_decision_output={},
    )
    assert b.get("config_snapshot") is None
