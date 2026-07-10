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


def test_reviewer_persists_tactical_attribution(tmp_path):
    from agents.trading.reviewer import ReviewerAgent

    reviewer = ReviewerAgent.__new__(ReviewerAgent)
    reviewer.trade_history = []
    reviewer.history_file = str(tmp_path / "trade_history.json")
    reviewer.logger = type(
        "L",
        (),
        {"info": lambda *a, **k: None, "warning": lambda *a, **k: None},
    )()
    reviewer._save_trade_history = lambda: None

    msg = {
        "type": "execution_result",
        "timestamp": 123.0,
        "payload": {
            "status": "executed",
            "action": "close",
            "symbol": "WLD-USDT",
            "result": {
                "pnl": 1.2,
                "side": "short",
                "entry_price": 0.385,
                "exit_price": 0.382,
                "attribution": {
                    "track": "tactical",
                    "exit_profile": "tactical_v1",
                    "slot_type": "tactical",
                    "tactical_close_reason": "tactical_tp1",
                },
            },
        },
    }

    import asyncio

    asyncio.run(reviewer._process_trade_result(msg))

    assert reviewer.trade_history[0]["track"] == "tactical"
    assert reviewer.trade_history[0]["exit_profile"] == "tactical_v1"
    assert reviewer.trade_history[0]["tactical_close_reason"] == "tactical_tp1"


def test_counterfactual_rejection_records_tactical_metadata(tmp_path):
    from utils.counterfactual_ledger import CounterfactualLedger

    ledger = CounterfactualLedger(enabled=True, logger=None)
    ledger._events_path = str(tmp_path / "events.jsonl")
    ledger._lifecycle_path = str(tmp_path / "lifecycle.json")
    ledger._active = {}

    ledger.record_rejection(
        "WLD-USDT", "short",
        {
            "entry_zone": [0.385],
            "stop_loss": 0.3904,
            "take_profit": [0.3817],
            "track": "tactical",
            "exit_profile": "tactical_v1",
            "tactical_effective_rr": 0.8,
            "tactical_expected_value": 0.12,
        },
        "mixed", -58, 70, "main_quality_failed",
        {
            "track": "tactical",
            "exit_profile": "tactical_v1",
            "tactical_source": "main_quality_failed",
        },
    )

    rec = next(iter(ledger._active.values()))
    assert rec["track"] == "tactical"
    assert rec["exit_profile"] == "tactical_v1"
    assert rec["tactical_source"] == "main_quality_failed"
