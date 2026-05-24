"""AC-P1-006/007/008: OKX SWAP contractSize notional 换算测试"""
import pytest
from unittest.mock import MagicMock, patch


class TestContractSizeNotional:
    """验证 orderbook depth / liquidation vol / slippage 使用 contractSize"""

    def test_orderbook_depth_with_btc_contract_size(self):
        """AC-P1-006: BTC contractSize=0.01"""
        from agents.trading.multi_data_collector import MultiDataCollector
        dc = MultiDataCollector.__new__(MultiDataCollector)
        dc.logger = MagicMock()
        dc.exchange = MagicMock()
        dc.exchange.markets = {
            'BTC/USDT:USDT': {'contractSize': 0.01}
        }
        dc._orderbook_cache = {}

        ct_size = dc._get_contract_size('BTC-USDT')
        assert ct_size == 0.01

        # Manual calculation: price=67000, qty=100 contracts, ct_size=0.01
        # notional = 67000 * 100 * 0.01 = 67000 USD
        bids = [[67000, 100]]
        depth = sum(b[0] * b[1] * ct_size for b in bids)
        assert depth == 67000.0

    def test_orderbook_depth_with_doge_contract_size(self):
        """AC-P1-006: DOGE contractSize=1000"""
        from agents.trading.multi_data_collector import MultiDataCollector
        dc = MultiDataCollector.__new__(MultiDataCollector)
        dc.logger = MagicMock()
        dc.exchange = MagicMock()
        dc.exchange.markets = {
            'DOGE/USDT:USDT': {'contractSize': 1000}
        }
        dc._orderbook_cache = {}

        ct_size = dc._get_contract_size('DOGE-USDT')
        assert ct_size == 1000

        # Manual: price=0.15, qty=5 contracts, ct_size=1000
        # notional = 0.15 * 5 * 1000 = 750 USD
        asks = [[0.15, 5]]
        depth = sum(a[0] * a[1] * ct_size for a in asks)
        assert depth == 750.0

    def test_liquidation_vol_with_contract_size(self):
        """AC-P1-007: liquidation vol 使用 contractSize"""
        from agents.trading.multi_data_collector import MultiDataCollector
        dc = MultiDataCollector.__new__(MultiDataCollector)
        dc.logger = MagicMock()
        dc.exchange = MagicMock()
        dc.exchange.markets = {
            'BTC/USDT:USDT': {'contractSize': 0.01}
        }

        ct_size = dc._get_contract_size('BTC-USDT')
        # sz=500 contracts liquidated at price=65000, ct_size=0.01
        # vol_usd = 500 * 65000 * 0.01 = 325000 USD
        vol_usd = 500 * 65000 * ct_size
        assert vol_usd == 325000.0

    def test_slippage_depth_with_contract_size(self):
        """AC-P1-008: slippage depth 使用 contractSize"""
        from executor import ContractExecutor
        ex = ContractExecutor.__new__(ContractExecutor)
        ex.logger = MagicMock()
        ex.exchange = MagicMock()
        ex.exchange.markets = {
            'ETH/USDT:USDT': {'contractSize': 1}
        }
        ex.exchange.fetch_order_book = MagicMock(return_value={
            'asks': [[3500, 10], [3501, 20], [3502, 30], [3503, 40], [3504, 50]],
            'bids': [[3499, 10], [3498, 20]],
        })

        ct_size = ex._get_contract_size('ETH/USDT:USDT')
        assert ct_size == 1.0

        # With ct_size=1, depth = sum(p*q*1) for asks[:5]
        result = ex._check_slippage('ETH/USDT:USDT', 100, 3500)
        assert result is True

    def test_contract_size_missing_defaults_to_1(self):
        """contractSize 缺失时默认为 1"""
        from agents.trading.multi_data_collector import MultiDataCollector
        dc = MultiDataCollector.__new__(MultiDataCollector)
        dc.logger = MagicMock()
        dc.exchange = MagicMock()
        dc.exchange.markets = {}

        ct_size = dc._get_contract_size('UNKNOWN-USDT')
        assert ct_size == 1.0
