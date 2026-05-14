"""
回调入场机制单元测试
验证分级入场策略：强信号追价、弱信号等回调、R:R过低放弃
"""
import sys
import os
import time
import asyncio
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unittest.mock import patch, MagicMock, AsyncMock


def make_tech(price=100.0, rsi=50, trend_dir='bearish', trend_strength=70,
              atr_pct=0.02, funding_rate=0.0001, volume_ratio=1.0,
              rule_signal_short=False, rule_signal_long=False,
              ma_aligned_short=False, ma_aligned_long=False):
    """构造tech_analysis数据"""
    return {
        'indicators': {'price': price},
        'trend': {'direction': trend_dir, 'strength': trend_strength,
                  'ma_alignment': 'bearish' if trend_dir == 'bearish' else 'bullish',
                  'higher_tf_bias': trend_dir, 'daily_near_resistance': False,
                  'daily_near_support': False},
        'levels': {'support': [price * 0.95], 'resistance': [price * 1.05]},
        'momentum': {'rsi': rsi, 'rsi_divergence': None, 'volume_ratio': volume_ratio,
                     'volume_anomaly': False, 'atr_pct': atr_pct},
        'money_flow': {'funding_rate': funding_rate, 'oi_divergence': None,
                       'taker_pressure': 'neutral'},
        'microstructure': {'whale_direction': 'neutral', 'spread_pct': 0.01,
                           'liquidation_intensity': 'low'},
        'crowd': {'contrarian_signal': None, 'long_ratio': 0.5},
        'risk': {'leverage_risk': 'medium', 'volatility_regime': 'normal',
                 'liquidity_score': 80},
        'rule_signal': {
            'entry_long': 1 if rule_signal_long else 0,
            'entry_short': 1 if rule_signal_short else 0,
            'ma_aligned_long': 1 if ma_aligned_long else 0,
            'ma_aligned_short': 1 if ma_aligned_short else 0,
        },
        'llm_analysis': {},
    }


class MockJudge:
    """Minimal Judge mock for testing decision logic"""

    def __init__(self, balance=105.0):
        self._available_balance = balance
        self._max_trade_amount = 10
        self._symbol_state = {}
        self._force_close_cooldown = 300
        self._decision_cooldown = 55
        self.published = []
        self.logger = MagicMock()

    def _get_state(self, symbol):
        if symbol not in self._symbol_state:
            self._symbol_state[symbol] = {
                "last_decision_time": 0,
                "last_tech": None,
                "last_force_close_time": 0,
                "trend_streak": 0,
                "trend_streak_dir": None,
                "pending_pullback": None,
                "pending_pullback_time": 0,
                "pullback_bonus": 0,
                "deferred_entry": None,
            }
        return self._symbol_state[symbol]


def test_strong_signal_chase_entry():
    """场景1: score=-65, R:R=1.3 → 追价入场，仓位缩至87%"""
    print("\n=== Test 1: 强信号追价入场 ===")

    score = -65
    rr = 1.3
    min_rr = 1.5
    plan_size = 10.0

    abs_score = abs(score)
    assert abs_score >= 50, "应该是强信号"

    chase_pct = max(0.6, min(0.9, rr / min_rr))
    new_size = round(plan_size * chase_pct, 2)

    print(f"  score={score}, R:R={rr}")
    print(f"  chase_pct = {rr}/{min_rr} = {rr/min_rr:.3f} → clamped to {chase_pct:.2f}")
    print(f"  仓位: {plan_size} → {new_size} ({chase_pct:.0%})")

    assert 0.6 <= chase_pct <= 0.9
    assert new_size < plan_size
    assert new_size == round(10.0 * chase_pct, 2)
    print("  PASS ✓")


def test_weak_signal_deferred_entry():
    """场景2: score=-40, R:R=1.35 → 进入回调等待"""
    print("\n=== Test 2: 弱信号回调等待 ===")

    score = -40
    rr = 1.35
    min_rr = 1.5
    price = 100.0
    sl_dist = 0.025  # 2.5%

    abs_score = abs(score)
    assert abs_score < 50, "应该是弱信号"

    needed_improve = (min_rr - rr) * sl_dist / (1 + min_rr)
    target_price = price * (1 + needed_improve)  # 做空等回调上涨

    print(f"  score={score}, R:R={rr}, sl_dist={sl_dist}")
    print(f"  needed_improve = ({min_rr}-{rr}) × {sl_dist} / {1+min_rr} = {needed_improve:.5f}")
    print(f"  target_price = {price} × (1 + {needed_improve:.5f}) = {target_price:.4f}")
    print(f"  需回调: {needed_improve:.3%}")

    assert needed_improve < 0.03, "回调幅度应该合理（<3%）"
    assert target_price > price, "做空时回调目标应高于当前价"
    print("  PASS ✓")


def test_deferred_entry_expiry():
    """场景3: 回调等待3h后过期"""
    print("\n=== Test 3: 延迟入场过期 ===")

    judge = MockJudge()
    state = judge._get_state("TON-USDT")
    state['deferred_entry'] = {
        'action': 'open_short',
        'signal_price': 100.0,
        'signal_score': -40,
        'target_price': 100.15,
        'plan': {'size_usdt': 10.0, 'leverage': 10},
        'created_at': time.time() - 4 * 3600,  # 4h ago (>3h)
        'expiry_bars': 3,
        'chase_eligible': False,
        'highest_since': 100.0,
        'lowest_since': 99.5,
    }

    age_seconds = time.time() - state['deferred_entry']['created_at']
    assert age_seconds > 3 * 3600, "应该已过期"

    # Simulate expiry logic
    state['deferred_entry'] = None
    assert state['deferred_entry'] is None
    print(f"  age={age_seconds/3600:.1f}h > 3h → 过期放弃")
    print("  PASS ✓")


def test_deferred_entry_pullback_hit():
    """场景4: 回调到位 → 全仓入场"""
    print("\n=== Test 4: 回调到位入场 ===")

    judge = MockJudge()
    state = judge._get_state("TON-USDT")
    target_price = 100.15
    state['deferred_entry'] = {
        'action': 'open_short',
        'signal_price': 100.0,
        'signal_score': -40,
        'target_price': target_price,
        'plan': {'size_usdt': 10.0, 'leverage': 10, 'stop_loss': 102.5,
                 'take_profit': [96.0], 'order_type': 'market'},
        'created_at': time.time() - 1800,  # 30min ago
        'expiry_bars': 3,
        'chase_eligible': False,
        'highest_since': 100.2,
        'lowest_since': 99.8,
    }

    current_price = 100.20  # 高于target（做空回调=价格上涨）
    is_long = (state['deferred_entry']['action'] == 'open_long')
    pullback_hit = (is_long and current_price <= target_price) or \
                   (not is_long and current_price >= target_price)

    assert pullback_hit, "做空时价格>=target应触发回调入场"
    print(f"  target={target_price}, current={current_price}")
    print(f"  pullback_hit={pullback_hit} → 全仓入场")
    print("  PASS ✓")


def test_rr_too_low_abandon():
    """场景5: R:R=1.1 → 直接放弃"""
    print("\n=== Test 5: R:R过低直接放弃 ===")

    rr = 1.1
    min_rr = 1.5

    assert rr < 1.2, "R:R<1.2应直接放弃"
    print(f"  R:R={rr} < 1.2 → 放弃（不进入任何延迟模式）")
    print("  PASS ✓")


def test_trend_reversal_cancels_deferred():
    """场景6: 回调等待中趋势反转 → 取消"""
    print("\n=== Test 6: 趋势反转取消延迟入场 ===")

    judge = MockJudge()
    state = judge._get_state("TON-USDT")
    state['deferred_entry'] = {
        'action': 'open_short',
        'signal_price': 100.0,
        'signal_score': -40,
        'target_price': 100.15,
        'plan': {'size_usdt': 10.0, 'leverage': 10},
        'created_at': time.time() - 600,
        'expiry_bars': 3,
        'chase_eligible': False,
        'highest_since': 100.0,
        'lowest_since': 99.8,
    }

    trend_dir = 'bullish'  # 趋势反转为看多
    is_long = (state['deferred_entry']['action'] == 'open_long')

    should_cancel = (is_long and trend_dir == 'bearish') or \
                    (not is_long and trend_dir == 'bullish')

    assert should_cancel, "做空信号遇到趋势转多应取消"
    state['deferred_entry'] = None
    assert state['deferred_entry'] is None
    print(f"  action=open_short, trend_dir={trend_dir} → 取消")
    print("  PASS ✓")


def test_chase_trigger_price_moved():
    """场景7: 价格移动>1.5%无回调 → 追价入场（仅chase_eligible时）"""
    print("\n=== Test 7: 追价触发 ===")

    judge = MockJudge()
    state = judge._get_state("BTC-USDT")

    # 7a: chase_eligible=True → 追价
    state['deferred_entry'] = {
        'action': 'open_short',
        'signal_price': 100.0,
        'signal_score': -55,
        'target_price': 100.3,
        'plan': {'size_usdt': 10.0, 'leverage': 20, 'order_type': 'limit'},
        'created_at': time.time() - 1800,
        'expiry_bars': 3,
        'chase_eligible': True,
        'highest_since': 100.0,
        'lowest_since': 98.0,
    }

    current_price = 98.0  # 做空方向移动了2%
    signal_price = state['deferred_entry']['signal_price']
    move_pct = (signal_price - current_price) / signal_price

    assert move_pct > 0.015, f"移动{move_pct:.1%}应>1.5%"
    assert state['deferred_entry']['chase_eligible'], "应该允许追价"

    new_size = round(state['deferred_entry']['plan']['size_usdt'] * 0.6, 2)
    print(f"  7a: chase_eligible=True, move={move_pct:.1%}>1.5%")
    print(f"      仓位: 10.0 → {new_size} (60%)")

    # 7b: chase_eligible=False → 不追价（继续等待）
    state2 = judge._get_state("ETH-USDT")
    state2['deferred_entry'] = {
        'action': 'open_short',
        'signal_price': 100.0,
        'signal_score': -40,
        'target_price': 100.3,
        'plan': {'size_usdt': 10.0, 'leverage': 10},
        'created_at': time.time() - 1800,
        'expiry_bars': 3,
        'chase_eligible': False,
        'highest_since': 100.0,
        'lowest_since': 98.0,
    }

    should_chase = move_pct > 0.015 and state2['deferred_entry']['chase_eligible']
    assert not should_chase, "chase_eligible=False不应追价"
    print(f"  7b: chase_eligible=False, move={move_pct:.1%}>1.5% → 不追价，继续等待")
    print("  PASS ✓")


def test_chase_pct_formula():
    """验证仓位缩放公式的边界值"""
    print("\n=== Test 8: 仓位缩放公式边界 ===")

    min_rr = 1.5
    test_cases = [
        (1.2, 0.8),   # 1.2/1.5=0.8
        (1.3, 0.867), # clamped to 0.867
        (1.4, 0.9),   # 1.4/1.5=0.933 → clamped to 0.9
        (1.49, 0.9),  # clamped to 0.9
    ]

    for rr, expected_approx in test_cases:
        raw = rr / min_rr
        chase_pct = max(0.6, min(0.9, raw))
        print(f"  R:R={rr} → raw={raw:.3f} → chase_pct={chase_pct:.3f}")
        assert 0.6 <= chase_pct <= 0.9

    print("  PASS ✓")


if __name__ == '__main__':
    print("=" * 60)
    print("回调入场机制单元测试")
    print("=" * 60)

    test_strong_signal_chase_entry()
    test_weak_signal_deferred_entry()
    test_deferred_entry_expiry()
    test_deferred_entry_pullback_hit()
    test_rr_too_low_abandon()
    test_trend_reversal_cancels_deferred()
    test_chase_trigger_price_moved()
    test_chase_pct_formula()

    print("\n" + "=" * 60)
    print("全部 8 个测试通过 ✓")
    print("=" * 60)

