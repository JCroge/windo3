"""cf-choppy-neutral-tp1-floor-ab 驱动单测。"""
import asyncio


def test_is_accept():
    from cf_choppy_neutral_tp1_floor_ab import _is_accept
    assert _is_accept("open_long") is True
    assert _is_accept("open_short") is True
    assert _is_accept("hold") is False
    assert _is_accept(None) is False


def _rec(symbol, regime, direction, action="open_long"):
    return {"symbol": symbol, "decision": "accept", "replayable": True,
            "state_snapshot_before_decision": {"x": 1},
            "regime_state": regime,
            "tech_analysis": {"trend": {"direction": direction}},
            "trade_decision_output": {"plan": {"side": "long"}},
            "_action": action}


def test_scope_filter_choppy_neutral_long():
    from cf_choppy_neutral_tp1_floor_ab import scope_filter
    recs = [
        _rec("A-USDT", "choppy", "neutral"),          # 命中主桶
        _rec("B-USDT", "mixed", "neutral"),           # 不命中 choppy
        _rec("C-USDT", "choppy", "bullish"),          # 非 neutral
        _rec("D-USDT", "bullish", "neutral"),         # 非 choppy
    ]
    out = scope_filter(recs, regime="choppy")
    syms = {r["symbol"] for r in out}
    assert syms == {"A-USDT"}


def test_scope_filter_mixed_sidecar():
    from cf_choppy_neutral_tp1_floor_ab import scope_filter
    recs = [_rec("A-USDT", "choppy", "neutral"), _rec("B-USDT", "mixed", "neutral")]
    out = scope_filter(recs, regime="mixed")
    assert {r["symbol"] for r in out} == {"B-USDT"}


def test_scope_filter_excludes_short():
    from cf_choppy_neutral_tp1_floor_ab import scope_filter
    r = _rec("S-USDT", "choppy", "neutral")
    r["trade_decision_output"]["plan"]["side"] = "short"
    assert scope_filter([r], regime="choppy") == []


def test_classify_tp1_floor_rejected():
    import cf_choppy_neutral_tp1_floor_ab as m

    async def fake_replay(rec, cfg):
        # ladder ON(baseline)=accept 复现; ladder OFF(TP1)=rr_below_floor reject → 避开
        if cfg.get("ladder_rr_enabled") is True:
            return {"action": "open_long"}
        return {"action": "hold", "attribution": {"blocked_by": "rr_below_floor:1.30<1.50"}}

    rec = {"symbol": "HYPE-USDT", "decision": "accept", "replayable": True,
           "state_snapshot_before_decision": {"x": 1}}
    res = asyncio.run(m.classify_accepts([rec], replay_fn=fake_replay))
    assert res["mismatch"] == 0
    assert len(res["tp1_floor_rejected"]) == 1
    assert len(res["survives_tp1_floor"]) == 0
    assert len(res["other_flip"]) == 0
    assert res["rejected_reasons"]["rr_below_floor"] == 1


def test_classify_survives():
    import cf_choppy_neutral_tp1_floor_ab as m

    async def fake_replay(rec, cfg):
        return {"action": "open_long"}   # 两臂都过 → 卡 TP1 仍过

    rec = {"symbol": "X-USDT", "decision": "accept", "replayable": True,
           "state_snapshot_before_decision": {"x": 1}}
    res = asyncio.run(m.classify_accepts([rec], replay_fn=fake_replay))
    assert len(res["survives_tp1_floor"]) == 1
    assert len(res["tp1_floor_rejected"]) == 0


def test_classify_other_flip_excluded():
    import cf_choppy_neutral_tp1_floor_ab as m

    async def fake_replay(rec, cfg):
        # CF 臂 reject 但原因非 rr_below_floor → other_flip，不计 tp1_floor_rejected
        if cfg.get("ladder_rr_enabled") is True:
            return {"action": "open_long"}
        return {"action": "hold", "attribution": {"blocked_by": "quality_gate"}}

    rec = {"symbol": "Q-USDT", "decision": "accept", "replayable": True,
           "state_snapshot_before_decision": {"x": 1}}
    res = asyncio.run(m.classify_accepts([rec], replay_fn=fake_replay))
    assert len(res["other_flip"]) == 1
    assert len(res["tp1_floor_rejected"]) == 0


def test_classify_baseline_mismatch_excluded():
    import cf_choppy_neutral_tp1_floor_ab as m

    async def fake_replay(rec, cfg):
        return {"action": "hold"}   # baseline 复现不出 live accept → 失真排除

    rec = {"symbol": "M-USDT", "decision": "accept", "replayable": True,
           "state_snapshot_before_decision": {"x": 1}}
    res = asyncio.run(m.classify_accepts([rec], replay_fn=fake_replay))
    assert res["mismatch"] == 1
    assert len(res["tp1_floor_rejected"]) == 0
    assert len(res["survives_tp1_floor"]) == 0
    assert len(res["other_flip"]) == 0


def test_extract_settle_fields_contract():
    from cf_choppy_neutral_tp1_floor_ab import extract_settle_fields
    rec = {"symbol": "XLM-USDT", "timestamp": 1000.0,
           "trade_decision_output": {"plan": {
               "side": "long", "entry_ref": 0.20, "stop_loss": 0.19,
               "take_profit": [0.22, 0.24, 0.26]}}}
    out = extract_settle_fields(rec)
    assert out["_side"] == "long" and out["_created"] == 1000.0
    assert abs(out["_sl_dist"] - 0.05) < 1e-6
    assert abs(out["_tp1_dist"] - 0.10) < 1e-6
    # 结算契约：传 resolve 所需字段，不传原始 plan 的 entry_ref
    assert out["_plan"]["entry_price"] == 0.20
    assert out["_plan"]["created_at"] == 1000.0
    assert "entry_ref" not in out["_plan"]


def test_extract_settle_fields_invalid():
    from cf_choppy_neutral_tp1_floor_ab import extract_settle_fields
    rec = {"symbol": "X", "timestamp": 1.0,
           "trade_decision_output": {"plan": {"side": "long", "entry_ref": 1.0,
                                              "take_profit": [1.1]}}}
    assert extract_settle_fields(rec) is None


def test_settle_clusters_real_resolve():
    """不 mock resolve_counterfactual：锁死 _plan 契约不被 mock 掩盖。"""
    import cf_choppy_neutral_tp1_floor_ab as m
    from utils.counterfactual_pnl import resolve_counterfactual
    rec = {"symbol": "TST-USDT", "timestamp": 1000.0,
           "trade_decision_output": {"plan": {
               "side": "long", "entry_ref": 100.0, "stop_loss": 95.0,
               "take_profit": [110.0, 120.0, 130.0]}}}
    field = m.extract_settle_fields(rec)
    tp_bars = [{"open_time": 1001_000, "high": 105.0, "low": 99.0, "close": 104.0},
               {"open_time": 1002_000, "high": 112.0, "low": 104.0, "close": 111.0}]
    s_tp = m.settle_clusters([field], load_bars_fn=lambda *a, **k: tp_bars,
                             resolve_fn=resolve_counterfactual)
    assert s_tp["tp"] == 1 and s_tp["net_R"] > 0


def test_dedup_clusters():
    from cf_choppy_neutral_tp1_floor_ab import dedup_clusters
    recs = [
        {"symbol": "XLM-USDT", "_side": "long", "_created": 1000.0},
        {"symbol": "XLM-USDT", "_side": "long", "_created": 2000.0},
        {"symbol": "XLM-USDT", "_side": "long", "_created": 6000.0},
        {"symbol": "ETH-USDT", "_side": "long", "_created": 1500.0},
    ]
    assert len(dedup_clusters(recs)) == 3


def test_bucket_verdict_thin_sample():
    from cf_choppy_neutral_tp1_floor_ab import bucket_verdict
    settle = {"tp": 1, "sl": 1, "expired": 0, "nodata": 0, "resolved": 2,
              "net_R": 1.0, "r_samples": [2.0, -1.0]}
    v = bucket_verdict(settle)
    assert v["verdict"] == "INSUFFICIENT_SAMPLE" and v["n"] == 2
