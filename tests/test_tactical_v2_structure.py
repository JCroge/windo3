def _klines():
    rows = []
    for index in range(56):
        close = 100.0 + index * 0.02
        rows.append(
            [
                index * 900_000,
                close - 0.01,
                close + 0.20,
                close - 0.20,
                close,
                1000.0,
            ]
        )

    # Confirmed local high at 48, followed by a closed break at 51.
    rows[48][2] = 104.0
    rows[47][2] = 102.0
    rows[49][2] = 102.0
    rows[51][4] = 104.5
    rows[51][2] = 104.7
    return rows


def _analyze(rows, quality=None):
    from agents.trading.tech_analyst import MultiTechAnalyst

    return MultiTechAnalyst._analyze_entry_timing_15m(
        None,
        rows,
        quality or {"tf_15m_ok": True},
    )


def test_structure_metadata_uses_latest_closed_bar_and_confirmed_break():
    rows = _klines()

    result = _analyze(rows)

    assert result["tf_15m_closed_bar_ts"] == rows[-2][0]
    assert result["tf_15m_structure_token"].startswith("break_up:")


def test_forming_bar_changes_do_not_change_structure_identity():
    rows = _klines()
    changed_forming = [list(row) for row in rows]
    changed_forming[-1][2] = 999.0
    changed_forming[-1][3] = 1.0
    changed_forming[-1][4] = 500.0

    before = _analyze(rows)
    after = _analyze(changed_forming)

    assert after["tf_15m_closed_bar_ts"] == before["tf_15m_closed_bar_ts"]
    assert after["tf_15m_structure_token"] == before["tf_15m_structure_token"]


def test_stale_structure_has_no_reset_identity():
    result = _analyze(
        _klines(),
        {"tf_15m_ok": False, "tf_15m_stale": True},
    )

    assert result["tf_15m_available"] is False
    assert result["tf_15m_closed_bar_ts"] is None
    assert result["tf_15m_structure_token"] is None
