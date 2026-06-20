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
