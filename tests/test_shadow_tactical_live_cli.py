import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock

import pytest

from utils.shadow_sidecar_policy import (
    SIDECAR_POLICY_MAX_AGE_SECONDS,
    SIDECAR_POLICY_VERSION,
    stamp_sidecar_policy,
)
from utils.shadow_tactical_live import ShadowEventRow


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = str(ROOT / "scripts" / "shadow_tactical_live_sidecar.py")


def _load_sidecar_module():
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tactical_record(*, decided_at=None, stamp=True, **overrides):
    rec = {
        "id": "shadow-1",
        "symbol": "WLD-USDT-SWAP",
        "side": "long",
        "entry_price": 1.25,
        "stop_loss": 1.20,
        "take_profit": [1.32],
        "leverage": 20,
        "track": "tactical",
        "exit_profile": "tactical_v1",
        "tactical_track_gate": "pass",
        "tactical_trend_exhaustion_warning": False,
        "tactical_weak_volume_oi": False,
        "tactical_weak_provenance": False,
    }
    rec.update(overrides)
    if not stamp:
        return rec
    return stamp_sidecar_policy(
        rec,
        decided_at=time.time() + 0.5 if decided_at is None else decided_at,
    )


def _event(record):
    return {"event_type": "rejected_plan_created", "record": record}


def _policy_evidence(record):
    return {
        "tactical_track_gate": record["tactical_track_gate"],
        "tactical_trend_exhaustion_warning": record[
            "tactical_trend_exhaustion_warning"
        ],
        "tactical_weak_volume_oi": record["tactical_weak_volume_oi"],
        "tactical_weak_provenance": record["tactical_weak_provenance"],
    }


def _process_event_fixture(tmp_path, mod):
    paths = mod.SidecarPaths(
        owners=str(tmp_path / "owners.json"),
        audit=str(tmp_path / "audit.jsonl"),
    )
    state = {"seen_shadow_ids": {}}
    registry = mod.ShadowTacticalOwnerRegistry(paths.owners)
    executor = MagicMock()
    executor.open_sidecar_plan.return_value = {
        "symbol": "WLD-USDT-SWAP",
        "side": "long",
        "amount_usdt": 100.0,
        "entry_order_id": "ord-1",
        "entry_clord_id": "stl-1",
        "sl_algo_id": "algo-1",
        "sl_algo_clord_id": "sl-1",
    }
    return paths, state, registry, executor


def _audit_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def _assert_policy_rejected(row, *, shadow_id, reason, version, tier, evidence):
    assert row["event_type"] == "rejected"
    assert row["shadow_id"] == shadow_id
    assert row["reason"] == reason
    assert row["sidecar_policy_version"] == version
    assert row["sidecar_risk_tier"] == tier
    assert row["sidecar_policy_evidence"] == evidence


def test_status_prints_state_counts(tmp_path):
    state = tmp_path / "state.json"
    owners = tmp_path / "owners.json"
    state.write_text(
        json.dumps({"last_offset": 10, "seen_shadow_ids": {"s1": "opened", "s2": "rejected"}})
    )
    owners.write_text(
        json.dumps(
            {
                "owners": {
                    "s1": {
                        "status": "open",
                        "symbol": "WLD-USDT-SWAP",
                        "side": "long",
                    }
                }
            }
        )
    )

    out = subprocess.check_output(
        [
            sys.executable,
            SCRIPT,
            "status",
            "--state",
            str(state),
            "--owners",
            str(owners),
        ],
        text=True,
        cwd=str(ROOT),
    )

    assert "opened=1" in out
    assert "rejected=1" in out
    assert "active=1" in out


def test_sidecar_executor_forces_dedicated_bot_owner(monkeypatch):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import utils.halt_state as halt_state_mod

    monkeypatch.setenv("BOT_INSTANCE_ID", "main01")
    monkeypatch.setenv("SIDECAR_BOT_INSTANCE_ID", "stlive")
    monkeypatch.setattr(
        halt_state_mod,
        "HALT_STATE_FILE",
        halt_state_mod.HALT_STATE_FILE,
    )
    monkeypatch.setattr(mod, "ContractExecutor", MagicMock(return_value=object()))

    mod._build_executor(mod.SidecarPaths(), max_trade_amount=100.0)

    assert mod.os.environ["BOT_INSTANCE_ID"] == "stlive"
    mod.ContractExecutor.assert_called_once()
    assert mod.ContractExecutor.call_args.kwargs["max_trade_amount_override"] == 100.0


def test_resolve_sidecar_base_size_accepts_valid_hard_limit_value():
    mod = _load_sidecar_module()

    assert mod.resolve_sidecar_base_size("100") == 100.0


@pytest.mark.parametrize("invalid", ["bad", "nan", "inf", "0", "-1", "10001"])
def test_resolve_sidecar_base_size_rejects_invalid_values(invalid):
    mod = _load_sidecar_module()

    with pytest.raises(ValueError):
        mod.resolve_sidecar_base_size(invalid)


@pytest.mark.parametrize("value, expected", [("1", 1), (2, 2), ("3", 3)])
def test_resolve_sidecar_max_active_accepts_one_through_three(value, expected):
    mod = _load_sidecar_module()

    assert mod.resolve_sidecar_max_active(value) == expected


@pytest.mark.parametrize("invalid", [True, False, "bad", "2.0", 2.5, 0, "0", 4, "4"])
def test_resolve_sidecar_max_active_rejects_invalid_values(invalid):
    mod = _load_sidecar_module()

    with pytest.raises(ValueError):
        mod.resolve_sidecar_max_active(invalid)


def test_run_rejects_oversized_max_active_before_state_or_executor(
    tmp_path, monkeypatch
):
    mod = _load_sidecar_module()
    build_executor = MagicMock()
    monkeypatch.setattr(mod, "_build_executor", build_executor)
    state = tmp_path / "state.json"

    code = mod.cmd_run(
        SimpleNamespace(
            events=str(tmp_path / "events.jsonl"),
            state=str(state),
            audit=str(tmp_path / "audit.jsonl"),
            owners=str(tmp_path / "owners.json"),
            dry_run=False,
            once=True,
            backfill_from_start=False,
            poll_seconds="2",
            size_usdt="100",
            max_active="4",
        )
    )

    assert code == 2
    assert not state.exists()
    build_executor.assert_not_called()


def test_run_dry_run_processes_new_tactical_event(tmp_path):
    events = tmp_path / "events.jsonl"
    state = tmp_path / "state.json"
    audit = tmp_path / "audit.jsonl"
    rec = _tactical_record(id="s1")
    events.write_text(json.dumps(_event(rec)) + "\n")

    subprocess.check_call(
        [
            sys.executable,
            SCRIPT,
            "run",
            "--dry-run",
            "--once",
            "--backfill-from-start",
            "--events",
            str(events),
            "--state",
            str(state),
            "--audit",
            str(audit),
            "--duration-hours",
            "24",
        ],
        cwd=str(ROOT),
    )

    row = json.loads(audit.read_text().splitlines()[0])
    assert row["event_type"] == "dry_run_plan"
    assert row["shadow_id"] == "s1"
    assert row["requested_size_usdt"] == 30.0
    assert row["sidecar_policy_version"] == SIDECAR_POLICY_VERSION
    assert row["sidecar_risk_tier"] == "full"


def test_run_dry_run_rejects_shadow_only_tactical_gate_fail_event(tmp_path):
    events = tmp_path / "events.jsonl"
    state = tmp_path / "state.json"
    audit = tmp_path / "audit.jsonl"
    rec = _tactical_record(
        id="shadow-gate-fail",
        symbol="DOGE-USDT-SWAP",
        side="short",
        entry_price=0.072,
        stop_loss=0.073,
        take_profit=[0.071],
        track="shadow_only",
        tactical_track_gate="fail",
        reject_reason="main_quality_failed:tactical_shadow_only",
    )
    events.write_text(json.dumps(_event(rec)) + "\n")

    subprocess.check_call(
        [
            sys.executable,
            SCRIPT,
            "run",
            "--dry-run",
            "--once",
            "--backfill-from-start",
            "--events",
            str(events),
            "--state",
            str(state),
            "--audit",
            str(audit),
            "--duration-hours",
            "24",
        ],
        cwd=str(ROOT),
    )

    row = json.loads(audit.read_text().splitlines()[0])
    _assert_policy_rejected(
        row,
        shadow_id="shadow-gate-fail",
        reason="tactical_track_gate_failed",
        version=SIDECAR_POLICY_VERSION,
        tier="none",
        evidence=_policy_evidence(rec),
    )


def test_run_defaults_to_no_backfill_on_first_start(tmp_path):
    events = tmp_path / "events.jsonl"
    state = tmp_path / "state.json"
    audit = tmp_path / "audit.jsonl"
    rec = _tactical_record(id="old")
    events.write_text(json.dumps(_event(rec)) + "\n")

    subprocess.check_call(
        [
            sys.executable,
            SCRIPT,
            "run",
            "--dry-run",
            "--once",
            "--events",
            str(events),
            "--state",
            str(state),
            "--audit",
            str(audit),
        ],
        cwd=str(ROOT),
    )

    loaded = json.loads(state.read_text())
    assert loaded["last_offset"] == events.stat().st_size
    assert loaded["seen_shadow_ids"] == {}
    assert not audit.exists()


def test_run_preserves_existing_watermark_when_no_backfill_default(tmp_path):
    events = tmp_path / "events.jsonl"
    state = tmp_path / "state.json"
    audit = tmp_path / "audit.jsonl"
    old_line = json.dumps(_event(_tactical_record(id="old"))) + "\n"
    events.write_text(old_line)
    old_offset = events.stat().st_size
    state.write_text(
        json.dumps(
            {
                "started_at": 1,
                "stop_at": None,
                "last_offset": old_offset,
                "seen_shadow_ids": {},
            }
        )
    )
    with events.open("a") as fh:
        fh.write(json.dumps(_event(_tactical_record(id="new"))) + "\n")

    subprocess.check_call(
        [
            sys.executable,
            SCRIPT,
            "run",
            "--dry-run",
            "--once",
            "--events",
            str(events),
            "--state",
            str(state),
            "--audit",
            str(audit),
        ],
        cwd=str(ROOT),
    )

    row = json.loads(audit.read_text().splitlines()[0])
    assert row["shadow_id"] == "new"
    loaded = json.loads(state.read_text())
    assert loaded["last_offset"] == events.stat().st_size


def test_run_ignores_legacy_stop_at_and_stays_resident(tmp_path):
    events = tmp_path / "events.jsonl"
    state = tmp_path / "state.json"
    audit = tmp_path / "audit.jsonl"
    rec = _tactical_record(id="legacy-stop")
    events.write_text(json.dumps(_event(rec)) + "\n")
    state.write_text(
        json.dumps(
            {
                "started_at": 1,
                "stop_at": 1,
                "last_offset": 0,
                "seen_shadow_ids": {},
            }
        )
    )

    subprocess.check_call(
        [
            sys.executable,
            SCRIPT,
            "run",
            "--dry-run",
            "--once",
            "--events",
            str(events),
            "--state",
            str(state),
            "--audit",
            str(audit),
            "--duration-hours",
            "24",
        ],
        cwd=str(ROOT),
    )

    row = json.loads(audit.read_text().splitlines()[0])
    assert row["event_type"] == "dry_run_plan"
    assert row["shadow_id"] == "legacy-stop"
    loaded = json.loads(state.read_text())
    assert loaded["stop_at"] is None


def test_stop_admission_command_persists_before_success(tmp_path):
    state = tmp_path / "state.json"
    audit = tmp_path / "audit.jsonl"

    out = subprocess.check_output(
        [
            sys.executable,
            SCRIPT,
            "stop-admission",
            "--state",
            str(state),
            "--audit",
            str(audit),
        ],
        text=True,
        cwd=str(ROOT),
    )

    saved = json.loads(state.read_text())
    assert saved["admission_enabled"] is False
    assert saved["admission_disabled_at"] > 0
    assert "admission_enabled=false" in out
    assert json.loads(audit.read_text())["event_type"] == "admission_stopped"


def test_resident_runner_refreshes_external_admission_stop_before_event(
    tmp_path, monkeypatch
):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    events = tmp_path / "events.jsonl"
    state_path = tmp_path / "state.json"
    audit = tmp_path / "audit.jsonl"
    events.write_text("", encoding="utf-8")
    store = mod.SidecarStateStore(str(state_path))
    store.save({
        "last_offset": 0,
        "seen_shadow_ids": {},
        "admission_enabled": True,
    })
    event = {
        "event_type": "rejected_plan_created",
        "record": {
            "id": "arrived-after-stop",
            "symbol": "WLD-USDT-SWAP",
            "side": "long",
            "entry_price": 1.25,
            "stop_loss": 1.20,
            "take_profit": [1.32],
            "leverage": 5,
            "track": "tactical",
            "exit_profile": "tactical_v1",
        },
    }

    def rows_after_external_stop(path, offset):
        store.disable_admission(source="cutover", now=1001.0)
        yield ShadowEventRow(event=event, start_offset=0, next_offset=1)

    monkeypatch.setattr(mod, "iter_new_shadow_events", rows_after_external_stop)

    code = mod.main([
        "run",
        "--dry-run",
        "--once",
        "--events",
        str(events),
        "--state",
        str(state_path),
        "--audit",
        str(audit),
    ])

    saved = store.load()
    assert code == 0
    assert saved["admission_enabled"] is False
    assert saved["seen_shadow_ids"]["arrived-after-stop"] == "admission_disabled"
    assert json.loads(audit.read_text())["event_type"] == "admission_disabled_skipped"


def test_disabled_admission_consumes_candidate_without_open_or_backfill(tmp_path):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    paths = mod.SidecarPaths(
        owners=str(tmp_path / "owners.json"),
        audit=str(tmp_path / "audit.jsonl"),
    )
    state = {"admission_enabled": False, "seen_shadow_ids": {}}
    registry = mod.ShadowTacticalOwnerRegistry(paths.owners)
    executor = MagicMock()
    event = {
        "event_type": "rejected_plan_created",
        "record": {
            "id": "post-stop-1",
            "symbol": "WLD-USDT-SWAP",
            "side": "long",
            "entry_price": 1.25,
            "stop_loss": 1.20,
            "take_profit": [1.32],
            "leverage": 20,
            "track": "tactical",
            "exit_profile": "tactical_v1",
        },
    }
    args = SimpleNamespace(dry_run=False, max_active="3", size_usdt="30")

    mod._process_event(args, paths, state, registry, executor, event)
    mod._process_event(args, paths, state, registry, executor, event)

    executor.open_sidecar_plan.assert_not_called()
    assert state["seen_shadow_ids"]["post-stop-1"] == "admission_disabled"
    rows = [json.loads(line) for line in Path(paths.audit).read_text().splitlines()]
    assert rows[0]["event_type"] == "admission_disabled_skipped"
    assert rows[1]["event_type"] == "duplicate_skipped"


def _drain_cli_executor(*, exchange_error=None, pending_pnl=None):
    fake = MagicMock()
    fake.positions = {}
    fake.logger = MagicMock()
    if exchange_error is None:
        fake._fetch_positions_with_retry.return_value = []
    else:
        fake._fetch_positions_with_retry.side_effect = exchange_error
    fake._normalize_okx_position.side_effect = lambda row: row
    fake._list_pending_algos.return_value = []
    fake.ledger.find_pending_external_closes.return_value = list(pending_pnl or [])
    return fake


def test_drain_report_cli_writes_incomplete_evidence_without_archiving(
    tmp_path, monkeypatch
):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    state = tmp_path / "state.json"
    output = tmp_path / "retirement.json"
    mod.SidecarStateStore(str(state)).disable_admission(source="cutover", now=900.0)
    fake = _drain_cli_executor(exchange_error=RuntimeError("offline"))
    monkeypatch.setattr(mod, "_build_executor", lambda paths, **kwargs: fake)

    code = mod.main([
        "drain-report",
        "--state",
        str(state),
        "--owners",
        str(tmp_path / "owners.json"),
        "--output",
        str(output),
    ])

    report = json.loads(output.read_text())
    assert code == 1
    assert report["complete"] is False
    assert report["retired"] is False
    assert report["exchange_state"] == "unknown"


def test_drain_report_cli_requires_explicit_archive_flag(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    state = tmp_path / "state.json"
    output = tmp_path / "retirement.json"
    mod.SidecarStateStore(str(state)).disable_admission(source="cutover", now=900.0)
    monkeypatch.setattr(
        mod, "_build_executor", lambda paths, **kwargs: _drain_cli_executor()
    )

    code = mod.main([
        "drain-report",
        "--state",
        str(state),
        "--owners",
        str(tmp_path / "owners.json"),
        "--output",
        str(output),
    ])
    assert code == 0
    assert json.loads(output.read_text())["retired"] is False

    code = mod.main([
        "drain-report",
        "--state",
        str(state),
        "--owners",
        str(tmp_path / "owners.json"),
        "--output",
        str(output),
        "--archive",
    ])
    assert code == 0
    assert json.loads(output.read_text())["retired"] is True


def test_drain_report_cli_uses_namespaced_default_and_documented_exceptions(
    tmp_path, monkeypatch
):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    state = tmp_path / "state.json"
    owners = tmp_path / "owners.json"
    retirement = tmp_path / "testnet_sidecar_retirement.json"
    exceptions = tmp_path / "exceptions.json"
    mod.SidecarStateStore(str(state)).disable_admission(source="cutover", now=900.0)
    owners.write_text(json.dumps({
        "owners": {
            "s1": {
                "shadow_id": "s1",
                "status": "closed",
                "close_pnl_status": "pending",
            }
        }
    }))
    exceptions.write_text(json.dumps({
        "documented_exceptions": [{
            "type": "pending_pnl",
            "object_id": "pnl-1",
            "accepted": True,
            "reason": "manually reconciled against exchange bill history",
        }]
    }))
    pending = [{
        "resolution_id": "pnl-1",
        "entry_request_id": "s1",
        "status": "pending",
    }]
    monkeypatch.setattr(
        mod,
        "_build_executor",
        lambda paths, **kwargs: _drain_cli_executor(pending_pnl=pending),
    )
    monkeypatch.setattr(
        mod,
        "get_state_paths",
        lambda namespace=None: SimpleNamespace(
            namespace=namespace or "testnet",
            sidecar_retirement=str(retirement),
        ),
    )

    code = mod.main([
        "drain-report",
        "--namespace",
        "testnet",
        "--state",
        str(state),
        "--owners",
        str(owners),
        "--exceptions",
        str(exceptions),
        "--archive",
    ])

    report = json.loads(retirement.read_text())
    assert code == 0
    assert report["namespace"] == "testnet"
    assert report["complete"] is True
    assert report["retired"] is True
    assert report["documented_exceptions"][0]["object_id"] == "pnl-1"


def test_process_event_opens_valid_full_policy_with_full_size(tmp_path, monkeypatch):
    mod = _load_sidecar_module()
    monkeypatch.setattr(mod.time, "time", lambda: 100.0)
    fetch_positions = MagicMock(return_value=[])
    monkeypatch.setattr(mod, "_fetch_exchange_positions", fetch_positions)
    paths, state, registry, executor = _process_event_fixture(tmp_path, mod)
    record = _tactical_record(id="shadow-full", decided_at=100.0)
    args = SimpleNamespace(dry_run=False, max_active="3", size_usdt="100")

    mod._process_event(args, paths, state, registry, executor, _event(record))

    executor.open_sidecar_plan.assert_called_once()
    assert executor.open_sidecar_plan.call_args.kwargs["size_usdt"] == 100.0
    fetch_positions.assert_called_once_with(executor)
    row = _audit_rows(paths.audit)[0]
    assert row["event_type"] == "opened"
    assert row["sidecar_policy_version"] == SIDECAR_POLICY_VERSION
    assert row["sidecar_risk_tier"] == "full"
    assert row["requested_size_usdt"] == 100.0


def test_process_event_opens_valid_reduced_policy_with_half_size(
    tmp_path, monkeypatch
):
    mod = _load_sidecar_module()
    monkeypatch.setattr(mod.time, "time", lambda: 100.0)
    fetch_positions = MagicMock(return_value=[])
    monkeypatch.setattr(mod, "_fetch_exchange_positions", fetch_positions)
    paths, state, registry, executor = _process_event_fixture(tmp_path, mod)
    record = _tactical_record(
        id="shadow-reduced",
        decided_at=100.0,
        tactical_weak_volume_oi=True,
    )
    args = SimpleNamespace(dry_run=False, max_active="3", size_usdt="100")

    mod._process_event(args, paths, state, registry, executor, _event(record))

    executor.open_sidecar_plan.assert_called_once()
    assert executor.open_sidecar_plan.call_args.kwargs["size_usdt"] == 50.0
    fetch_positions.assert_called_once_with(executor)
    row = _audit_rows(paths.audit)[0]
    assert row["event_type"] == "opened"
    assert row["sidecar_policy_version"] == SIDECAR_POLICY_VERSION
    assert row["sidecar_risk_tier"] == "reduced"
    assert row["requested_size_usdt"] == 50.0


@pytest.mark.parametrize(
    ("record", "reason", "version", "tier", "expected_age"),
    [
        (
            _tactical_record(
                id="shadow-gate-failure",
                tactical_track_gate="fail",
                decided_at=100.0,
            ),
            "tactical_track_gate_failed",
            SIDECAR_POLICY_VERSION,
            "none",
            0.0,
        ),
        (
            _tactical_record(
                id="shadow-trend-exhaustion",
                tactical_trend_exhaustion_warning=True,
                decided_at=100.0,
            ),
            "trend_exhaustion_warning",
            SIDECAR_POLICY_VERSION,
            "none",
            0.0,
        ),
        (
            _tactical_record(id="shadow-missing-stamp", stamp=False),
            "sidecar_policy_version_missing",
            None,
            None,
            None,
        ),
        (
            {
                **_tactical_record(id="shadow-unsupported-version", decided_at=100.0),
                "sidecar_policy_version": "shadow-sidecar-v999",
            },
            "sidecar_policy_version_unsupported",
            "shadow-sidecar-v999",
            "full",
            None,
        ),
        (
            {
                **_tactical_record(id="shadow-stamp-mismatch", decided_at=100.0),
                "sidecar_risk_tier": "reduced",
            },
            "sidecar_policy_outcome_mismatch",
            SIDECAR_POLICY_VERSION,
            "reduced",
            None,
        ),
        (
            _tactical_record(id="shadow-stale", decided_at=100.0),
            "sidecar_policy_stale",
            SIDECAR_POLICY_VERSION,
            "full",
            SIDECAR_POLICY_MAX_AGE_SECONDS + 0.01,
        ),
    ],
)
def test_process_event_rejects_invalid_policy_before_exchange_work(
    tmp_path,
    monkeypatch,
    record,
    reason,
    version,
    tier,
    expected_age,
):
    mod = _load_sidecar_module()
    now = 105.01 if reason == "sidecar_policy_stale" else 100.0
    monkeypatch.setattr(mod.time, "time", lambda: now)
    fetch_positions = MagicMock(return_value=[])
    monkeypatch.setattr(mod, "_fetch_exchange_positions", fetch_positions)
    paths, state, registry, executor = _process_event_fixture(tmp_path, mod)
    args = SimpleNamespace(dry_run=False, max_active="3", size_usdt="100")

    mod._process_event(args, paths, state, registry, executor, _event(record))

    fetch_positions.assert_not_called()
    executor.open_sidecar_plan.assert_not_called()
    assert state["seen_shadow_ids"][record["id"]] == "rejected"
    row = _audit_rows(paths.audit)[0]
    _assert_policy_rejected(
        row,
        shadow_id=record["id"],
        reason=reason,
        version=version,
        tier=tier,
        evidence=_policy_evidence(record),
    )
    if expected_age is not None:
        assert row["sidecar_policy_age_seconds"] == pytest.approx(expected_age)


def test_process_event_rejects_unmappable_verified_policy_with_audit(
    tmp_path, monkeypatch
):
    mod = _load_sidecar_module()
    monkeypatch.setattr(mod.time, "time", lambda: 100.0)
    fetch_positions = MagicMock(return_value=[])
    monkeypatch.setattr(mod, "_fetch_exchange_positions", fetch_positions)
    paths, state, registry, executor = _process_event_fixture(tmp_path, mod)
    record = _tactical_record(id="shadow-bad-plan", decided_at=100.0, stop_loss=0)
    args = SimpleNamespace(dry_run=False, max_active="3", size_usdt="100")

    mod._process_event(args, paths, state, registry, executor, _event(record))

    fetch_positions.assert_not_called()
    executor.open_sidecar_plan.assert_not_called()
    assert state["seen_shadow_ids"]["shadow-bad-plan"] == "rejected"
    row = _audit_rows(paths.audit)[0]
    assert row["event_type"] == "rejected"
    assert row["shadow_id"] == "shadow-bad-plan"
    assert row["reason"] == "missing_stop_loss"
    assert row["sidecar_policy_version"] == SIDECAR_POLICY_VERSION
    assert row["sidecar_risk_tier"] == "full"
    assert row["requested_size_usdt"] == 100.0


def test_process_event_persists_sidecar_entry_drift_rejection_audit(tmp_path):
    mod = _load_sidecar_module()

    paths = mod.SidecarPaths(
        owners=str(tmp_path / "owners.json"),
        audit=str(tmp_path / "audit.jsonl"),
    )
    state = {"seen_shadow_ids": {}}
    registry = mod.ShadowTacticalOwnerRegistry(paths.owners)
    record = _tactical_record(id="shadow-drift")
    event = _event(record)
    fake = MagicMock()
    fake._fetch_positions_with_retry.return_value = []
    fake.logger = MagicMock()
    fake.open_sidecar_plan.return_value = None
    fake._pending_drift_alerts = [
        {
            "type": "sidecar_entry_drift_rejected",
            "symbol": "WLD-USDT-SWAP",
            "side": "long",
            "drift_pct": 0.12,
            "decision": "abandon",
            "reason": "drift_too_large",
            "source": "sidecar",
            "shadow_id": "shadow-drift",
        },
        {
            "type": "sidecar_entry_drift_rejected",
            "symbol": "ETH-USDT-SWAP",
            "side": "short",
            "drift_pct": 0.08,
            "decision": "abandon",
            "reason": "drift_too_large",
            "source": "sidecar",
            "shadow_id": "other-shadow",
        },
        {
            "type": "entry_drift_abandoned",
            "symbol": "WLD-USDT-SWAP",
            "side": "long",
            "source": "main",
        }
    ]
    args = SimpleNamespace(dry_run=False, max_active="3", size_usdt="30")

    mod._process_event(args, paths, state, registry, fake, event)

    rows = [json.loads(line) for line in Path(paths.audit).read_text().splitlines()]
    drift_rows = [
        row for row in rows if row["event_type"] == "sidecar_entry_drift_rejected"
    ]
    assert drift_rows == [
        {
            "ts": ANY,
            "event_type": "sidecar_entry_drift_rejected",
            "shadow_id": "shadow-drift",
            "symbol": "WLD-USDT-SWAP",
            "side": "long",
            "drift_pct": 0.12,
            "decision": "abandon",
            "reason": "drift_too_large",
            "source": "sidecar",
            "sidecar_policy_version": SIDECAR_POLICY_VERSION,
            "sidecar_risk_tier": "full",
            "requested_size_usdt": 30.0,
        }
    ]
    rejected = next(row for row in rows if row["event_type"] == "rejected")
    assert rejected["reason"] == "executor_rejected"
    assert rejected["sidecar_policy_version"] == SIDECAR_POLICY_VERSION
    assert rejected["sidecar_risk_tier"] == "full"
    assert rejected["requested_size_usdt"] == 30.0
    assert fake._pending_drift_alerts == [
        {
            "type": "sidecar_entry_drift_rejected",
            "symbol": "ETH-USDT-SWAP",
            "side": "short",
            "drift_pct": 0.08,
            "decision": "abandon",
            "reason": "drift_too_large",
            "source": "sidecar",
            "shadow_id": "other-shadow",
        },
        {
            "type": "entry_drift_abandoned",
            "symbol": "WLD-USDT-SWAP",
            "side": "long",
            "source": "main",
        },
    ]


def test_process_event_passes_scalar_take_profit_as_level_list(tmp_path):
    mod = _load_sidecar_module()

    paths = mod.SidecarPaths(
        owners=str(tmp_path / "owners.json"),
        audit=str(tmp_path / "audit.jsonl"),
    )
    state = {"seen_shadow_ids": {}}
    registry = mod.ShadowTacticalOwnerRegistry(paths.owners)
    event = _event(_tactical_record(id="shadow-scalar-tp", take_profit=1.32))
    fake = MagicMock()
    fake._fetch_positions_with_retry.return_value = []
    fake.logger = MagicMock()
    fake.open_sidecar_plan.return_value = {
        "symbol": "WLD-USDT-SWAP",
        "side": "long",
        "amount_usdt": 30.0,
        "entry_order_id": "ord-1",
        "entry_clord_id": "stl-1",
        "sl_algo_id": "algo-1",
        "sl_algo_clord_id": "sl-1",
    }
    args = SimpleNamespace(dry_run=False, max_active="3", size_usdt="30")

    mod._process_event(args, paths, state, registry, fake, event)

    plan = fake.open_sidecar_plan.call_args.args[0]
    assert plan["take_profit"] == [1.32]


def test_process_event_rejects_when_exchange_position_guard_fetch_fails(tmp_path):
    mod = _load_sidecar_module()

    paths = mod.SidecarPaths(
        owners=str(tmp_path / "owners.json"),
        audit=str(tmp_path / "audit.jsonl"),
    )
    state = {"seen_shadow_ids": {}}
    registry = mod.ShadowTacticalOwnerRegistry(paths.owners)
    event = _event(_tactical_record(id="shadow-fetch-fail"))
    fake = MagicMock()
    fake._fetch_positions_with_retry.side_effect = RuntimeError("okx unavailable")
    fake.logger = MagicMock()
    fake.open_sidecar_plan = MagicMock()
    args = SimpleNamespace(dry_run=False, max_active="3", size_usdt="30")

    mod._process_event(args, paths, state, registry, fake, event)

    rows = [json.loads(line) for line in Path(paths.audit).read_text().splitlines()]
    assert rows[-1]["event_type"] == "rejected"
    assert rows[-1]["shadow_id"] == "shadow-fetch-fail"
    assert rows[-1]["reason"] == "same_symbol_exposure_unknown"
    assert state["seen_shadow_ids"]["shadow-fetch-fail"] == "rejected"
    fake.open_sidecar_plan.assert_not_called()


def test_stop_closes_only_proven_sidecar_owned_exposure(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    owners = tmp_path / "owners.json"
    audit = tmp_path / "audit.jsonl"
    owners.write_text(
        json.dumps(
            {
                "owners": {
                    "s1": {
                        "shadow_id": "s1",
                        "status": "open",
                        "symbol": "WLD-USDT-SWAP",
                        "side": "long",
                        "sl_algo_id": "algo-1",
                        "sl_algo_clord_id": "castliveWLD1",
                    },
                    "s2": {
                        "shadow_id": "s2",
                        "status": "open",
                        "symbol": "ETH-USDT-SWAP",
                        "side": "short",
                    },
                }
            }
        )
    )
    fake = MagicMock()
    fake.positions = {
        "WLD-USDT-SWAP": {
            "symbol": "WLD-USDT-SWAP",
            "side": "long",
            "shadow_id": "s1",
        }
    }
    fake._cancel_algo_by_id.return_value = True
    fake.close_position.return_value = {"id": "close-1"}
    monkeypatch.setattr(mod, "_build_executor", lambda paths, **kwargs: fake)

    code = mod.main(
        [
            "stop",
            "--owners",
            str(owners),
            "--audit",
            str(audit),
            "--state",
            str(tmp_path / "state.json"),
        ]
    )

    assert code == 0
    fake._cancel_algo_by_id.assert_called_once_with("WLD-USDT-SWAP", "algo-1")
    fake.close_position.assert_called_once_with(
        "WLD-USDT-SWAP",
        action_kind="sidecar_stop",
    )
    rows = [json.loads(line) for line in audit.read_text().splitlines()]
    assert [row["event_type"] for row in rows[:2]] == [
        "stop_closed",
        "stop_skipped_unproven",
    ]


def test_stop_matches_legacy_internal_symbol_position(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    owners = tmp_path / "owners.json"
    audit = tmp_path / "audit.jsonl"
    owners.write_text(
        json.dumps(
            {
                "owners": {
                    "s1": {
                        "shadow_id": "s1",
                        "status": "open",
                        "symbol": "ONDO-USDT",
                        "internal_symbol": "ONDO-USDT",
                        "exchange_symbol": "ONDO-USDT-SWAP",
                        "side": "long",
                        "sl_algo_id": "algo-1",
                        "sl_algo_clord_id": "castliveONDO1",
                    }
                }
            }
        )
    )
    fake = MagicMock()
    fake.positions = {
        "ONDO-USDT": {
            "symbol": "ONDO-USDT",
            "internal_symbol": "ONDO-USDT",
            "side": "long",
            "shadow_id": "s1",
        }
    }
    fake._cancel_algo_by_id.return_value = True
    fake.close_position.return_value = {"id": "close-1"}
    monkeypatch.setattr(mod, "_build_executor", lambda paths, **kwargs: fake)

    code = mod.main(
        [
            "stop",
            "--owners",
            str(owners),
            "--audit",
            str(audit),
            "--state",
            str(tmp_path / "state.json"),
        ]
    )

    assert code == 0
    fake._cancel_algo_by_id.assert_called_once_with("ONDO-USDT", "algo-1")
    fake.close_position.assert_called_once_with(
        "ONDO-USDT",
        action_kind="sidecar_stop",
    )


def test_monitor_routes_tactical_tp1_reduce(tmp_path):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    paths = mod.SidecarPaths(
        owners=str(tmp_path / "owners.json"),
        audit=str(tmp_path / "audit.jsonl"),
    )
    mod.ShadowTacticalOwnerRegistry(paths.owners).record_open(
        shadow_id="s1",
        symbol="ONDO-USDT-SWAP",
        side="long",
        amount_usdt=30.0,
        order_id="ord-1",
        entry_clord_id="cl-1",
        sl_algo_id="algo-1",
        sl_algo_clord_id="sl-1",
    )
    fake = MagicMock()
    fake.positions = {
        "ONDO-USDT-SWAP": {
            "symbol": "ONDO-USDT-SWAP",
            "internal_symbol": "ONDO-USDT",
            "side": "long",
            "shadow_id": "s1",
            "sidecar_source": "shadow_tactical_live",
            "take_profit_levels": [1.32, 1.38],
            "tp_filled": 0,
            "entry_price": 1.25,
            "stop_loss": 1.20,
            "original_sl": 1.20,
            "highest_price": 1.25,
            "lowest_price": 1.25,
            "atr_pct": 0.02,
            "open_time": 0,
        }
    }
    fake.check_stop_loss_take_profit.return_value = "tactical_tp1"
    fake.reduce_position.return_value = {"ok": True}

    result = mod.monitor_sidecar_owned_exposure(paths, fake)

    fake.reduce_position.assert_called_once_with(
        "ONDO-USDT-SWAP",
        0.5,
        tp_advance=1,
        action_kind="sidecar_tactical_tp1",
    )
    assert result["reduced"] == 1


def test_run_once_monitors_open_sidecar_position_without_new_events(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    events = tmp_path / "events.jsonl"
    state = tmp_path / "state.json"
    audit = tmp_path / "audit.jsonl"
    owners = tmp_path / "owners.json"
    state.write_text(json.dumps({"last_offset": 0, "seen_shadow_ids": {}}))
    owners.write_text(
        json.dumps(
            {
                "owners": {
                    "s1": {
                        "shadow_id": "s1",
                        "status": "open",
                        "symbol": "ONDO-USDT-SWAP",
                        "internal_symbol": "ONDO-USDT",
                        "exchange_symbol": "ONDO-USDT-SWAP",
                        "side": "long",
                        "sl_algo_id": "algo-1",
                        "sl_algo_clord_id": "sl-1",
                    }
                }
            }
        )
    )
    fake = MagicMock()
    fake.positions = {
        "ONDO-USDT-SWAP": {
            "symbol": "ONDO-USDT-SWAP",
            "internal_symbol": "ONDO-USDT",
            "side": "long",
            "shadow_id": "s1",
            "sidecar_source": "shadow_tactical_live",
            "take_profit_levels": [1.32, 1.38],
            "tp_filled": 0,
            "entry_price": 1.25,
            "stop_loss": 1.20,
            "original_sl": 1.20,
            "highest_price": 1.25,
            "lowest_price": 1.25,
            "atr_pct": 0.02,
            "open_time": 0,
        }
    }
    fake.check_stop_loss_take_profit.return_value = "tactical_tp1"
    fake.reduce_position.return_value = {"ok": True}
    monkeypatch.setattr(mod, "_build_executor", lambda paths, **kwargs: fake)

    code = mod.main(
        [
            "run",
            "--once",
            "--events",
            str(events),
            "--state",
            str(state),
            "--audit",
            str(audit),
            "--owners",
            str(owners),
            "--duration-hours",
            "1",
        ]
    )

    assert code == 0
    fake.check_stop_loss_take_profit.assert_called_once_with("ONDO-USDT-SWAP")


def test_monitor_reconciles_sidecar_owner_when_exchange_is_flat(tmp_path):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    paths = mod.SidecarPaths(
        owners=str(tmp_path / "owners.json"),
        audit=str(tmp_path / "audit.jsonl"),
        positions=str(tmp_path / "positions.json"),
        risk_state=str(tmp_path / "risk_state.json"),
        halt_state=str(tmp_path / "halt_state.json"),
    )
    mod.ShadowTacticalOwnerRegistry(paths.owners).record_open(
        shadow_id="s1",
        symbol="WLD-USDT-SWAP",
        side="short",
        amount_usdt=30.0,
        order_id="ord-1",
        entry_clord_id="cl-1",
        sl_algo_id="algo-1",
        sl_algo_clord_id="sl-1",
    )
    Path(paths.halt_state).write_text(
        json.dumps(
            {
                "halted": True,
                "reason": "okx_sl_cancel_failed:WLD-USDT-SWAP",
                "triggered_at": 1.0,
                "triggered_by": "executor",
                "resume_at": 0.0,
                "resume_by": "",
                "reconciliation_pending": False,
                "reconciliation_result": None,
            }
        )
    )
    fake = SimpleNamespace(
        exchange_id="okx",
        positions={
            "WLD-USDT-SWAP": {
                "symbol": "WLD-USDT-SWAP",
                "internal_symbol": "WLD-USDT",
                "side": "short",
                "shadow_id": "s1",
                "sidecar_source": "shadow_tactical_live",
            }
        },
        _halted_symbols={
            "WLD-USDT-SWAP": {"reason": "sl_cancel_failed", "halted_at": 1.0}
        },
        _fetch_positions_with_retry=MagicMock(return_value=[]),
        _normalize_okx_position=MagicMock(side_effect=lambda raw: raw),
        _save_positions=MagicMock(),
        check_stop_loss_take_profit=MagicMock(return_value=None),
        clear_symbol_halt=MagicMock(return_value=1),
        logger=MagicMock(),
    )

    result = mod.monitor_sidecar_owned_exposure(paths, fake)

    owners = mod.ShadowTacticalOwnerRegistry(paths.owners).load()["owners"]
    assert owners["s1"]["status"] == "closed"
    assert owners["s1"]["close_reason"] == "exchange_flat_reconciled"
    assert "WLD-USDT-SWAP" not in fake.positions
    assert mod._active_owner_count(mod.ShadowTacticalOwnerRegistry(paths.owners)) == 0
    assert result["closed"] == 1
    assert result["exchange_flat"] == 1
    fake._save_positions.assert_called_once()
    fake.check_stop_loss_take_profit.assert_not_called()
    fake.clear_symbol_halt.assert_called_once_with(
        "WLD-USDT-SWAP",
        source="sidecar_monitor_exchange_flat",
    )
    rows = [json.loads(line) for line in Path(paths.audit).read_text().splitlines()]
    assert rows[-1]["event_type"] == "monitor_reconciled_flat"
    assert rows[-1]["cleared_symbol_halt"] is True
    assert rows[-1]["cleared_global_halt"] is True
    halt_state = json.loads(Path(paths.halt_state).read_text())
    assert halt_state["halted"] is False


def test_monitor_skips_flat_reconciliation_when_exchange_fetch_fails(tmp_path):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    paths = mod.SidecarPaths(
        owners=str(tmp_path / "owners.json"),
        audit=str(tmp_path / "audit.jsonl"),
        positions=str(tmp_path / "positions.json"),
        risk_state=str(tmp_path / "risk_state.json"),
        halt_state=str(tmp_path / "halt_state.json"),
    )
    mod.ShadowTacticalOwnerRegistry(paths.owners).record_open(
        shadow_id="s1",
        symbol="WLD-USDT-SWAP",
        side="short",
        amount_usdt=30.0,
        order_id="ord-1",
        entry_clord_id="cl-1",
        sl_algo_id="algo-1",
        sl_algo_clord_id="sl-1",
    )
    fake = SimpleNamespace(
        exchange_id="okx",
        positions={
            "WLD-USDT-SWAP": {
                "symbol": "WLD-USDT-SWAP",
                "internal_symbol": "WLD-USDT",
                "side": "short",
                "shadow_id": "s1",
                "sidecar_source": "shadow_tactical_live",
            }
        },
        _fetch_positions_with_retry=MagicMock(side_effect=RuntimeError("timeout")),
        _normalize_okx_position=MagicMock(side_effect=lambda raw: raw),
        _save_positions=MagicMock(),
        check_stop_loss_take_profit=MagicMock(return_value=None),
        logger=MagicMock(),
    )

    result = mod.monitor_sidecar_owned_exposure(paths, fake)

    owners = mod.ShadowTacticalOwnerRegistry(paths.owners).load()["owners"]
    assert owners["s1"]["status"] == "open"
    assert "WLD-USDT-SWAP" in fake.positions
    assert result["skipped"] == 1
    fake._save_positions.assert_not_called()
    fake.check_stop_loss_take_profit.assert_not_called()
    rows = [json.loads(line) for line in Path(paths.audit).read_text().splitlines()]
    assert rows[-1]["event_type"] == "monitor_skipped_exchange_unknown"


def test_monitor_records_pending_external_close_when_exchange_is_flat(tmp_path):
    spec = importlib.util.spec_from_file_location("shadow_tactical_live_sidecar", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    paths = mod.SidecarPaths(
        owners=str(tmp_path / "owners.json"),
        audit=str(tmp_path / "audit.jsonl"),
        positions=str(tmp_path / "positions.json"),
        risk_state=str(tmp_path / "risk_state.json"),
        halt_state=str(tmp_path / "halt_state.json"),
    )
    mod.ShadowTacticalOwnerRegistry(paths.owners).record_open(
        shadow_id="doge-shadow",
        symbol="DOGE-USDT-SWAP",
        side="short",
        amount_usdt=30.0,
        order_id="open-1",
        entry_clord_id="entry-cl",
        sl_algo_id="sl-algo",
        sl_algo_clord_id="sl-cl",
    )
    ledger = MagicMock()
    ledger.record_pending_external_close.return_value = {
        "event_id": "pending-close-1",
        "pnl_status": "pending",
    }
    fake = SimpleNamespace(
        exchange_id="okx",
        positions={
            "DOGE-USDT-SWAP": {
                "symbol": "DOGE-USDT-SWAP",
                "internal_symbol": "DOGE-USDT",
                "side": "short",
                "shadow_id": "doge-shadow",
                "sidecar_source": "shadow_tactical_live",
                "entry_price": 0.07227,
                "amount_usdt": 30.0,
                "leverage": 5,
                "open_time": 123.0,
                "sl_algo_id": "sl-algo",
                "sl_algo_clord_id": "sl-cl",
                "gate_metadata": {"tactical_track_gate": "pass"},
            }
        },
        _fetch_positions_with_retry=MagicMock(return_value=[]),
        _normalize_okx_position=MagicMock(side_effect=lambda raw: raw),
        _save_positions=MagicMock(),
        check_stop_loss_take_profit=MagicMock(return_value=None),
        logger=MagicMock(),
        ledger=ledger,
    )

    result = mod.monitor_sidecar_owned_exposure(paths, fake)

    assert result["exchange_flat"] == 1
    ledger.record_pending_external_close.assert_called_once_with(
        symbol="DOGE-USDT-SWAP",
        side="short",
        entry_price=0.07227,
        amount_usdt=30.0,
        leverage=5,
        estimated_pnl=None,
        position_id=None,
        entry_request_id="doge-shadow",
        opened_at=123.0,
        closed_at=ANY,
        sl_algo_id="sl-algo",
        sl_algo_clord_id="sl-cl",
        entry_attribution={"tactical_track_gate": "pass"},
    )
    owners = mod.ShadowTacticalOwnerRegistry(paths.owners).load()["owners"]
    assert owners["doge-shadow"]["close_ledger_event_id"] == "pending-close-1"
    rows = [json.loads(line) for line in Path(paths.audit).read_text().splitlines()]
    assert rows[-1]["ledger_close_recorded"] is True
    assert rows[-1]["ledger_close_event_id"] == "pending-close-1"
