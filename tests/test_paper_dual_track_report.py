import pytest
from agents.trading.paper_dual_track_report import compute_gap


def _t(book, net, closed_at=0.0):
    return {"book": book, "net_pnl": net, "closed_at": closed_at}


def test_gap_basic_metrics_and_sign():
    trades = [
        _t("realistic", 5.0), _t("realistic", -2.0),
        _t("idealized", 1.0), _t("idealized", -8.0),
    ]
    g = compute_gap(trades, window_days=None, min_trades=1)
    assert g["realistic"]["n"] == 2
    assert g["realistic"]["total_net_pnl"] == 3.0
    assert g["idealized"]["total_net_pnl"] == -7.0
    assert g["limit_discipline_value"] == 10.0
    assert g["low_sample"] is False


def test_missing_book_field_counts_as_realistic():
    g = compute_gap([{"net_pnl": 4.0}], window_days=None, min_trades=1)
    assert g["realistic"]["n"] == 1 and g["realistic"]["total_net_pnl"] == 4.0


def test_low_sample_flagged():
    g = compute_gap([_t("realistic", 1.0)], window_days=None, min_trades=5)
    assert g["low_sample"] is True


def test_win_pct_and_drawdown():
    trades = [_t("realistic", 10.0, 1), _t("realistic", -4.0, 2), _t("realistic", -3.0, 3)]
    g = compute_gap(trades, window_days=None, min_trades=1)
    assert g["realistic"]["win_pct"] == pytest.approx(33.33, abs=0.1)
    assert g["realistic"]["max_drawdown"] == pytest.approx(7.0)


def test_format_gap_contains_verdict_and_low_sample():
    from agents.trading.paper_dual_track_report import format_gap
    g = compute_gap([{"book": "realistic", "net_pnl": 3.0}], window_days=None, min_trades=5)
    out = format_gap(g)
    assert "limit_discipline_value" in out
    assert "样本不足" in out


def test_paper_gap_command_format_smoke():
    from agents.trading.paper_dual_track_report import compute_gap, format_gap
    g = compute_gap([{"book": "realistic", "net_pnl": 2.0}, {"book": "idealized", "net_pnl": -1.0}],
                    window_days=None, min_trades=1)
    out = format_gap(g)
    assert "realistic" in out and "idealized" in out
    assert g["limit_discipline_value"] == 3.0
