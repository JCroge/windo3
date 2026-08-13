import json
import socket
import subprocess
import sys
from copy import deepcopy
from dataclasses import fields as dataclass_fields
from dataclasses import replace
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
FIXTURE = Path(__file__).with_name("fixtures") / "tactical_v2_shadow_admission_window.json"
DRIVER = ROOT / "scripts" / "replay_tactical_v2_admission.py"
PINNED_FIXTURE_SHA256 = "65dd6e2f3cd21dd1aaa9d163126c818f0a0db8f92997d80f24e548f44e72fa5f"
PINNED_PARITY_SHA256 = "d3cd2fe742b5bae5dafa1e018d007bb431d4f2314bcb13c31508e3697c0d02f5"
PINNED_REPLAY_EVIDENCE_SHA256 = (
    "897580be19ba533e1a6b0bf4877d85ccbecf572143f23f35d1e83a8150499cd3"
)
PINNED_STABILITY_SHA256 = (
    "73175bcdd2435db7c7be81f242be5264390acc318655c8c7cd758dd293ca2ab0"
)
EXPECTED_ROOT_FIELDS = frozenset({
    "schema_version",
    "source_evidence",
    "initial_episode_state",
    "candidates",
})
EXPECTED_SOURCE_FIELDS = frozenset({
    "description",
    "topic",
    "window_start_epoch",
    "window_end_epoch",
    "raw_candidate_count",
})
EXPECTED_ROW_FIELDS = frozenset({
    "msg_id",
    "source_evidence_payload_hash",
    "journal_timestamp",
    "candidate",
})

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
EXPECTED_CANDIDATE_FIELDS = frozenset({
    "candidate_id",
    "created_at",
    "entry_ref",
    "leverage",
    "namespace",
    "side",
    "source_shadow_id",
    "stop_loss",
    "symbol",
    "tactical_cost_gate",
    "tactical_ev",
    "tactical_rr",
    "tactical_source",
    "take_profit",
    "tf_15m_available",
    "tf_15m_bias",
    "tf_15m_block_long",
    "tf_15m_block_short",
    "tf_15m_closed_bar_ts",
    "tf_15m_structure_token",
})


@pytest.fixture(scope="module")
def report():
    from scripts.replay_tactical_v2_admission import replay_fixture

    return replay_fixture(FIXTURE)


def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_replay_driver_exists_and_locks_admission_counts(report):
    assert report.raw_candidates == 22
    assert report.accepted == 5
    assert report.accepted_by_symbol == {"BICO-USDT": 3, "PUMP-USDT": 2}
    assert report.reasons == {"duplicate_episode": 17}
    assert report.accepted_reasons == {"eligible": 3, "new_confirmed_structure": 2}
    assert report.rejected == 0
    assert report.unknown == 0


def test_report_separates_normalized_opportunities_from_controller_intents(report):
    assert report.accepted_metric == "normalized_admission_opportunities"
    assert report.controller_replay_intents == 2
    assert report.controller_replay_receipts == 22
    assert report.controller_replay_results == {
        "accepted": 2,
        "duplicate_episode": 17,
        "same_symbol_exposure": 3,
    }
    assert report.controller_replay_event_seq_contiguous is True
    assert report.controller_replay_integrity_failure is None
    assert report.controller_replay_lifecycle_evidence == "absent_from_fixture"
    assert report.controller_five_intent_parity_proven is False
    assert report.controller_replay_expected_values_passed is True
    assert report.controller_replay_stability_requirement_passed is True


def test_controller_replay_runs_and_compares_every_independent_iteration(report):
    assert report.controller_stable_iterations == 100
    assert report.controller_stability_compared_fields == (
        "controller_replay_intent_ids",
        "controller_replay_receipts",
        "controller_replay_results",
        "controller_replay_event_seq_contiguous",
        "controller_replay_integrity_failure",
    )
    assert len(report.controller_stability_fingerprint) == 64


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
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    journal_times = {
        row["candidate"]["candidate_id"]: row["journal_timestamp"]
        for row in fixture["candidates"]
        if (row["candidate"]["candidate_id"], row["candidate"]["source_shadow_id"])
        in EXPECTED_ACCEPTED
    }
    assert all(
        row["observed_at"] == row["evaluated_at"] == journal_times[row["candidate_id"]]
        for row in report.entry_decision_checks
    )


def test_journal_time_beyond_candidate_ttl_fails_admission_expectation():
    from scripts.replay_tactical_v2_admission import replay_fixture

    fixture = _fixture()
    first = fixture["candidates"][0]
    first["journal_timestamp"] = first["candidate"]["created_at"] + 901.0
    for offset, row in enumerate(fixture["candidates"][1:4], start=1):
        row["journal_timestamp"] = first["journal_timestamp"] + offset

    tampered = replay_fixture(fixture)
    failed_check = next(
        row for row in tampered.entry_decision_checks
        if row["candidate_id"] == first["candidate"]["candidate_id"]
    )

    assert failed_check["evaluated_at"] == first["journal_timestamp"]
    assert failed_check["observed_at"] == first["journal_timestamp"]
    assert failed_check["ttl_fresh"] is False
    assert failed_check["action"] == "terminal"
    assert failed_check["reason"] == "expired"
    assert tampered.parity_expected_values_passed is False
    assert tampered.pinned_fixture_fingerprint_match is False
    assert tampered.replay_integrity_passed is False
    assert tampered.stability_requirement_passed is False
    assert tampered.admission_replay_passed is False


@pytest.mark.parametrize(
    "case,match",
    [
        ("root_extra", "fixture root fields"),
        ("schema_version", "schema_version"),
        ("source_extra", "source_evidence fields"),
        ("topic", "source evidence topic"),
        ("window_start", "source evidence window"),
        ("row_extra", "candidate evidence row fields"),
        ("paper_namespace", "candidate namespace"),
        ("journal_before_created", "journal_timestamp must not precede created_at"),
        ("created_outside_window", "created_at must be inside"),
        ("journal_outside_window", "journal_timestamp must be inside"),
        ("nonmonotonic_journal", "journal timestamps must be strictly increasing"),
        (
            "replay_order_clock_regression",
            "replay-order journal timestamps must be strictly increasing",
        ),
        ("duplicate_msg", "duplicate msg_id"),
        ("duplicate_hash", "duplicate source evidence payload hash"),
    ],
)
def test_pinned_source_sequence_tampering_fails_before_store(
    case,
    match,
    monkeypatch,
):
    import scripts.replay_tactical_v2_admission as replay

    fixture = _fixture()
    if case == "root_extra":
        fixture["unexpected"] = None
    elif case == "schema_version":
        fixture["schema_version"] = 2
    elif case == "source_extra":
        fixture["source_evidence"]["unexpected"] = None
    elif case == "topic":
        fixture["source_evidence"]["topic"] = "other"
    elif case == "window_start":
        fixture["source_evidence"]["window_start_epoch"] += 1
    elif case == "row_extra":
        fixture["candidates"][0]["unexpected"] = None
    elif case == "paper_namespace":
        fixture["candidates"][0]["candidate"]["namespace"] = "paper"
    elif case == "journal_before_created":
        row = fixture["candidates"][0]
        row["journal_timestamp"] = row["candidate"]["created_at"] - 1.0
    elif case == "created_outside_window":
        fixture["candidates"][0]["candidate"]["created_at"] = (
            fixture["source_evidence"]["window_start_epoch"] - 1.0
        )
    elif case == "journal_outside_window":
        fixture["candidates"][-1]["journal_timestamp"] = (
            fixture["source_evidence"]["window_end_epoch"] + 1.0
        )
    elif case == "nonmonotonic_journal":
        fixture["candidates"][0], fixture["candidates"][1] = (
            fixture["candidates"][1], fixture["candidates"][0]
        )
    elif case == "replay_order_clock_regression":
        first = fixture["candidates"][0]["candidate"]
        fixture["candidates"][1]["candidate"]["created_at"] = (
            first["created_at"] - 1.0
        )
        assert all(
            left["journal_timestamp"] < right["journal_timestamp"]
            for left, right in zip(fixture["candidates"], fixture["candidates"][1:])
        )
        ordered = sorted(
            fixture["candidates"][:2],
            key=lambda row: row["candidate"]["created_at"],
        )
        assert ordered[0]["journal_timestamp"] > ordered[1]["journal_timestamp"]
    elif case == "duplicate_msg":
        fixture["candidates"][1]["msg_id"] = fixture["candidates"][0]["msg_id"]
    elif case == "duplicate_hash":
        fixture["candidates"][1]["source_evidence_payload_hash"] = (
            fixture["candidates"][0]["source_evidence_payload_hash"]
        )

    def forbid_store_construction(*args, **kwargs):
        raise AssertionError("store constructed before source validation")

    monkeypatch.setattr(replay.TacticalStore, "__init__", forbid_store_construction)
    with pytest.raises(ValueError, match=match):
        replay.replay_fixture(fixture, iterations=1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("namespace", "paper"),
        ("symbol", "BICO-USDT"),
        ("side", "short"),
        ("epoch_seq", 13),
        ("attempted", False),
        ("terminal", False),
        ("terminal_reason", "other"),
        ("episode_id", "0" * 64),
        ("last_closed_bar_ts", float("nan")),
        ("max_observed_closed_bar_ts", 1786072500001.0),
        ("last_structure_token", ""),
    ],
)
def test_initial_episode_tampering_fails_before_store(field, value, monkeypatch):
    import scripts.replay_tactical_v2_admission as replay

    fixture = _fixture()
    fixture["initial_episode_state"][field] = value

    def forbid_store_construction(*args, **kwargs):
        raise AssertionError("store constructed before initial-state validation")

    monkeypatch.setattr(replay.TacticalStore, "__init__", forbid_store_construction)
    with pytest.raises(ValueError, match="initial episode"):
        replay.replay_fixture(fixture, iterations=1)


def test_full_fixture_fingerprint_pins_all_source_evidence(report):
    from scripts.replay_tactical_v2_admission import PINNED_FIXTURE_SHA256

    assert PINNED_FIXTURE_SHA256 == globals()["PINNED_FIXTURE_SHA256"]
    assert report.fixture_fingerprint == PINNED_FIXTURE_SHA256
    assert report.pinned_fixture_fingerprint_match is True
    assert report.replay_integrity_passed is True


def test_semantically_valid_fixture_change_cannot_claim_integrity():
    from scripts.replay_tactical_v2_admission import replay_fixture

    fixture = _fixture()
    fixture["source_evidence"]["description"] += " Sanitized copy."

    modified = replay_fixture(fixture, iterations=1)

    assert modified.parity_expected_values_passed is True
    assert modified.pinned_fixture_fingerprint_match is False
    assert modified.replay_integrity_passed is False
    assert modified.admission_replay_passed is False


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
    assert report.stability_requirement_passed is True


def test_report_separates_admission_integrity_from_live_rollout_safety(report):
    payload = report.to_dict()

    assert "safety_checks_passed" not in payload
    assert "safety_parity_passed" not in payload
    assert report.parity_expected_values_passed is True
    assert report.replay_integrity_passed is True
    assert report.stability_requirement_passed is True
    assert report.admission_replay_passed is True
    assert report.exchange_fill is False
    assert report.historical_executable_quote_available is False
    assert report.protection_evidence_proven is False
    assert report.protection_check_status == "not_run_no_fill"
    assert report.protection_live_rollout_gate_passed is False
    assert report.live_rollout_ready is False
    assert report.synthetic_boundary_role == "admission_normalization_only"
    assert report.synthetic_boundary_market_settlement is False
    assert "entry-decision check" not in payload


def test_summary_results_are_computed_and_serialized_from_report_content(report):
    from scripts.replay_tactical_v2_admission import (
        PINNED_PARITY_SHA256,
        PINNED_REPLAY_EVIDENCE_SHA256,
        PINNED_STABILITY_SHA256,
    )

    stored_fields = {field.name for field in dataclass_fields(report)}
    computed_fields = {
        "parity_expected_values_passed",
        "replay_integrity_passed",
        "stability_requirement_passed",
        "admission_replay_passed",
    }

    assert computed_fields.isdisjoint(stored_fields)
    assert PINNED_PARITY_SHA256 == globals()["PINNED_PARITY_SHA256"]
    assert PINNED_REPLAY_EVIDENCE_SHA256 == globals()[
        "PINNED_REPLAY_EVIDENCE_SHA256"
    ]
    assert PINNED_STABILITY_SHA256 == globals()["PINNED_STABILITY_SHA256"]
    payload = report.to_dict()
    assert all(payload[field] is True for field in computed_fields)


@pytest.mark.parametrize(
    "field,mutate,expected",
    [
        ("raw_candidates", lambda value: value - 1, (False, False, True)),
        ("accepted", lambda value: value - 1, (False, False, True)),
        (
            "accepted_by_symbol",
            lambda value: {**value, "BICO-USDT": 2},
            (False, False, True),
        ),
        ("reasons", lambda value: {}, (False, False, True)),
        ("accepted_reasons", lambda value: {}, (False, False, True)),
        (
            "episode_ids",
            lambda value: (*value[:-1], "0" * 64),
            (False, False, False),
        ),
        (
            "source_shadow_ids",
            lambda value: ("other", *value[1:]),
            (True, False, True),
        ),
        (
            "source_evidence_payload_hashes",
            lambda value: ("0" * 12, *value[1:]),
            (True, False, True),
        ),
        (
            "stability_compared_fields",
            lambda value: ("accepted_identities",),
            (True, False, False),
        ),
        (
            "stability_fingerprint",
            lambda value: "0" * 64,
            (True, False, False),
        ),
        ("stable_iterations", lambda value: 99, (True, True, False)),
        (
            "fixture_fingerprint",
            lambda value: "0" * 64,
            (True, False, True),
        ),
        (
            "pinned_fixture_fingerprint_match",
            lambda value: False,
            (True, False, True),
        ),
    ],
)
def test_computed_results_fail_closed_for_top_level_evidence_tampering(
    report,
    field,
    mutate,
    expected,
):
    contradictory = replace(report, **{field: mutate(getattr(report, field))})

    assert (
        contradictory.parity_expected_values_passed,
        contradictory.replay_integrity_passed,
        contradictory.stability_requirement_passed,
    ) == expected
    assert contradictory.admission_replay_passed is False
    assert contradictory.to_dict()["admission_replay_passed"] is False


def _replace_nested_record(report, report_field, index, record_field, value):
    records = list(deepcopy(getattr(report, report_field)))
    records[index][record_field] = value
    return replace(report, **{report_field: tuple(records)})


def test_accepted_identity_nested_tampering_is_recomputed(report):
    contradictory = _replace_nested_record(
        report,
        "accepted_identities",
        0,
        "msg_id",
        "different-message",
    )

    assert contradictory.parity_expected_values_passed is False
    assert contradictory.replay_integrity_passed is False
    assert contradictory.stability_requirement_passed is False
    assert contradictory.admission_replay_passed is False


def test_row_reason_nested_tampering_is_recomputed(report):
    contradictory = _replace_nested_record(
        report,
        "row_reasons",
        1,
        "reason",
        "eligible",
    )

    assert contradictory.parity_expected_values_passed is True
    assert contradictory.replay_integrity_passed is False
    assert contradictory.stability_requirement_passed is False
    assert contradictory.admission_replay_passed is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("label", "other"),
        ("quote_evidence", "historical"),
        ("synthetic_quote_role", "other"),
        ("observed_at", 0.0),
        ("action", "terminal"),
        ("reason", "expired"),
        ("ttl_fresh", False),
        ("governor_allowed", False),
        ("governor_reason", "capacity"),
        ("candidate_id", "other"),
        ("episode_id", "0" * 64),
        ("ask", 0.0),
    ],
)
def test_entry_decision_nested_tampering_is_recomputed(report, field, value):
    contradictory = _replace_nested_record(
        report,
        "entry_decision_checks",
        0,
        field,
        value,
    )

    assert contradictory.parity_expected_values_passed is False
    assert contradictory.replay_integrity_passed is False
    assert contradictory.stability_requirement_passed is True
    assert contradictory.admission_replay_passed is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("historical_receipt_context", "other"),
        ("historical_receipt_evidence", "known"),
        ("historical_receipt_unknown", 21),
        ("synthetic_boundary_role", "market_settlement"),
        ("synthetic_boundary_market_settlement", True),
        ("exchange_fill", True),
        ("historical_executable_quote_available", True),
        ("protection_evidence_proven", True),
        ("protection_check_status", "passed"),
        ("protection_live_rollout_gate_passed", True),
        ("live_rollout_ready", True),
    ],
)
def test_computed_admission_replay_fails_for_every_invariant(report, field, value):
    contradictory = replace(report, **{field: value})

    assert contradictory.admission_replay_passed is False
    assert contradictory.to_dict()["admission_replay_passed"] is False


def test_historical_receipt_unknown_is_distinct_from_normalized_replay_unknown(report):
    assert report.unknown == 0
    assert report.historical_receipt_context == "predates_durable_receipts"
    assert report.historical_receipt_evidence == "unknown"
    assert report.historical_receipt_unknown == 22


def test_all_source_evidence_is_preserved_and_artifacts_are_sanitized(report):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert frozenset(fixture) == EXPECTED_ROOT_FIELDS
    assert frozenset(fixture["source_evidence"]) == EXPECTED_SOURCE_FIELDS
    assert all(
        frozenset(row) == EXPECTED_ROW_FIELDS
        for row in fixture["candidates"]
    )
    assert report.source_shadow_ids == EXPECTED_SOURCE_SHADOW_IDS
    assert tuple(
        row["candidate"]["source_shadow_id"] for row in fixture["candidates"]
    ) == EXPECTED_SOURCE_SHADOW_IDS
    assert len({row["msg_id"] for row in fixture["candidates"]}) == 22
    assert all(len(row["source_evidence_payload_hash"]) == 12 for row in fixture["candidates"])
    assert "source evidence, not a canonical local receipt hash" in (
        fixture["source_evidence"]["description"]
    )
    assert all(
        frozenset(row["candidate"]) == EXPECTED_CANDIDATE_FIELDS
        for row in fixture["candidates"]
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


def test_replay_rejects_candidate_fields_outside_exact_schema():
    from scripts.replay_tactical_v2_admission import replay_fixture

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture["candidates"][0]["candidate"]["unexpected_field"] = "not allowed"

    with pytest.raises(ValueError, match="candidate fields do not match"):
        replay_fixture(fixture, iterations=1)


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
    assert isolated_report.parity_expected_values_passed is True
    assert isolated_report.replay_integrity_passed is True
    assert isolated_report.stability_requirement_passed is False
    assert isolated_report.admission_replay_passed is False
    assert destinations
    assert all(path.is_relative_to(tmp_path.resolve()) for path in destinations)
    assert set(tmp_path.iterdir()) == initial_entries


def test_default_temporary_directory_is_network_free_and_removed(monkeypatch):
    import scripts.replay_tactical_v2_admission as replay

    roots = []
    destinations = []
    original_temporary_directory = replay.TemporaryDirectory
    original_store_init = replay.TacticalStore.__init__

    def record_temporary_directory(*args, **kwargs):
        temporary = original_temporary_directory(*args, **kwargs)
        roots.append(Path(temporary.name).resolve())
        return temporary

    def record_store_paths(store, paths):
        destinations.extend(
            [Path(paths.tactical_v2_events).resolve(), Path(paths.tactical_v2_state).resolve()]
        )
        original_store_init(store, paths)

    def deny_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(replay, "TemporaryDirectory", record_temporary_directory)
    monkeypatch.setattr(replay.TacticalStore, "__init__", record_store_paths)
    monkeypatch.setattr(socket, "socket", deny_network)

    isolated_report = replay.replay_fixture(FIXTURE, iterations=2)

    assert isolated_report.raw_candidates == 22
    assert roots
    assert all(
        any(destination.is_relative_to(root) for root in roots)
        for destination in destinations
    )
    assert all(not root.exists() for root in roots)


def test_cli_prints_deterministic_json_and_evidence_limitations(report):
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
    assert "entry-decision check" not in payload
    assert payload["exchange_fill"] is False
    assert payload["historical_executable_quote_available"] is False
    assert payload["protection_evidence_proven"] is False
    assert payload["live_rollout_ready"] is False
    assert payload["parity_expected_values_passed"] is True
    assert payload["replay_integrity_passed"] is True
    assert payload["stability_requirement_passed"] is True
    assert payload["admission_replay_passed"] is True


def test_cli_rejects_less_than_100_stability_iterations():
    result = subprocess.run(
        [
            sys.executable,
            str(DRIVER),
            "--fixture",
            str(FIXTURE),
            "--iterations",
            "1",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["parity_expected_values_passed"] is True
    assert payload["replay_integrity_passed"] is True
    assert payload["stability_requirement_passed"] is False
    assert payload["admission_replay_passed"] is False
