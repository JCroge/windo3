#!/usr/bin/env python3
"""ZEC事故回归测试：4h RSI 50% 衰减保护

事故复盘：
- 1h RSI=64（未触发1h硬cap >75 / <25）
- 4h RSI=73.9（超买区）
- 仍按规则入场做多 → 趋势反转亏 -135 USDT

修复（judge.py _compute_score 末尾）：
- 1h RSI 正常 + 4h RSI>=70 + score>0 → score *= 0.5
- 1h RSI 正常 + 4h RSI<=30 + score<0 → score *= 0.5
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agents.trading.judge import MultiJudge


def make_tech(rule_long=False, rule_short=False, ma_long=False, ma_short=False,
              rsi_1h=50, rsi_4h=None, htf='neutral', direction='neutral',
              strength=50, daily_bias='neutral'):
    """构造最小 tech payload。"""
    return {
        'rule_signal': {
            'entry_long': 1 if rule_long else 0,
            'entry_short': 1 if rule_short else 0,
            'ma_aligned_long': 1 if ma_long else 0,
            'ma_aligned_short': 1 if ma_short else 0,
        },
        'momentum': {'rsi': rsi_1h, 'rsi_divergence': None},
        'trend': {
            'direction': direction,
            'strength': strength,
            'higher_tf_bias': htf,
            'daily_bias': daily_bias,
            'tf_4h_rsi': rsi_4h,
        },
        'money_flow': {'oi_price_divergence': None, 'taker_pressure': 'neutral'},
        'microstructure': {'whale_direction': 'neutral'},
        'crowd': {'contrarian_signal': 'neutral'},
    }


def test_zec_scenario_4h_overbought_decay():
    """ZEC事故复刻：1h RSI=64正常 + 4h RSI=73.9超买 + rule_signal做多

    修复前：score = 35（rule_signal） → 过门槛 → 开多 → 亏损
    修复后：score = 35 × 0.5 = 17.5 → 未过任何门槛
    """
    judge = MultiJudge()
    tech = make_tech(rule_long=True, rsi_1h=64, rsi_4h=73.9)
    score = judge._compute_score(tech)
    assert score == 35 * 0.5, f"Expected 17.5 (35*0.5), got {score}"
    print(f"  OK ZEC scenario: rule_long+rsi_4h=73.9 → score={score} (从35衰减到17.5)")


def test_4h_oversold_short_decay():
    """对称场景：4h RSI=27超卖 + rule_signal做空 → 做空 score 衰减"""
    judge = MultiJudge()
    tech = make_tech(rule_short=True, rsi_1h=40, rsi_4h=27)
    score = judge._compute_score(tech)
    assert score == -35 * 0.5, f"Expected -17.5, got {score}"
    print(f"  OK 对称做空: rule_short+rsi_4h=27 → score={score} (从-35衰减到-17.5)")


def test_4h_normal_no_decay():
    """4h RSI 正常（50）→ 不衰减"""
    judge = MultiJudge()
    tech = make_tech(rule_long=True, rsi_1h=55, rsi_4h=50)
    score = judge._compute_score(tech)
    assert score == 35, f"Expected 35 (no decay), got {score}"
    print(f"  OK 4h RSI正常: score={score} (无衰减)")


def test_4h_overbought_short_not_decayed():
    """4h RSI=72 超买 + rule_signal做空 → 做空方向不受影响（4h超买保护的是做多）"""
    judge = MultiJudge()
    tech = make_tech(rule_short=True, rsi_1h=50, rsi_4h=72)
    score = judge._compute_score(tech)
    # rule_short = -35, 4h RSI>=70 只衰减 score>0 的部分
    assert score == -35, f"Expected -35 (短方向不衰减), got {score}"
    print(f"  OK 做空不受4h超买影响: score={score}")


def test_4h_missing_no_crash():
    """tf_4h_rsi 缺失（None）时不应崩溃"""
    judge = MultiJudge()
    tech = make_tech(rule_long=True, rsi_1h=50, rsi_4h=None)
    score = judge._compute_score(tech)
    assert score == 35, f"Expected 35, got {score}"
    print(f"  OK 4h RSI 缺失: score={score} (兼容旧数据)")


def test_4h_decay_after_existing_1h_cap():
    """复合场景：1h RSI=76 触发1h硬cap=15 + 4h RSI=72 → 在cap之后再衰减

    顺序：先1h硬cap到+15，再4h衰减 → 7.5（满足设计意图：双层保护叠加）
    """
    judge = MultiJudge()
    # 强多分场景：rule_long + trend bullish strong + htf bullish
    tech = make_tech(rule_long=True, rsi_1h=76, rsi_4h=72, htf='bullish',
                     direction='bullish', strength=80)
    score = judge._compute_score(tech)
    # 1h RSI=76 (extreme_bearish, >75) + htf=bullish → cap=25
    # 然后 4h RSI=72 → score *= 0.5
    # 但 rule_signal+35 + trend score 可能超过25 cap后再衰减
    # 实际：cap后score≤25，再衰减≤12.5
    assert score <= 25 * 0.5 + 0.01, f"Expected score<=12.5, got {score}"
    print(f"  OK 1h cap + 4h衰减叠加: score={score} (两层防护生效)")


def test_4h_overbought_blocks_high_score_long():
    """关键安全测试：score=70 的强多信号 + 4h RSI=73 → 衰减到35（仍可能入场但仓位减半）

    设计意图：强趋势中4h超买不完全禁止，但显著降低置信度
    """
    judge = MultiJudge()
    # 各种正向加分叠加
    tech = make_tech(rule_long=True, rsi_1h=60, rsi_4h=73,
                     direction='bullish', strength=85, htf='bullish')
    tech['money_flow']['oi_price_divergence'] = 'bullish'
    tech['microstructure']['whale_direction'] = 'accumulating'
    score = judge._compute_score(tech)
    # 原始: 35 + trend(17~20) + oi(12) + whale(15) + htf(10) ≈ 89~92
    # 衰减后: ≈ 44~46
    assert 30 < score < 60, f"Expected 30-60 after decay, got {score}"
    print(f"  OK 强多信号+4h超买: score={score} (衰减约50%)")


if __name__ == '__main__':
    print("=== ZEC事故回归测试: 4h RSI 50%衰减保护 ===\n")
    tests = [
        test_zec_scenario_4h_overbought_decay,
        test_4h_oversold_short_decay,
        test_4h_normal_no_decay,
        test_4h_overbought_short_not_decayed,
        test_4h_missing_no_crash,
        test_4h_decay_after_existing_1h_cap,
        test_4h_overbought_blocks_high_score_long,
    ]
    passed = 0
    failed = []
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            failed.append((t.__name__, str(e)))
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n=== 结果: {passed}/{len(tests)} 通过 ===")
    if failed:
        for name, err in failed:
            print(f"  失败: {name} - {err}")
        sys.exit(1)
