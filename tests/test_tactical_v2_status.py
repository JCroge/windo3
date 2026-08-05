import json
from types import SimpleNamespace


def _paths(tmp_path):
    return SimpleNamespace(tactical_v2_status=str(tmp_path / "status.json"))


def _healthy_snapshot(**overrides):
    snapshot = {
        "schema_version": 2,
        "engine_version": "tactical_v2",
        "updated_at": 1000.0,
        "namespace": "live",
        "mode": "live",
        "requested_mode": "live",
        "cutover": {"allowed": True, "reason": "sidecar_retirement_verified"},
        "margin_usdt": 100.0,
        "max_concurrent": 3,
        "slots": {"active": 1, "pending": 1, "free": 1},
        "symbols": {"active": ["WLD-USDT"], "pending": ["SOL-USDT"]},
        "rolling_pnl_24h_usdt": -2.5,
        "rolling_loss_limit_usdt": -15.0,
        "loss_streak": 1,
        "loss_streak_limit": 3,
        "timed_pause_until": 0.0,
        "integrity_halt": None,
        "episode_outcomes": {"expired": 2},
        "protection": {"state": "verified", "unverified_count": 0},
        "reconciliation": {"state": "verified", "unknown_count": 0},
        "parity": {"mismatch_count": 0},
    }
    snapshot.update(overrides)
    return snapshot


def test_status_snapshot_write_and_read_are_atomic(tmp_path):
    from utils.tactical_v2.status import read_status, write_status

    paths = _paths(tmp_path)
    expected = _healthy_snapshot()
    write_status(paths, expected)

    assert read_status(paths) == expected
    assert not (tmp_path / "status.json.tmp").exists()


def test_status_marks_old_snapshot_stale():
    from utils.tactical_v2.status import format_tactical_v2_status

    text = format_tactical_v2_status(
        _healthy_snapshot(updated_at=1000.0), stale_seconds=90, now=1091.0
    )

    assert "STALE" in text
    assert "circuit clear" not in text.lower()
    assert "protection verified" not in text.lower()


def test_status_rejects_nan_pnl_without_hiding_other_identity():
    from utils.tactical_v2.status import format_tactical_v2_status

    text = format_tactical_v2_status(
        _healthy_snapshot(rolling_pnl_24h_usdt=float("nan")), now=1001.0
    )

    assert "Tactical V2 LIVE" in text
    assert "PnL: ?" in text


def test_status_formats_healthy_slots_symbols_and_independent_circuit():
    from utils.tactical_v2.status import format_tactical_v2_status

    text = format_tactical_v2_status(_healthy_snapshot(), now=1001.0)

    assert "Tactical V2 LIVE" in text
    assert "100U x 3" in text
    assert "1 active / 1 pending / 1 free" in text
    assert "WLD" in text and "SOL" in text
    assert "circuit clear" in text.lower()
    assert "protection verified" in text.lower()
    assert "parity 0 mismatch" in text.lower()


def test_status_shows_parity_category_and_shadow_denominators():
    from utils.tactical_v2.status import format_tactical_v2_status

    text = format_tactical_v2_status(
        _healthy_snapshot(parity={
            "mismatch_count": 2,
            "compared_intents": 4,
            "categories": {"exchange_fill": 1, "order_rejection": 1},
            "shadow_filled": 3,
            "shadow_nonfilled": 1,
        }),
        now=1001.0,
    )

    assert "exchange_fill:1" in text
    assert "order_rejection:1" in text
    assert "shadow 3 filled / 1 nonfilled" in text


def test_status_distinguishes_requested_live_from_cutover_blocked_shadow():
    from utils.tactical_v2.status import format_tactical_v2_status

    text = format_tactical_v2_status(
        _healthy_snapshot(
            mode="shadow",
            requested_mode="live",
            cutover={
                "allowed": False,
                "reason": "sidecar_retirement_missing",
            },
        ),
        now=1001.0,
    )

    assert "Tactical V2 SHADOW | requested LIVE" in text
    assert "Cutover: BLOCKED (sidecar_retirement_missing)" in text
    assert "integrity HALT" not in text


def test_status_labels_rolling_pause_as_new_admission_only():
    from utils.tactical_v2.status import format_tactical_v2_status

    text = format_tactical_v2_status(
        _healthy_snapshot(rolling_pnl_24h_usdt=-15.0), now=1001.0
    )

    assert "new admission PAUSED (rolling loss)" in text
    assert "existing positions managed" in text


def test_status_integrity_halt_has_no_expiry_claim():
    from utils.tactical_v2.status import format_tactical_v2_status

    text = format_tactical_v2_status(
        _healthy_snapshot(
            integrity_halt={
                "reason": "protection_unknown",
                "evidence": {"symbol": "WLD-USDT"},
            }
        ),
        now=1001.0,
    )

    assert "integrity HALT" in text
    assert "protection_unknown" in text
    assert "until" not in text


def test_read_status_missing_or_malformed_degrades_to_none(tmp_path):
    from utils.tactical_v2.status import read_status

    paths = _paths(tmp_path)
    assert read_status(paths) is None

    (tmp_path / "status.json").write_text("{broken", encoding="utf-8")
    assert read_status(paths) is None

    (tmp_path / "status.json").write_text(json.dumps([]), encoding="utf-8")
    assert read_status(paths) is None


def test_malformed_nested_snapshot_never_formats_as_healthy():
    from utils.tactical_v2.status import format_tactical_v2_status

    malformed = _healthy_snapshot(episode_outcomes={"expired": "not-a-count"})

    text = format_tactical_v2_status(malformed, now=1001.0)

    assert "STALE" in text
    assert "circuit clear" not in text.lower()


def test_wrong_status_schema_or_engine_never_formats_as_healthy():
    from utils.tactical_v2.status import format_tactical_v2_status

    for snapshot in (
        _healthy_snapshot(schema_version=1),
        _healthy_snapshot(engine_version="legacy_tactical"),
    ):
        text = format_tactical_v2_status(snapshot, now=1001.0)
        assert "STALE" in text
        assert "circuit clear" not in text.lower()
