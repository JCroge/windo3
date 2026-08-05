import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest


FIXTURE = Path(__file__).with_name("fixtures") / "tactical_v2_reproduced_window.json"


@pytest.fixture
def report():
    from scripts.replay_tactical_v2 import replay_fixture

    return replay_fixture(FIXTURE)


def test_reproduced_window_has_one_attempt_per_episode(report):
    assert report.raw_candidates == 143
    assert report.episodes == 14
    assert report.duplicate_live_attempts == 0
    assert report.stale_chase_fills == 0
    assert report.tp_before_entry_fills == 0
    assert report.unclassified_mismatches == 0


def test_reproduced_live_pnl_ledger_reconciles_exactly(report):
    assert report.historical_live_closes == 7
    assert Decimal(str(report.historical_live_pnl_usdt)) == Decimal("-1.4437")
    assert Decimal(str(report.invalidated_pnl_usdt)) == Decimal("-3.2773")
    assert Decimal(str(report.other_live_pnl_usdt)) == Decimal("1.8336")


def test_legacy_evidence_gaps_are_classified_without_fabricating_quotes(report):
    assert report.classified_mismatches == 14
    assert {row["mismatch_category"] for row in report.intent_comparisons} == {
        "legacy_executable_quote_unavailable"
    }
    assert all(row["attempts"] == 1 for row in report.intent_comparisons)
    assert all(row["ticks_processed"] == 0 for row in report.intent_comparisons)
    assert all(row["replay_fill_state"] == "not_evaluable" for row in report.intent_comparisons)


def test_replay_cli_emits_json_and_exits_zero_for_safety_gate():
    result = subprocess.run(
        [sys.executable, "scripts/replay_tactical_v2.py", "--fixture", str(FIXTURE)],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["raw_candidates"] == 143
    assert payload["episodes"] == 14
    assert payload["safety_gate_passed"] is True


def test_replay_rejects_unclassified_observed_fill_mismatch(tmp_path):
    from scripts.replay_tactical_v2 import replay_fixture

    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["episodes"][0]["tick_evidence_status"] = "recorded"
    bad_fixture = tmp_path / "bad.json"
    bad_fixture.write_text(json.dumps(raw), encoding="utf-8")

    report = replay_fixture(bad_fixture)

    assert report.unclassified_mismatches == 1
    assert report.safety_gate_passed is False


def test_replay_uses_shared_full_tp1_exit_reducer():
    from scripts.replay_tactical_v2 import replay_fixture

    fixture = {
        "episodes": [{
            "episode_id": "episode-tp1",
            "raw_count": 1,
            "source_timestamps": {"created_at": 1000.0, "resolved_at": 1010.0},
            "candidate": {
                "candidate_id": "candidate-tp1",
                "source_shadow_id": "shadow-tp1",
                "namespace": "testnet",
                "symbol": "WLD-USDT",
                "side": "long",
                "entry_ref": 1.0,
                "stop_loss": 0.95,
                "take_profit": 1.08,
                "leverage": 5,
                "tactical_source": "test",
            },
            "structure": {
                "tf_15m_closed_bar_ts": 900.0,
                "tf_15m_structure_token": "break-up-1",
            },
            "ticks": [
                {"bid": 1.0, "ask": 1.001, "observed_at": 1000.0},
                {"bid": 1.08, "ask": 1.081, "observed_at": 1010.0},
            ],
            "tick_evidence_status": "recorded",
            "observed_fill": {
                "status": "executable_fill",
                "price": 1.001,
                "executable_quote_proven": True,
            },
            "observed_exit": {"reason": "tactical_tp1", "price": 1.08},
        }],
        "historical_live_closes": [],
    }

    report = replay_fixture(fixture)

    assert report.intent_comparisons[0]["replay_fill_state"] == "filled"
    assert report.intent_comparisons[0]["replay_exit_reason"] == "tactical_tp1"
    assert report.full_tp1_violations == 0
    assert report.safety_gate_passed is True
