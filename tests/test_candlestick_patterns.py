"""Golden + near-miss 单测 for utils/candlestick_patterns.py(预登记固定阈值)。

每大类 1 golden(断言命中 + 方向)+ 关键 near-miss(断言不命中)。
阈值锁死;若 golden 与阈值不符,只调测试序列数值,不调阈值。
"""
from utils.candlestick_patterns import detect_patterns


def _bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


def _names(bars):
    return [n for (n, _d) in detect_patterns(bars)]


def _hit(bars, name):
    return [d for (n, d) in detect_patterns(bars) if n == name]


# ---------------- single ----------------

def test_doji_golden():
    bars = [_bar(100, 102, 98, 100.05)]
    assert _hit(bars, "Doji") == [0]


def test_doji_near_miss():
    # 大实体,body=2 > 0.1*rng=0.4 → 非 Doji
    bars = [_bar(100, 102, 98, 102)]
    assert "Doji" not in _names(bars)


def test_hammer_golden():
    # bull, body=0.5, up=h-max(o,c)=100.55-100.5=0.05 ≤ 0.3*body=0.15,
    # lo=min(o,c)-l=100-90=10 ≥ 2*body=1
    bars = [_bar(100, 100.55, 90, 100.5)]
    assert _hit(bars, "Hammer") == [1]


def test_hammer_near_miss_no_lower_shadow():
    bars = [_bar(100, 101, 99.5, 100.5)]
    assert "Hammer" not in _names(bars)


def test_shooting_star_golden():
    # 长上影:up=110-100.5=9.5 ≥ 2*body=1;lo=100-99.95=0.05 ≤ 0.3*body=0.15
    bars = [_bar(100, 110, 99.95, 100.5)]
    assert _hit(bars, "Shooting Star") == [-1]


def test_shooting_star_near_miss():
    bars = [_bar(100, 100.5, 99.5, 100.4)]
    assert "Shooting Star" not in _names(bars)


# ---------------- double ----------------

def test_bullish_engulfing_golden():
    bars = [_bar(100, 100.5, 98, 98.5), _bar(98, 101, 97.8, 100.8)]
    assert _hit(bars, "Bullish Engulfing") == [1]


def test_bullish_engulfing_near_miss_not_engulfed():
    # 今实体未吞没昨实体(今c<昨o)
    bars = [_bar(100, 100.5, 98, 98.5), _bar(98, 99.5, 97.8, 99.2)]
    assert "Bullish Engulfing" not in _names(bars)


def test_bearish_engulfing_golden():
    bars = [_bar(98, 100.5, 97.8, 100), _bar(100.5, 101, 97, 97.5)]
    assert _hit(bars, "Bearish Engulfing") == [-1]


def test_bullish_harami_golden():
    # 昨大阴(100->95),今小阳⊂昨实体,昨body=5 ≥ 1.5*今body
    bars = [_bar(100, 100.5, 94.5, 95), _bar(96, 97.5, 95.8, 97)]
    assert _hit(bars, "Bullish Harami") == [1]


def test_bullish_harami_near_miss_prev_body_too_small():
    # 昨实体不足 1.5*今实体
    bars = [_bar(100, 100.5, 98.5, 99), _bar(98.8, 99.5, 98.6, 99.4)]
    assert "Bullish Harami" not in _names(bars)


def test_bearish_harami_golden():
    bars = [_bar(95, 100.5, 94.5, 100), _bar(99, 99.2, 97.5, 98)]
    assert _hit(bars, "Bearish Harami") == [-1]


# ---------------- triple ----------------

def test_morning_star_golden():
    bars = [
        _bar(100, 100.5, 92, 92.5),   # 1 大阴
        _bar(91.5, 92, 90.5, 91),     # 2 小实体(向下跳空)
        _bar(92, 99, 91.5, 98),       # 3 大阳收回 1 实体过半
    ]
    assert _hit(bars, "Morning Star") == [1]


def test_morning_star_near_miss_no_recovery():
    # 第3根收不回第1实体中点
    bars = [
        _bar(100, 100.5, 92, 92.5),
        _bar(91.5, 92, 90.5, 91),
        _bar(91, 93, 90.5, 92.5),
    ]
    assert "Morning Star" not in _names(bars)


def test_evening_star_golden():
    bars = [
        _bar(92, 100.5, 91.5, 100),   # 1 大阳
        _bar(101, 101.5, 100.5, 101.2),  # 2 小实体(向上跳空)
        _bar(100, 100.5, 92, 93),     # 3 大阴收回 1 实体过半
    ]
    assert _hit(bars, "Evening Star") == [-1]


def test_three_white_soldiers_golden():
    bars = [
        _bar(100, 102.2, 99.8, 102),   # body=2, rng=2.4, body/rng=0.83
        _bar(101.8, 104.2, 101.7, 104),
        _bar(103.8, 106.2, 103.7, 106),
    ]
    assert _hit(bars, "Three White Soldiers") == [1]


def test_three_white_soldiers_near_miss_not_advancing():
    # 第3根未逐根收高
    bars = [
        _bar(100, 102.2, 99.8, 102),
        _bar(101.8, 104.2, 101.7, 104),
        _bar(103.8, 104.0, 102, 102.5),
    ]
    assert "Three White Soldiers" not in _names(bars)


# ---------------- five ----------------

def test_rising_three_methods_golden():
    bars = [
        _bar(100, 110, 99.5, 109),    # 1 大阳
        _bar(108, 108.5, 102, 107),   # 2 小阴(未破1 low=99.5)
        _bar(107, 107.5, 103, 105.5),
        _bar(106, 106.5, 102.5, 104),
        _bar(105, 113, 104.5, 112),   # 5 大阳收新高
    ]
    assert _hit(bars, "Rising Three Methods") == [1]


def test_rising_three_methods_near_miss_break_low():
    # 中间小阴跌破第1根 low
    bars = [
        _bar(100, 110, 99.5, 109),
        _bar(108, 108.5, 98, 99),     # 破 99.5
        _bar(99, 100, 97, 98),
        _bar(98, 99, 96, 97),
        _bar(97, 113, 96.5, 112),
    ]
    assert "Rising Three Methods" not in _names(bars)
