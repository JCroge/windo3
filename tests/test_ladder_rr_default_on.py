"""trend-entry-levers-default-on: lever2(ladder_rr_enabled) 默认开 + env 逃生阀。"""
from utils.config_loader import load_config, DEFAULTS


def test_ladder_rr_enabled_default_true():
    assert DEFAULTS.get("ladder_rr_enabled") is True


def test_ladder_rr_env_escape_valve(monkeypatch):
    # testnet 模式跳过 live 凭证硬校验，专测 env 覆盖逻辑
    monkeypatch.setenv("USE_TESTNET", "true")
    monkeypatch.setenv("LADDER_RR_ENABLED", "false")
    cfg = load_config()
    assert cfg["ladder_rr_enabled"] is False


def test_path_evidence_stays_default_off():
    # lever1 本 change 不动，维持默认关
    assert DEFAULTS.get("path_evidence_aligned_enabled") in (None, False)
