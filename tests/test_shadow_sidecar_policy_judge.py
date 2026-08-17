import copy
import json
from unittest.mock import patch

import pytest

from agents.trading import judge as judge_module
from agents.trading.judge import MultiJudge
from utils.counterfactual_ledger import CounterfactualLedger
from utils import shadow_sidecar_policy
from utils.shadow_sidecar_policy import SIDECAR_POLICY_VERSION


class _RegimeManager:
    _effective_regime = "choppy"

    def snapshot(self):
        return {"effective_regime": self._effective_regime}


class _CapturingLedger:
    _enabled = True

    def __init__(self):
        self.calls = []

    def record_rejection(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "shadow-1"


class _CapturingTape:
    def __init__(self):
        self.bundles = []

    def record_decision(self, bundle):
        self.bundles.append(bundle)


def _partial_judge(*, with_tape=False):
    judge = MultiJudge.__new__(MultiJudge)
    judge._counterfactual_ledger = _CapturingLedger()
    judge._regime_manager = _RegimeManager()
    judge._decision_tape = _CapturingTape() if with_tape else None
    judge._symbol_tech_tape_cache = {}
    judge._symbol_llm_cache = {}
    judge._shadow_logger_enabled = False
    judge.config = {}
    return judge


def _plan(**overrides):
    plan = {
        "side": "long",
        "entry_ref": 100.0,
        "entry_zone": [99.0, 101.0],
        "stop_loss": 95.0,
        "take_profit": [110.0],
        "leverage": 5,
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "tactical_track_gate": "pass",
        "tactical_trend_exhaustion_warning": False,
        "tactical_weak_volume_oi": False,
        "tactical_weak_provenance": False,
    }
    plan.update(overrides)
    return plan


def _record(judge, plan):
    return judge._record_rejected_plan(
        "BTC-USDT",
        "open_long",
        plan,
        score=58,
        confidence=65,
        reason="shadow_rejection",
        attribution={"request_id": "req-sidecar"},
    )


def _captured_plan(judge):
    return judge._counterfactual_ledger.calls[0][0][2]


def test_tactical_rejection_stamps_frozen_policy_without_mutating_input():
    judge = _partial_judge()
    plan = _plan(tactical_trend_exhaustion_warning=True)
    original = copy.deepcopy(plan)

    with patch.object(judge_module.time, "time", return_value=1234.5) as clock:
        record_id = _record(judge, plan)

    stamped = _captured_plan(judge)
    expected_evidence = {
        "tactical_track_gate": "pass",
        "tactical_trend_exhaustion_warning": True,
        "tactical_weak_volume_oi": False,
        "tactical_weak_provenance": False,
    }
    assert record_id == "shadow-1"
    assert len(judge._counterfactual_ledger.calls) == 1
    assert plan == original
    assert stamped is not plan
    assert {key: stamped[key] for key in expected_evidence} == expected_evidence
    assert stamped["sidecar_policy_version"] == SIDECAR_POLICY_VERSION
    assert stamped["sidecar_live_eligible"] is False
    assert stamped["sidecar_risk_tier"] == "none"
    assert stamped["sidecar_rejection_reason"] == "trend_exhaustion_warning"
    assert stamped["sidecar_decided_at"] == 1234.5
    assert stamped["sidecar_policy_evidence"] == expected_evidence
    clock.assert_called_once_with()


@pytest.mark.parametrize(
    ("track", "exit_profile", "extra", "should_stamp"),
    [
        ("tactical", "trend_runner", {}, True),
        ("shadow_only", "tactical_v1", {}, True),
        ("main", "trend_runner", {"slot_type": "tactical"}, False),
        ("Tactical", "none", {"tactical_source": "candidate"}, False),
    ],
)
def test_tactical_detection_is_exact_track_or_exit_profile(
    track, exit_profile, extra, should_stamp
):
    judge = _partial_judge()
    plan = _plan(track=track, exit_profile=exit_profile, **extra)

    with patch.object(judge_module.time, "time", return_value=55.0) as clock:
        _record(judge, plan)

    captured = _captured_plan(judge)
    assert ("sidecar_policy_version" in captured) is should_stamp
    assert clock.call_count == int(should_stamp)


def test_main_rejection_remains_unstamped():
    judge = _partial_judge()
    plan = _plan(track="main", exit_profile="trend_runner")

    _record(judge, plan)

    captured = _captured_plan(judge)
    assert captured == plan
    assert captured is not plan
    assert not any(key.startswith("sidecar_") for key in captured)


def test_decision_tape_price_reads_from_the_stamped_plan_copy():
    judge = _partial_judge(with_tape=True)
    plan = _plan(entry_ref=100.0)
    stamped = _plan(entry_ref=222.0)
    stamped["sidecar_policy_version"] = SIDECAR_POLICY_VERSION

    with (
        patch.object(
            shadow_sidecar_policy,
            "stamp_sidecar_policy",
            return_value=stamped,
        ) as stamp,
        patch.object(judge_module.time, "time", return_value=77.0),
        patch.object(judge_module, "build_bundle", side_effect=lambda **kwargs: kwargs),
    ):
        _record(judge, plan)

    stamp_input = stamp.call_args.args[0]
    assert stamp_input == plan
    assert stamp_input is not plan
    assert stamp.call_args.kwargs == {"decided_at": 77.0}
    assert _captured_plan(judge) is stamped
    assert judge._decision_tape.bundles[0]["decision"] == "reject"
    assert judge._decision_tape.bundles[0]["price_at_decision"] == 222.0
    assert judge._decision_tape.bundles[0]["trade_decision_output"] == {
        "reject_reason": "shadow_rejection",
        "attribution": {"request_id": "req-sidecar"},
    }


def test_ledger_persists_frozen_sidecar_fields_and_copies_nested_evidence(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    ledger = CounterfactualLedger(enabled=True)
    evidence = {
        "tactical_track_gate": "pass",
        "tactical_trend_exhaustion_warning": True,
        "tactical_weak_volume_oi": True,
        "tactical_weak_provenance": False,
    }
    plan = _plan(
        tactical_trend_exhaustion_warning=True,
        tactical_weak_volume_oi=True,
        sidecar_live_eligible=False,
        sidecar_policy_version=SIDECAR_POLICY_VERSION,
        sidecar_risk_tier="none",
        sidecar_rejection_reason="trend_exhaustion_warning",
        sidecar_decided_at=9876.5,
        sidecar_policy_evidence=evidence,
    )

    ledger.record_rejection(
        "BTC-USDT", "long", plan, "choppy", 58, 65, "shadow_rejection"
    )

    events = [
        json.loads(line)
        for line in (tmp_path / "data" / "rejected_signal_events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    record = events[0]["record"]
    expected = {
        "tactical_trend_exhaustion_warning": True,
        "tactical_weak_volume_oi": True,
        "tactical_weak_provenance": False,
        "sidecar_live_eligible": False,
        "sidecar_policy_version": SIDECAR_POLICY_VERSION,
        "sidecar_risk_tier": "none",
        "sidecar_rejection_reason": "trend_exhaustion_warning",
        "sidecar_decided_at": 9876.5,
    }
    assert events[0]["event_type"] == "rejected_plan_created"
    assert {key: record[key] for key in expected} == expected
    assert record["sidecar_policy_evidence"] == evidence
    assert ledger._active[record["id"]]["sidecar_policy_evidence"] is not evidence
    assert ledger.active_count() == 1
