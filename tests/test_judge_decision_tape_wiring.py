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


def test_reject_path_captures_tech_and_llm_from_cache(tmp_path):
    tape_path = str(tmp_path / "tape.jsonl")
    j = _partial_judge(tape_path)
    j._symbol_tech_tape_cache = {"BTC-USDT": {"indicators": {"price": 100.0}}}
    j._symbol_llm_cache = {"BTC-USDT": {"action": "open_long", "confidence": 70,
                                        "reasoning": "r", "key_factors": [], "risk_warnings": []}}
    j._record_rejected_plan(
        "BTC-USDT", "open_long",
        {"entry_ref": 100.0, "stop_loss": 95.0, "take_profit": [110.0], "leverage": 5},
        score=50, confidence=60, reason="rr_below_floor:1.39<1.50",
        attribution={"request_id": "req-x"},
    )
    import json
    rows = [json.loads(l) for l in open(tape_path) if l.strip()]
    assert rows[0]["tech_analysis"] == {"indicators": {"price": 100.0}}
    assert rows[0]["llm_output_inline"]["action"] == "open_long"
    assert rows[0]["replayable"] is True


def test_reject_capture_defensive_when_caches_absent(tmp_path):
    # partial judge with NO cache attributes must NOT raise (red-line: tape never breaks decision)
    tape_path = str(tmp_path / "tape.jsonl")
    j = _partial_judge(tape_path)  # does not set _symbol_tech_cache / _symbol_llm_cache
    j._record_rejected_plan(
        "ETH-USDT", "open_short",
        {"entry_ref": 100.0, "stop_loss": 105.0, "take_profit": [90.0], "leverage": 5},
        score=-50, confidence=60, reason="rr_below_floor", attribution={"request_id": "r2"},
    )  # must not raise


def test_accept_tape_reads_llm_cache_not_hardcoded_none():
    import inspect
    src = inspect.getsource(judge_mod)
    # 两个录制点都应从 _symbol_llm_cache 取；accept 点不得再硬编码 llm_output=None
    assert src.count("_symbol_llm_cache") >= 3
    assert "llm_output=None, llm_audit_ref=None" not in src


def test_ranked_candidate_carries_llm_and_tech_for_faithful_flush():
    import inspect
    src = inspect.getsource(judge_mod)
    # 入队时把 llm_result + tech 挂到候选；flush 派发前用候选值 re-prime cache
    assert "rank_candidate['llm_output']" in src or 'rank_candidate["llm_output"]' in src
    assert "rank_candidate['tech']" in src or 'rank_candidate["tech"]' in src
    flush = src[src.index("async def _flush_ranked_candidates"):]
    assert "_symbol_llm_cache[symbol] = candidate" in flush


def test_flush_does_not_mutate_live_tech_cache():
    import inspect
    src = inspect.getsource(judge_mod)
    flush = src[src.index("async def _flush_ranked_candidates"):]
    # 观测性不变量：flush 只能写 tape 侧 cache，绝不写 live 决策读取的 _symbol_tech_cache
    assert "_symbol_tech_cache[symbol] =" not in flush
    assert "_symbol_tech_tape_cache[symbol] =" in flush
