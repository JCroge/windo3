"""RQ-15M-02: 15m 入场时机计算单元测试"""

import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from agents.trading.tech_analyst import MultiTechAnalyst


def _make_klines_15m(closes: list, base_time=1700000000000) -> list:
    """构造 15m K 线数据 (open_time, open, high, low, close, volume)"""
    klines = []
    for i, c in enumerate(closes):
        t = base_time + i * 900_000
        o = c * 0.999
        h = c * 1.002
        l = c * 0.998
        klines.append([t, o, h, l, c, 1000.0])
    return klines


class TestEntryTiming15m:
    def setup_method(self):
        self.analyst = MultiTechAnalyst.__new__(MultiTechAnalyst)

    def test_unavailable_when_empty(self):
        result = self.analyst._analyze_entry_timing_15m([])
        assert result['tf_15m_available'] is False
        assert result['tf_15m_bias'] == 'unavailable'

    def test_unavailable_when_too_few(self):
        klines = _make_klines_15m([100.0] * 20)
        result = self.analyst._analyze_entry_timing_15m(klines)
        assert result['tf_15m_available'] is False

    def test_bearish_blocks_long(self):
        # 构造 bearish: MA fast < MA slow, RSI < 48, 3根连续下行
        closes = [100.0] * 40  # 前40根平稳（让MA slow稳定在100）
        # 后面下跌让 MA fast < MA slow
        for i in range(15):
            closes.append(100.0 - (i + 1) * 0.5)
        klines = _make_klines_15m(closes)
        result = self.analyst._analyze_entry_timing_15m(klines)
        assert result['tf_15m_available'] is True
        assert result['tf_15m_block_long'] is True
        assert result['tf_15m_ma_alignment'] == 'bearish'

    def test_bullish_blocks_short(self):
        # 构造 bullish: MA fast > MA slow, RSI > 52, 3根连续上行
        closes = [100.0] * 40
        for i in range(15):
            closes.append(100.0 + (i + 1) * 0.5)
        klines = _make_klines_15m(closes)
        result = self.analyst._analyze_entry_timing_15m(klines)
        assert result['tf_15m_available'] is True
        assert result['tf_15m_block_short'] is True
        assert result['tf_15m_ma_alignment'] == 'bullish'

    def test_bullish_confirms_long(self):
        closes = [100.0] * 40
        for i in range(15):
            closes.append(100.0 + (i + 1) * 0.5)
        klines = _make_klines_15m(closes)
        result = self.analyst._analyze_entry_timing_15m(klines)
        assert result['tf_15m_confirm_long'] is True
        assert result['tf_15m_block_long'] is False

    def test_bearish_confirms_short(self):
        closes = [100.0] * 40
        for i in range(15):
            closes.append(100.0 - (i + 1) * 0.5)
        klines = _make_klines_15m(closes)
        result = self.analyst._analyze_entry_timing_15m(klines)
        assert result['tf_15m_confirm_short'] is True
        assert result['tf_15m_block_short'] is False

    def test_neutral_when_flat(self):
        # 构造 neutral: 价格在窄幅震荡，MA fast ≈ MA slow
        closes = [100.0] * 55
        klines = _make_klines_15m(closes)
        result = self.analyst._analyze_entry_timing_15m(klines)
        assert result['tf_15m_available'] is True
        assert result['tf_15m_bias'] == 'neutral'
        assert result['tf_15m_block_long'] is False
        assert result['tf_15m_block_short'] is False

    def test_recent_closes_up(self):
        # 构造 55 根 K 线，最后 4 根: [-4]=100, [-3]=100.5, [-2]=101.0, [-1]=未闭合
        closes = [100.0] * 51
        closes.append(100.0)   # -4
        closes.append(100.5)   # -3
        closes.append(101.0)   # -2
        closes.append(101.5)   # -1 (未闭合，不参与)
        klines = _make_klines_15m(closes)
        result = self.analyst._analyze_entry_timing_15m(klines)
        assert result['tf_15m_recent_closes'] == 'up'

    def test_recent_closes_down(self):
        closes = [100.0] * 51
        closes.append(101.0)   # -4
        closes.append(100.5)   # -3
        closes.append(100.0)   # -2
        closes.append(99.5)    # -1 (未闭合)
        klines = _make_klines_15m(closes)
        result = self.analyst._analyze_entry_timing_15m(klines)
        assert result['tf_15m_recent_closes'] == 'down'

    def test_uses_closed_candles_only(self):
        """确保使用 iloc[-2] 而非 iloc[-1]（最后一根未闭合）"""
        closes = [100.0] * 50
        for i in range(5):
            closes.append(100.0 + (i + 1) * 0.5)
        # 最后一根（未闭合）突然暴跌 — 不应影响结果
        closes.append(80.0)
        klines = _make_klines_15m(closes)
        result = self.analyst._analyze_entry_timing_15m(klines)
        # 即使最后一根暴跌，已闭合的趋势仍是 bullish
        assert result['tf_15m_ma_alignment'] == 'bullish'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
