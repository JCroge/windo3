import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils.state_paths import get_state_paths, reset_state_paths


def _prepare_state_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    reset_state_paths()
    path = Path(get_state_paths(refresh=True).riskguard_state)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _quiet_logger():
    return SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )


def _minimal_judge():
    from agents.trading.judge import MultiJudge

    judge = MultiJudge.__new__(MultiJudge)
    judge.config = {"tactical_daily_loss_limit_usdt": -10.0}
    judge.logger = _quiet_logger()
    judge._open_positions = set()
    judge._pending_open_symbols = set()
    judge._position_slots = {}
    judge._pending_open_slots = {}
    judge._pending_open_ts = {}
    judge._pending_ttl = 120
    judge._max_concurrent_positions = 3
    judge._candidate_ranker = SimpleNamespace(tactical_slot=1)
    judge._rejection_attribution = (
        lambda action, plan, reason, **kw: {"blocked_by": reason}
    )
    judge._record_rejected_plan = lambda *a, **k: None
    return judge


@pytest.mark.asyncio
async def test_judge_rejects_tactical_open_when_circuit_is_paused(tmp_path, monkeypatch):
    state_path = _prepare_state_dir(tmp_path, monkeypatch)
    state_path.write_text(json.dumps({
        "tactical_circuit": {
            "daily_date": "2099-01-01",
            "daily_pnl": 0.0,
            "loss_streak": 3,
            "pause_until": time.time() + 3600,
            "pause_reason": "loss_streak",
        }
    }))

    judge = _minimal_judge()
    published = []

    async def capture_publish(msg_type, payload, **kwargs):
        published.append((msg_type, payload, kwargs))

    judge.publish = capture_publish

    decision = {
        "symbol": "ETH-USDT",
        "timestamp": time.time(),
        "action": "open_long",
        "confidence": 70,
        "plan": {
            "track": "tactical",
            "slot_type": "tactical",
            "exit_profile": "tactical_v1",
            "size_usdt": 10,
            "leverage": 5,
        },
        "size_pct": 1,
    }

    published_open = await judge._gate_and_publish_open("ETH-USDT", decision, {})

    assert published_open is False
    assert judge._pending_open_symbols == set()
    assert published[0][0] == "trade_decision"
    assert published[0][1]["action"] == "hold"
    assert published[0][1]["reasoning"] == "Tactical circuit blocked: tactical_paused"
    assert published[0][1]["attribution"]["blocked_by"] == "tactical_paused"


@pytest.mark.asyncio
async def test_judge_rejects_tactical_open_when_daily_loss_limit_is_hit(tmp_path, monkeypatch):
    state_path = _prepare_state_dir(tmp_path, monkeypatch)
    judge = _minimal_judge()
    state_path.write_text(json.dumps({
        "tactical_circuit": {
            "daily_date": judge._tactical_day_key(),
            "daily_pnl": -10.01,
            "loss_streak": 2,
            "pause_until": 0,
        }
    }))

    published = []

    async def capture_publish(msg_type, payload, **kwargs):
        published.append((msg_type, payload, kwargs))

    judge.publish = capture_publish

    decision = {
        "symbol": "ETH-USDT",
        "timestamp": time.time(),
        "action": "open_short",
        "confidence": 70,
        "plan": {
            "track": "tactical",
            "slot_type": "tactical",
            "exit_profile": "tactical_v1",
            "size_usdt": 10,
            "leverage": 5,
        },
        "size_pct": 1,
    }

    published_open = await judge._gate_and_publish_open("ETH-USDT", decision, {})

    assert published_open is False
    assert published[0][1]["reasoning"] == (
        "Tactical circuit blocked: tactical_daily_loss_limit"
    )
    assert published[0][1]["attribution"]["blocked_by"] == "tactical_daily_loss_limit"


def test_portfolio_risk_guard_persists_tactical_circuit_state(tmp_path, monkeypatch):
    _prepare_state_dir(tmp_path, monkeypatch)

    from agents.trading.portfolio_risk_guard import PortfolioRiskGuard

    config = {
        "tactical_daily_loss_limit_usdt": -10.0,
        "tactical_loss_streak_pause_count": 3,
        "tactical_loss_streak_pause_minutes": 60,
    }
    guard = PortfolioRiskGuard(config)
    guard.logger = _quiet_logger()
    guard.record_tactical_close("ETH-USDT", -2.5, "stop_loss", {})
    guard._tactical_pause_until = time.time() + 600
    guard._tactical_pause_reason = "loss_streak"
    guard._save_state()

    restored = PortfolioRiskGuard(config)
    restored.logger = _quiet_logger()
    restored._load_state()

    assert restored._tactical_daily_pnl == pytest.approx(-2.5)
    assert restored._tactical_loss_streak == 1
    assert restored._tactical_pause_until == pytest.approx(guard._tactical_pause_until)
    assert restored._tactical_pause_reason == "loss_streak"

    state = json.loads(Path(get_state_paths().riskguard_state).read_text())
    assert state["tactical_circuit"]["daily_pnl"] == pytest.approx(-2.5)
    assert state["tactical_circuit"]["loss_streak"] == 1
