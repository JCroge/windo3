import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
FIXTURE = Path(__file__).with_name("fixtures") / "tactical_v2_shadow_admission_window.json"
DRIVER = ROOT / "scripts" / "replay_tactical_v2_admission.py"

EXPECTED_EPISODE_IDS = (
    "b321a646e2a0b5f0b65e2478a4cd65bdd9af3c4652f32daa9c118ce885b439c5",
    "73576673e6db1172b618f2b387eaf7793f15baa8be05156ddc89bd5e6b0236bf",
    "4778bb24537d7ef25d348393ceceaeb18d6b6d1c50bbf792dda1732e9c3195a2",
    "96ee7827c312372cf15c241bf3f990c0fc14fe434138f98614a2df389da8b382",
    "69cd302eac72ba654afccd84797b20ab27722fabb343e979b1f43af6460c848d",
)
EXPECTED_ACCEPTED = (
    ("92ae52b2a067b12a6c00f1ef80cbfa0b", "d1e7880d"),
    ("5e19050a9f8272e6e25b928137f2ac4a", "f978fd43"),
    ("677197cc02691c503966cd09830d6164", "3bce3dd2"),
    ("7ef72c05d3297688f04a00879ed2bbd5", "d8e48042"),
    ("438d069dfe63a83cced089717ad5c7fa", "72524a13"),
)
EXPECTED_SOURCE_SHADOW_IDS = (
    "d1e7880d", "e75637c6", "15cf7200", "fc579a9b", "f978fd43", "d8dc7fca",
    "2a3aae8c", "f8a09aaa", "dfb3ab09", "bf64ccac", "80a92cd8", "1054371e",
    "a86ae45a", "3bce3dd2", "b1863929", "62bb7dda", "ad899485", "ae3aa4b1",
    "d8e48042", "ba2a1cd4", "72524a13", "90de5091",
)


@pytest.fixture(scope="module")
def report():
    from scripts.replay_tactical_v2_admission import replay_fixture

    return replay_fixture(FIXTURE)


def test_replay_driver_exists_and_locks_admission_counts(report):
    assert report.raw_candidates == 22
    assert report.accepted == 5
    assert report.accepted_by_symbol == {"BICO-USDT": 3, "PUMP-USDT": 2}
    assert report.reasons == {"duplicate_episode": 17}
    assert report.accepted_reasons == {"eligible": 3, "new_confirmed_structure": 2}
    assert report.rejected == 0
    assert report.unknown == 0


def test_normalized_opportunity_uses_bias_specific_identity():
    from scripts.replay_tactical_v2_admission import normalized_structural_opportunity

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = fixture["candidates"]

    first_neutral = normalized_structural_opportunity(rows[0]["candidate"])
    first_aligned = normalized_structural_opportunity(rows[4]["candidate"])
    adjacent_aligned = normalized_structural_opportunity(rows[6]["candidate"])
    first_pump = normalized_structural_opportunity(rows[18]["candidate"])
    second_pump = normalized_structural_opportunity(rows[20]["candidate"])

    assert first_neutral == (
        "BICO-USDT", "long", "neutral_bar", 1786205700000.0,
    )
    assert first_aligned == adjacent_aligned == (
        "BICO-USDT", "long", "aligned_token", "break_down:1d1b1b8cacfa92839a4a",
    )
    assert first_pump != second_pump
    assert first_pump[:2] == second_pump[:2] == ("PUMP-USDT", "long")


@pytest.mark.parametrize(
    "candidate_update",
    [
        {"tf_15m_bias": "bullish", "tf_15m_structure_token": None},
        {"tf_15m_bias": "neutral", "tf_15m_closed_bar_ts": None},
        {"tf_15m_bias": "unavailable"},
    ],
)
def test_normalized_opportunity_fails_closed_without_required_evidence(candidate_update):
    from scripts.replay_tactical_v2_admission import (
        OpportunityEvidenceError,
        normalized_structural_opportunity,
    )

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    candidate = {**fixture["candidates"][0]["candidate"], **candidate_update}

    with pytest.raises(OpportunityEvidenceError):
        normalized_structural_opportunity(candidate)


def test_accepted_identity_episode_and_entry_decisions_are_shared_reducer_results(report):
    identities = tuple(
        (row["candidate_id"], row["source_shadow_id"])
        for row in report.accepted_identities
    )

    assert identities == EXPECTED_ACCEPTED
    assert report.episode_ids == EXPECTED_EPISODE_IDS
    assert len(report.entry_decision_checks) == 5
    assert all(row["label"] == "entry-decision check" for row in report.entry_decision_checks)
    assert all(row["quote_evidence"] == "synthetic" for row in report.entry_decision_checks)
    assert all(row["action"] == "immediate" for row in report.entry_decision_checks)
    assert all(row["reason"] == "within_entry_drift" for row in report.entry_decision_checks)
    assert all(row["ttl_fresh"] is True for row in report.entry_decision_checks)
    assert all(row["governor_allowed"] is True for row in report.entry_decision_checks)
    assert all(row["governor_reason"] == "admitted" for row in report.entry_decision_checks)


def test_stability_covers_full_identities_episode_ids_and_every_row_reason(report):
    assert report.stable_iterations == 100
    assert report.stability_compared_fields == (
        "accepted_identities",
        "episode_ids",
        "row_reasons",
    )
    assert len(report.row_reasons) == 22
    assert sum(row["reason"] == "duplicate_episode" for row in report.row_reasons) == 17
    assert report.stability_fingerprint


def test_all_source_evidence_is_preserved_and_artifacts_are_sanitized(report):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert report.source_shadow_ids == EXPECTED_SOURCE_SHADOW_IDS
    assert tuple(
        row["candidate"]["source_shadow_id"] for row in fixture["candidates"]
    ) == EXPECTED_SOURCE_SHADOW_IDS
    assert len({row["msg_id"] for row in fixture["candidates"]}) == 22
    assert all(len(row["source_evidence_payload_hash"]) == 12 for row in fixture["candidates"])
    assert "source evidence, not a canonical local receipt hash" in (
        fixture["source_evidence"]["description"]
    )

    blocked_fragments = (
        "crypto" + "-server",
        "/" + "opt/" + "crypto-arbitrage",
        "req" + "uests",
        "ht" + "tp",
        "ss" + "h",
    )
    artifact_text = "\n".join(
        path.read_text(encoding="utf-8") for path in (FIXTURE, DRIVER, Path(__file__))
    ).lower()
    assert not any(fragment in artifact_text for fragment in blocked_fragments)


def test_replay_is_network_free_and_writes_only_under_temporary_root(tmp_path, monkeypatch):
    import scripts.replay_tactical_v2_admission as replay

    destinations = []
    initial_entries = set(tmp_path.iterdir())
    original_init = replay.TacticalStore.__init__

    def record_store_paths(store, paths):
        destinations.extend(
            [Path(paths.tactical_v2_events).resolve(), Path(paths.tactical_v2_state).resolve()]
        )
        original_init(store, paths)

    def deny_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(replay.TacticalStore, "__init__", record_store_paths)
    monkeypatch.setattr(socket, "socket", deny_network)

    isolated_report = replay.replay_fixture(FIXTURE, iterations=2, scratch_root=tmp_path)

    assert isolated_report.raw_candidates == 22
    assert destinations
    assert all(path.is_relative_to(tmp_path.resolve()) for path in destinations)
    assert set(tmp_path.iterdir()) == initial_entries


def test_cli_prints_deterministic_json_and_safety_limitations(report):
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--fixture", str(FIXTURE)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == report.to_dict()
    assert payload["entry-decision check"] == payload["entry_decision_checks"]
    assert payload["exchange_fill"] is False
    assert payload["historical_executable_quote_available"] is False
    assert payload["protection_evidence_proven"] is False
    assert payload["live_rollout_ready"] is False
    assert payload["parity_expected_values_passed"] is True
