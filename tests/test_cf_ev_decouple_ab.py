"""ev-decouple-forward-ab: 胜率解耦放行单前向期望复核驱动单测。"""
import asyncio


def test_is_accept():
    from cf_ev_decouple_ab import _is_accept
    assert _is_accept("open_long") is True
    assert _is_accept("open_short") is True
    assert _is_accept("hold") is False
    assert _is_accept(None) is False


def test_classify_decouple_admitted(monkeypatch):
    import cf_ev_decouple_ab as m

    async def fake_replay(rec, cfg):
        # gate OFF(baseline)=accept 复现 live; gate ON(旧门)=reject → 解耦放行
        if cfg.get("ev_winrate_gate_enabled") is False:
            return {"action": "open_long"}
        return {"action": "hold", "attribution": {"blocked_by": "ev_gate"}}

    rec = {"symbol": "XLM-USDT", "decision": "accept", "replayable": True,
           "state_snapshot_before_decision": {"x": 1}}
    res = asyncio.run(m.classify_accepts([rec], replay_fn=fake_replay))
    assert res["mismatch"] == 0
    assert len(res["decouple_admitted"]) == 1
    assert len(res["both_pass"]) == 0
    assert res["admitted_reject_reasons"]["ev_gate"] == 1


def test_classify_both_pass(monkeypatch):
    import cf_ev_decouple_ab as m

    async def fake_replay(rec, cfg):
        return {"action": "open_long"}   # 两臂都 accept → 双门皆过

    rec = {"symbol": "ETH-USDT", "decision": "accept", "replayable": True,
           "state_snapshot_before_decision": {"x": 1}}
    res = asyncio.run(m.classify_accepts([rec], replay_fn=fake_replay))
    assert len(res["both_pass"]) == 1
    assert len(res["decouple_admitted"]) == 0


def test_classify_baseline_mismatch_excluded():
    import cf_ev_decouple_ab as m

    async def fake_replay(rec, cfg):
        # baseline 臂 hold（复现不出 live accept）→ 失真排除
        return {"action": "hold"}

    rec = {"symbol": "X", "decision": "accept", "replayable": True,
           "state_snapshot_before_decision": {"x": 1}}
    res = asyncio.run(m.classify_accepts([rec], replay_fn=fake_replay))
    assert res["mismatch"] == 1
    assert len(res["decouple_admitted"]) == 0
    assert len(res["both_pass"]) == 0


def test_dedup_clusters():
    from cf_ev_decouple_ab import dedup_clusters
    # 同 symbol/side: <1h 归一簇取最早; >1h 新簇
    recs = [
        {"symbol": "XLM-USDT", "_side": "long", "_created": 1000.0},
        {"symbol": "XLM-USDT", "_side": "long", "_created": 2000.0},   # +1000s <1h 同簇
        {"symbol": "XLM-USDT", "_side": "long", "_created": 6000.0},   # +4000s >1h 新簇
        {"symbol": "ETH-USDT", "_side": "long", "_created": 1500.0},   # 不同标的
    ]
    clusters = dedup_clusters(recs)
    # XLM 2 簇(1000代表, 6000代表) + ETH 1 簇 = 3
    assert len(clusters) == 3
    createds = sorted(c["_created"] for c in clusters)
    assert createds == [1000.0, 1500.0, 6000.0]


def test_settle_clusters():
    import cf_ev_decouple_ab as m

    class FakeRes:
        def __init__(self, outcome): self.outcome = outcome

    # 注入 load_bars/resolve：一簇 tp、一簇 sl、一簇无 klines
    def fake_load(db, sym, created, window=86400):
        return [] if sym == "NODATA-USDT" else [{"open_time": 1, "high": 2, "low": 1, "close": 1}]

    outcomes = {"TP-USDT": "tp", "SL-USDT": "sl"}
    def fake_resolve(plan, bars, source="tape"):
        return FakeRes(outcomes[plan["symbol"]])

    clusters = [
        {"symbol": "TP-USDT", "_tp1_dist": 0.04, "_sl_dist": 0.02, "_plan": {"symbol": "TP-USDT"}},
        {"symbol": "SL-USDT", "_tp1_dist": 0.03, "_sl_dist": 0.03, "_plan": {"symbol": "SL-USDT"}},
        {"symbol": "NODATA-USDT", "_tp1_dist": 0.03, "_sl_dist": 0.03, "_plan": {"symbol": "NODATA-USDT"}},
    ]
    s = m.settle_clusters(clusters, load_bars_fn=fake_load, resolve_fn=fake_resolve)
    assert s["tp"] == 1 and s["sl"] == 1 and s["nodata"] == 1
    assert abs(s["net_R"] - (0.04 / 0.02 - 1.0)) < 1e-9   # tp: +2R, sl: -1R → net +1R
    assert s["resolved"] == 2


def test_extract_settle_fields():
    from cf_ev_decouple_ab import extract_settle_fields
    rec = {"symbol": "XLM-USDT", "timestamp": 1000.0,
           "trade_decision_output": {"plan": {
               "side": "long", "entry_ref": 0.20, "stop_loss": 0.19,
               "take_profit": [0.22, 0.24, 0.26]}}}
    out = extract_settle_fields(rec)
    assert out["symbol"] == "XLM-USDT" and out["_side"] == "long"
    assert out["_created"] == 1000.0
    assert abs(out["_sl_dist"] - 0.05) < 1e-6      # (0.20-0.19)/0.20
    assert abs(out["_tp1_dist"] - 0.10) < 1e-6     # (0.22-0.20)/0.20
    assert out["_plan"]["side"] == "long"


def test_extract_settle_fields_invalid():
    from cf_ev_decouple_ab import extract_settle_fields
    # 缺 stop_loss → 返回 None(不可结算)
    rec = {"symbol": "X", "timestamp": 1.0,
           "trade_decision_output": {"plan": {"side": "long", "entry_ref": 1.0,
                                              "take_profit": [1.1]}}}
    assert extract_settle_fields(rec) is None


def test_fuzzy_join_real_pnl():
    from cf_ev_decouple_ab import fuzzy_join_real_pnl
    lifecycle = {"p1": {"symbol": "XLM-USDT", "side": "long", "opened_at": 1200.0,
                        "total_realized_pnl": -10.0, "reconcile_status": "matched"}}
    # 决策 ts=1000, 开仓 1200 在 [1000,1600] 窗口内 → 命中
    admitted = [{"symbol": "XLM-USDT", "_side": "long", "_created": 1000.0}]
    joined = fuzzy_join_real_pnl(admitted, lifecycle, window=600)
    assert len(joined) == 1 and joined[0]["real_pnl"] == -10.0
    # 窗口外不命中
    assert fuzzy_join_real_pnl(
        [{"symbol": "XLM-USDT", "_side": "long", "_created": 100.0}],
        lifecycle, window=600) == []


def test_bucket_verdict_thin_sample():
    from cf_ev_decouple_ab import bucket_verdict
    # 2 簇(<30) → 诚实门 INSUFFICIENT_SAMPLE
    settle = {"tp": 1, "sl": 1, "expired": 0, "nodata": 0, "resolved": 2,
              "net_R": 1.0, "r_samples": [2.0, -1.0]}
    v = bucket_verdict(settle)
    assert v["verdict"] == "INSUFFICIENT_SAMPLE"
    assert v["n"] == 2
