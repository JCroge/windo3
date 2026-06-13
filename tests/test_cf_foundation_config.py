from utils.config_loader import DEFAULTS, HARD_LIMITS
from utils.state_paths import get_state_paths


def test_cf_config_defaults():
    assert DEFAULTS["decision_tape_enabled"] is True
    assert DEFAULTS["tick_capture_enabled"] is True
    assert DEFAULTS["cf_min_sample"] == 30
    assert DEFAULTS["cf_lowconf_sample"] == 100
    assert DEFAULTS["decision_tape_retention_days"] == 90
    assert DEFAULTS["tick_capture_retention_days"] == 30


def test_cf_hard_limits():
    assert HARD_LIMITS["cf_min_sample"] == (1, 1000)
    assert HARD_LIMITS["cf_lowconf_sample"] == (1, 5000)


def test_state_paths_new_files_live():
    sp = get_state_paths("live", refresh=True)
    assert sp.decision_replay_tape == "data/decision_replay_tape.jsonl"
    assert sp.klines_1s == "data/klines_1s.db"


def test_state_paths_new_files_testnet():
    sp = get_state_paths("testnet", refresh=True)
    assert sp.decision_replay_tape == "data/testnet_decision_replay_tape.jsonl"
    assert sp.klines_1s == "data/testnet_klines_1s.db"
