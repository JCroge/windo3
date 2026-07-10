def test_tactical_config_defaults_are_present():
    from utils.config_loader import DEFAULTS

    assert DEFAULTS["tactical_track_enabled"] is False
    assert DEFAULTS["tactical_shadow_only"] is True
    assert DEFAULTS["main_quality_gate_enabled"] is True
    assert DEFAULTS["main_quality_min_provenance"] == 0.20
    assert DEFAULTS["main_quality_block_llm_reversal"] is True
    assert DEFAULTS["tactical_max_leverage"] == 5
    assert DEFAULTS["tactical_default_position_pct"] == 0.70
    assert DEFAULTS["tactical_max_hold_minutes"] == 90
    assert DEFAULTS["tactical_daily_loss_limit_usdt"] == -10.0


def test_tactical_env_overrides_are_loaded(monkeypatch):
    monkeypatch.setenv("USE_TESTNET", "true")
    monkeypatch.setenv("TACTICAL_TRACK_ENABLED", "true")
    monkeypatch.setenv("TACTICAL_SHADOW_ONLY", "false")
    monkeypatch.setenv("TACTICAL_MAX_LEVERAGE", "4")
    monkeypatch.setenv("TACTICAL_DEFAULT_POSITION_PCT", "0.5")

    from utils.config_loader import load_config

    cfg = load_config()

    assert cfg["tactical_track_enabled"] is True
    assert cfg["tactical_shadow_only"] is False
    assert cfg["tactical_max_leverage"] == 4
    assert cfg["tactical_default_position_pct"] == 0.5
