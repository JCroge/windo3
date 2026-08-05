import pytest

from utils.config_loader import ConfigError, DEFAULTS, HARD_LIMITS, format_banner, load_config
from utils.state_paths import StatePaths


def test_tactical_v2_defaults_are_first_cohort_values():
    assert DEFAULTS["tactical_v2_mode"] == "off"
    assert DEFAULTS["tactical_v2_margin_usdt"] == 100.0
    assert DEFAULTS["tactical_v2_max_concurrent"] == 3
    assert DEFAULTS["tactical_v2_max_leverage"] == 5
    assert DEFAULTS["tactical_v2_entry_max_worse_r"] == 0.10
    assert DEFAULTS["tactical_v2_entry_ttl_seconds"] == 900
    assert DEFAULTS["tactical_v2_max_hold_minutes"] == 90
    assert DEFAULTS["tactical_v2_rolling_loss_limit_usdt"] == -15.0
    assert DEFAULTS["tactical_v2_loss_streak_count"] == 3
    assert DEFAULTS["tactical_v2_loss_streak_pause_minutes"] == 60
    assert DEFAULTS["tactical_v2_status_stale_seconds"] == 90


def test_tactical_v2_fixed_limits_are_validated():
    assert HARD_LIMITS["tactical_v2_margin_usdt"] == (100.0, 100.0)
    assert HARD_LIMITS["tactical_v2_max_concurrent"] == (3, 3)
    assert HARD_LIMITS["tactical_v2_max_leverage"] == (1, 5)
    assert HARD_LIMITS["tactical_v2_entry_max_worse_r"] == (0.10, 0.10)
    assert HARD_LIMITS["tactical_v2_entry_ttl_seconds"] == (900, 900)
    assert HARD_LIMITS["tactical_v2_max_hold_minutes"] == (90, 90)
    assert HARD_LIMITS["tactical_v2_rolling_loss_limit_usdt"] == (-15.0, -15.0)


def test_tactical_v2_env_overrides_are_parsed(monkeypatch):
    monkeypatch.setenv("TACTICAL_V2_MODE", "shadow")
    monkeypatch.setenv("TACTICAL_V2_MARGIN_USDT", "100")
    monkeypatch.setenv("TACTICAL_V2_MAX_CONCURRENT", "3")

    cfg = load_config(env_file=None, strict_live_check=False)

    assert cfg["tactical_v2_mode"] == "shadow"
    assert cfg["tactical_v2_margin_usdt"] == 100.0
    assert cfg["tactical_v2_max_concurrent"] == 3


@pytest.mark.parametrize(
    "key,value",
    [
        ("TACTICAL_V2_MODE", "automatic"),
        ("TACTICAL_V2_MARGIN_USDT", "99"),
        ("TACTICAL_V2_MAX_CONCURRENT", "4"),
        ("TACTICAL_V2_ENTRY_MAX_WORSE_R", "nan"),
    ],
)
def test_invalid_tactical_v2_config_fails_closed(monkeypatch, key, value):
    monkeypatch.setenv(key, value)

    with pytest.raises(ConfigError):
        load_config(env_file=None, strict_live_check=False)


def test_tactical_paths_follow_namespace():
    paths = StatePaths.for_namespace("testnet")

    assert paths.tactical_v2_events == "data/testnet_tactical_v2_events.jsonl"
    assert paths.tactical_v2_state == "data/testnet_tactical_v2_state.json"
    assert paths.tactical_v2_status == "data/testnet_tactical_v2_status.json"
    assert paths.sidecar_retirement == "data/testnet_sidecar_retirement.json"


def test_banner_displays_tactical_v2_risk_contract(monkeypatch):
    monkeypatch.setenv("STATE_NAMESPACE", "testnet")
    cfg = dict(DEFAULTS, tactical_v2_mode="shadow", use_testnet=True)

    banner = format_banner(cfg)

    assert "Tactical V2" in banner
    assert "SHADOW" in banner
    assert "100.0U x 3" in banner
    assert "-15.0U/24h" in banner
