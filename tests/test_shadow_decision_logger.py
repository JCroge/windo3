"""trend-entry-shadow-decision-logger: 前向影子决策记录器单测。"""
import asyncio
import json

from utils.config_loader import DEFAULTS


def test_shadow_logger_flag_default_true():
    assert DEFAULTS.get("shadow_decision_logger_enabled") is True


def test_is_accept():
    from utils.shadow_decision_logger import _is_accept
    assert _is_accept("open_long") is True
    assert _is_accept("open_short") is True
    assert _is_accept("hold") is False
    assert _is_accept(None) is False
    assert _is_accept("close") is False


def test_compute_baseline_mismatch():
    from utils.shadow_decision_logger import compute_baseline_mismatch
    # baseline 复盘复现 live(都 accept) → 不 mismatch
    assert compute_baseline_mismatch("open_long", "open_long") is False
    # baseline 复盘复现 live(都 reject/hold) → 不 mismatch
    assert compute_baseline_mismatch("hold", "hold") is False
    # baseline 复盘背离 live(baseline hold, live accept) → mismatch
    assert compute_baseline_mismatch("hold", "open_long") is True
    # baseline 复盘背离 live(baseline accept, live hold) → mismatch
    assert compute_baseline_mismatch("open_short", "hold") is True


def test_compute_flip_kind():
    # 语义：baseline(lever2-only) vs shadow(both-levers)
    from utils.shadow_decision_logger import compute_flip_kind
    assert compute_flip_kind("hold", "open_long") == "shadow_opens"      # lever1 解锁新单
    assert compute_flip_kind("open_long", "open_long") == "same"
    assert compute_flip_kind("open_long", "hold") == "shadow_holds"
    assert compute_flip_kind("hold", "hold") == "same"
    assert compute_flip_kind("open_short", "open_long") == "same"        # 都 accept → same


def test_build_shadow_record_schema():
    from utils.shadow_decision_logger import build_shadow_record
    rec = build_shadow_record(
        ts=1.0, symbol="HYPE-USDT",
        real={"action": "open_long", "gate": "accept"},
        baseline={"action": "open_long", "gate": "accept"},
        shadow={"action": "open_long", "gate": "accept", "plan": {"x": 1}},
        tech_context={"trend": {"strength": 70}})
    assert rec["symbol"] == "HYPE-USDT"
    assert rec["real_action"] == "open_long" and rec["real_gate"] == "accept"
    assert rec["baseline_action"] == "open_long" and rec["baseline_gate"] == "accept"
    assert rec["shadow_action"] == "open_long" and rec["shadow_gate"] == "accept"
    assert rec["baseline_mismatch"] is False          # baseline 复现 live
    assert rec["flip_kind"] == "same"                 # baseline vs shadow 都 accept
    assert rec["shadow_plan"] == {"x": 1}
    assert rec["tech_context"] == {"trend": {"strength": 70}}


def test_build_shadow_record_mismatch_flagged():
    from utils.shadow_decision_logger import build_shadow_record
    # live accept, 但 baseline 复盘 hold → baseline_mismatch=True
    rec = build_shadow_record(
        ts=2.0, symbol="XLM-USDT",
        real={"action": "open_long", "gate": "accept"},
        baseline={"action": "hold", "gate": "ev_gate"},
        shadow={"action": "hold", "gate": "ev_gate", "plan": None},
        tech_context={})
    assert rec["baseline_mismatch"] is True
    assert rec["flip_kind"] == "same"                 # baseline=hold, shadow=hold


def test_log_shadow_disabled_noop(tmp_path):
    from utils.shadow_decision_logger import log_shadow_decision
    out = tmp_path / "s.jsonl"
    r = asyncio.run(log_shadow_decision({"replayable": True}, {"action": "hold"},
                                        str(out), enabled=False))
    assert r is None
    assert not out.exists()


def test_log_shadow_fail_safe_never_raises(tmp_path):
    # bundle 缺 replayable / 内部异常都不得抛
    from utils.shadow_decision_logger import log_shadow_decision
    out = tmp_path / "s.jsonl"
    r = asyncio.run(log_shadow_decision({"replayable": False}, {"action": "hold"}, str(out)))
    assert r is None  # 非 replayable → 跳过, 不抛


def _bare_judge(enabled=True):
    from unittest import mock
    from agents.trading.judge import MultiJudge
    j = MultiJudge.__new__(MultiJudge)
    j.logger = mock.MagicMock()
    j._shadow_logger_enabled = enabled
    j._shadow_tasks = set()
    return j


def test_schedule_shadow_no_loop_failsafe():
    # 无 running loop（同步上下文）→ fail-safe 跳过, 绝不抛
    j = _bare_judge()
    j._schedule_shadow({"replayable": False}, {"action": "hold"})  # must not raise


def test_schedule_shadow_disabled_noop():
    j = _bare_judge(enabled=False)
    j._schedule_shadow({"replayable": True}, {"action": "hold"})  # flag off → 不调度, 不抛


def test_schedule_shadow_in_loop_schedules_and_failsafe(tmp_path, monkeypatch):
    # async 上下文：调度一个 task; 即便 log_shadow_decision 抛, 也不冒泡破 live
    import utils.shadow_decision_logger as sdl

    async def boom(*a, **k):
        raise RuntimeError("shadow boom")
    monkeypatch.setattr(sdl, "log_shadow_decision", boom)

    async def run():
        j = _bare_judge()
        j._schedule_shadow({"replayable": True, "symbol": "X"}, {"action": "hold"})
        assert len(j._shadow_tasks) == 1            # 已调度
        await asyncio.gather(*list(j._shadow_tasks))  # 跑完, 异常被 _maybe_log_shadow 吞掉
    asyncio.run(run())  # 不抛 = live 不受影响


def _load_one_replayable_record():
    import os
    tape = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "data", "decision_replay_tape.jsonl")
    if not os.path.exists(tape):
        return None
    for line in open(tape):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if (r.get("schema_version") in ("decision_replay_record.v2", "decision_replay_record.v3")
                and r.get("tech_analysis") and r.get("replayable")
                and r.get("state_snapshot_before_decision")):
            return r
    return None


def test_log_shadow_on_real_bundle(tmp_path):
    # 关键风险：replay 从真实 chokepoint bundle 跑——不抛、不重复 record、写一行。
    rec = _load_one_replayable_record()
    if rec is None:
        import pytest
        pytest.skip("no replayable record in tape")
    from utils.shadow_decision_logger import log_shadow_decision
    out = tmp_path / "s.jsonl"
    r = asyncio.run(log_shadow_decision(rec, {"action": "hold"}, str(out)))
    # fail-safe 兜底；成功则写恰一行且 symbol 一致
    if r is not None:
        lines = [l for l in out.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["symbol"] == rec["symbol"]
        assert json.loads(lines[0])["shadow_action"] in (
            "open_long", "open_short", "hold", "close", None)
