import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = str(ROOT / "scripts" / "shadow_tactical_live_sidecar.py")


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


def test_run_dry_run_processes_new_tactical_event(tmp_path):
    events = tmp_path / "events.jsonl"
    state = tmp_path / "state.json"
    audit = tmp_path / "audit.jsonl"
    rec = {
        "id": "s1",
        "symbol": "WLD-USDT-SWAP",
        "side": "long",
        "entry_price": 1.25,
        "stop_loss": 1.20,
        "take_profit": [1.32],
        "leverage": 20,
        "track": "tactical",
        "exit_profile": "tactical_v1",
    }
    events.write_text(json.dumps({"event_type": "rejected_plan_created", "record": rec}) + "\n")

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
    assert row["shadow_id"] == "s1"


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
    monkeypatch.setattr(mod, "_build_executor", lambda paths: fake)

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
