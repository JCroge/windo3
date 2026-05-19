"""P1-M: OKX 订单能力检测 + 幂等对账 单元测试

主要验证：
1. OrderCapabilities 缓存 market 元数据，命中缓存不重复调用
2. OrderCapabilities.precheck_order 在各种边界返回正确结果
3. IdempotencyGuard.gen_client_order_id 同窗口稳定
4. IdempotencyGuard.is_duplicate 窗口内拦截、窗口外放行
"""
import sys
import time
sys.path.insert(0, '.')


class MockExchange:
    """模拟 ccxt exchange，用于测试 OrderCapabilities。"""

    def __init__(self):
        self.market_call_count = 0
        self.load_markets_called = 0
        self._markets = {
            'BTC-USDT-SWAP': {
                'base': 'BTC',
                'quote': 'USDT',
                'type': 'swap',
                'contractSize': 0.01,  # OKX BTC 永续合约面值 0.01 BTC
                'limits': {
                    'amount': {'min': 0.1, 'max': 10000},
                    'cost': {'min': 1.0},  # 最小 1 USDT 名义价值
                    'price': {'min': 0.1},
                },
                'precision': {'amount': 0.1, 'price': 0.1},
            },
            'DOGE-USDT-SWAP': {
                'base': 'DOGE',
                'quote': 'USDT',
                'type': 'swap',
                'contractSize': 1000,  # DOGE 合约面值 1000 DOGE
                'limits': {
                    'amount': {'min': 1, 'max': 100000},
                    'cost': {'min': 5.0},
                    'price': {'min': 0.00001},
                },
                'precision': {'amount': 1, 'price': 0.00001},
            },
        }

    def load_markets(self):
        self.load_markets_called += 1
        return self._markets

    def market(self, symbol):
        self.market_call_count += 1
        if symbol not in self._markets:
            raise ValueError(f"Unknown market: {symbol}")
        return self._markets[symbol]

    def amount_to_precision(self, symbol, amount):
        # 简化版精度——按整数位截断
        return float(int(amount))


def test_capabilities_warmup():
    """warmup 调用一次 load_markets，幂等调用不重复。"""
    from utils.order_capabilities import OrderCapabilities
    mock = MockExchange()
    caps = OrderCapabilities(mock)
    assert caps.warmup() is True
    assert mock.load_markets_called == 1
    caps.warmup()  # 幂等
    assert mock.load_markets_called == 1
    print("  ✅ Case 1: warmup 幂等")


def test_capabilities_cache_hit():
    """同 symbol 第二次 get 不打 market API。"""
    from utils.order_capabilities import OrderCapabilities
    mock = MockExchange()
    caps = OrderCapabilities(mock)
    caps.get('BTC-USDT-SWAP')
    caps.get('BTC-USDT-SWAP')
    caps.get('BTC-USDT-SWAP')
    assert mock.market_call_count == 1, \
        f"应命中缓存只调用1次 market，实际 {mock.market_call_count}"
    print("  ✅ Case 2: 缓存命中，市场元数据只取一次")


def test_capabilities_returns_keys():
    """get 返回完整 caps dict。"""
    from utils.order_capabilities import OrderCapabilities
    mock = MockExchange()
    caps = OrderCapabilities(mock)
    btc = caps.get('BTC-USDT-SWAP')
    assert btc['contract_size'] == 0.01
    assert btc['min_amount'] == 0.1
    assert btc['min_cost'] == 1.0
    assert btc['base'] == 'BTC'
    assert btc['type'] == 'swap'
    print(f"  ✅ Case 3: caps 字段完整 (contract_size={btc['contract_size']})")


def test_precheck_pass_normal():
    """正常订单通过预检并返回 amount/cost。"""
    from utils.order_capabilities import OrderCapabilities
    mock = MockExchange()
    caps = OrderCapabilities(mock)
    # DOGE: contract_size=1000, price=0.1, size_usdt=10, leverage=3
    # notional=30, amount = 30 / (0.1 * 1000) = 0.3 → 精度截断→ 0
    # 实际：用 BTC 案例
    # BTC: contract_size=0.01, price=50000, size_usdt=10, leverage=3
    # notional=30, amount = 30 / (50000 * 0.01) = 0.06 → 精度截0
    # 改用 size_usdt=100 让通过
    # notional=300, amount=300/(50000*0.01)=0.6 → 截 0 < min_amount 0.1 → 失败
    # 改用 size_usdt=2000, amount=2000*3/(50000*0.01) = 12 → 通过
    ok, reason, norm = caps.precheck_order(
        'BTC-USDT-SWAP', 'long', size_usdt=2000, price=50000, leverage=3
    )
    assert ok, f"应通过预检，实际 reason={reason}"
    assert 'amount' in norm
    assert 'cost' in norm
    print(f"  ✅ Case 4: 正常订单通过预检 amount={norm['amount']} cost={norm['cost']:.2f}")


def test_precheck_reject_amount_too_small():
    """amount 低于 min_amount → 拒绝。"""
    from utils.order_capabilities import OrderCapabilities
    mock = MockExchange()
    caps = OrderCapabilities(mock)
    # BTC: size_usdt=10, leverage=1, price=50000
    # amount = 10/(50000*0.01) = 0.02 < min 0.1 → 拒绝
    ok, reason, norm = caps.precheck_order(
        'BTC-USDT-SWAP', 'long', size_usdt=10, price=50000, leverage=1
    )
    assert not ok
    assert 'amount_too_small' in reason
    print(f"  ✅ Case 5: amount<min 被拒 ({reason})")


def test_precheck_reject_bad_price():
    """price<=0 → 直接拒。"""
    from utils.order_capabilities import OrderCapabilities
    mock = MockExchange()
    caps = OrderCapabilities(mock)
    ok, reason, _ = caps.precheck_order('BTC-USDT-SWAP', 'long', 100, 0, 1)
    assert not ok
    assert reason == 'bad_price'
    print(f"  ✅ Case 6: price<=0 直接拒")


def test_precheck_no_market_meta():
    """未知 symbol → no_market_meta。"""
    from utils.order_capabilities import OrderCapabilities
    mock = MockExchange()
    caps = OrderCapabilities(mock)
    ok, reason, _ = caps.precheck_order('UNKNOWN-USDT-SWAP', 'long', 100, 1, 1)
    assert not ok
    assert reason == 'no_market_meta'
    print(f"  ✅ Case 7: 未知 symbol → no_market_meta")


def test_idempotency_gen_stable_in_window():
    """同窗口内同 (symbol, side) 生成相同 clord。"""
    from utils.order_capabilities import IdempotencyGuard
    guard = IdempotencyGuard(window_sec=10)
    a = guard.gen_client_order_id('BTC-USDT-SWAP', 'long')
    b = guard.gen_client_order_id('BTC-USDT-SWAP', 'long')
    assert a == b, f"同窗口应稳定，{a} != {b}"
    assert len(a) <= 32 and a.startswith('a')
    print(f"  ✅ Case 8: clord 同窗口稳定 ({a})")


def test_idempotency_gen_differs_by_side():
    """同 symbol 不同方向 clord 不同。"""
    from utils.order_capabilities import IdempotencyGuard
    guard = IdempotencyGuard(window_sec=10)
    long_id = guard.gen_client_order_id('BTC-USDT-SWAP', 'long')
    short_id = guard.gen_client_order_id('BTC-USDT-SWAP', 'short')
    assert long_id != short_id
    print(f"  ✅ Case 9: long/short 方向不同 clord 不同")


def test_idempotency_is_duplicate():
    """mark 后窗口内 is_duplicate 返回 True。"""
    from utils.order_capabilities import IdempotencyGuard
    guard = IdempotencyGuard(window_sec=10)
    is_dup, _ = guard.is_duplicate('BTC-USDT-SWAP', 'long')
    assert is_dup is False, "未 mark 时不应判重"
    cid = guard.gen_client_order_id('BTC-USDT-SWAP', 'long')
    guard.mark('BTC-USDT-SWAP', 'long', cid)
    is_dup, prior = guard.is_duplicate('BTC-USDT-SWAP', 'long')
    assert is_dup is True and prior == cid
    print(f"  ✅ Case 10: mark 后窗口内 is_duplicate 返回 True 并带 prior clord")


def test_idempotency_window_expires():
    """超过 window 后 is_duplicate 返回 False。"""
    from utils.order_capabilities import IdempotencyGuard
    guard = IdempotencyGuard(window_sec=1)
    guard.mark('BTC-USDT-SWAP', 'long', 'aaa')
    time.sleep(1.1)
    is_dup, _ = guard.is_duplicate('BTC-USDT-SWAP', 'long')
    assert is_dup is False, "窗口过期应不判重"
    print(f"  ✅ Case 11: 窗口过期后 is_duplicate=False")


def test_idempotency_clear_releases():
    """clear 显式释放（用于平仓后立即允许反向开。"""
    from utils.order_capabilities import IdempotencyGuard
    guard = IdempotencyGuard(window_sec=300)
    guard.mark('BTC-USDT-SWAP', 'long', 'aaa')
    guard.clear('BTC-USDT-SWAP', 'long')
    is_dup, _ = guard.is_duplicate('BTC-USDT-SWAP', 'long')
    assert is_dup is False
    print(f"  ✅ Case 12: clear 显式释放窗口锁")


def main():
    print("=" * 60)
    print("P1-M: 订单能力检测 + 幂等对账 测试")
    print("=" * 60)
    test_capabilities_warmup()
    test_capabilities_cache_hit()
    test_capabilities_returns_keys()
    test_precheck_pass_normal()
    test_precheck_reject_amount_too_small()
    test_precheck_reject_bad_price()
    test_precheck_no_market_meta()
    test_idempotency_gen_stable_in_window()
    test_idempotency_gen_differs_by_side()
    test_idempotency_is_duplicate()
    test_idempotency_window_expires()
    test_idempotency_clear_releases()
    print("\n" + "=" * 60)
    print("✅ 全部 12 个测试通过")
    print("=" * 60)


if __name__ == '__main__':
    main()
