"""P2-17: config_loader 失败的 env 兜底路径必须把风险限额 clamp 到 HARD_LIMITS 内，
不得 fail-open 到未约束值。clamp（不 raise）与 _validate_hard_limits（超界 raise）区分。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_clamp_to_hard_limits_clamps_out_of_range():
    from utils.config_loader import clamp_to_hard_limits, HARD_LIMITS
    out = clamp_to_hard_limits({
        'max_trade_amount': 99999.0,        # > hi
        'max_drawdown_pct': 1.0,            # < lo
        'daily_pnl_hard_stop': -99999.0,    # < lo（更负）
        'effective_balance_cap': None,      # 保持 None
    })
    assert out['max_trade_amount'] == HARD_LIMITS['max_trade_amount'][1]
    assert out['max_drawdown_pct'] == HARD_LIMITS['max_drawdown_pct'][0]
    assert out['daily_pnl_hard_stop'] == HARD_LIMITS['daily_pnl_hard_stop'][0]
    assert out['effective_balance_cap'] is None


def test_clamp_leaves_in_range_untouched():
    from utils.config_loader import clamp_to_hard_limits
    out = clamp_to_hard_limits({'max_trade_amount': 30.0, 'max_drawdown_pct': 20.0})
    assert out['max_trade_amount'] == 30.0
    assert out['max_drawdown_pct'] == 20.0
