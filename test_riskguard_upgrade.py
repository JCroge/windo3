"""RiskGuard 升级验证 - 6维度风控 + trailing stop"""

import asyncio
import sys
import time
sys.path.insert(0, '.')

from agents.message_bus import MessageBus
from agents.trading.portfolio_risk_guard import PortfolioRiskGuard


async def test_position_danger():
    """测试1: 单仓浮亏超限 → position_danger"""
    print("=" * 60)
    print("测试1: 单仓浮亏 > 15% → position_danger")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    config = {"exchange": "okx", "max_trade_amount": 10}
    rg = PortfolioRiskGuard(config)

    bus.register("test_listener", ["risk_alert"])

    rg._positions = {
        "SOL-USDT": {
            "symbol": "SOL-USDT", "side": "long",
            "entry_price": 170.0, "amount_usdt": 10.0,
            "leverage": 5, "stop_loss": 165.0, "take_profit": 180.0,
            "open_time": time.time(), "highest_price": 172.0, "lowest_price": 170.0,
        }
    }
    # 5x杠杆，价格跌4% → 浮亏20% > 15%
    rg._prices = {"SOL-USDT": 163.2}

    await rg._check_position_pnl("SOL-USDT", 163.2)

    msg = await bus.receive("test_listener", timeout=1.0)
    assert msg is not None, "Should emit position_danger"
    assert msg['payload']['type'] == 'position_danger'
    assert msg['payload']['symbol'] == 'SOL-USDT'
    print(f"  ✓ position_danger 触发, pnl={msg['payload']['pnl_pct']:.1f}%")

    print("\n✅ 测试1通过\n")
    return True


async def test_portfolio_drawdown():
    """测试2: 组合回撤 > 10% → max_drawdown"""
    print("=" * 60)
    print("测试2: 组合回撤 > 10% → max_drawdown")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    config = {"exchange": "okx", "max_trade_amount": 10}
    rg = PortfolioRiskGuard(config)
    rg._account_balance = 100.0

    bus.register("test_listener", ["risk_alert"])

    rg._positions = {
        "SOL-USDT": {
            "symbol": "SOL-USDT", "side": "long",
            "entry_price": 170.0, "amount_usdt": 10.0,
            "leverage": 3, "stop_loss": 165.0, "take_profit": 180.0,
            "open_time": time.time(), "highest_price": 170.0, "lowest_price": 170.0,
        },
        "WIF-USDT": {
            "symbol": "WIF-USDT", "side": "long",
            "entry_price": 2.5, "amount_usdt": 10.0,
            "leverage": 5, "stop_loss": 2.4, "take_profit": 2.7,
            "open_time": time.time(), "highest_price": 2.5, "lowest_price": 2.5,
        }
    }
    # SOL: 3x, -3% → -9% of 10 = -0.9 USDT
    # WIF: 5x, -5% → -25% of 10 = -2.5 USDT
    # Total: -3.4 USDT, drawdown = 3.4/100 = 3.4% (not enough)
    # Need bigger loss: WIF -4% with 5x = -20% → -2.0; SOL -10% with 3x = -30% → -3.0
    # Total = -5.0, drawdown = 5% (still not enough for 10%)
    # Let's make it bigger: both -5% raw with high leverage
    rg._prices = {"SOL-USDT": 158.1, "WIF-USDT": 2.25}
    # SOL: (158.1-170)/170 * 3 * 100 = -21% of 10 = -2.1
    # WIF: (2.25-2.5)/2.5 * 5 * 100 = -50% of 10 = -5.0
    # Total: -7.1 USDT, drawdown = 7.1% (still not 10%)
    # Need: total_pnl < -10 USDT for 10% of 100
    rg._prices = {"SOL-USDT": 155.0, "WIF-USDT": 2.15}
    # SOL: (155-170)/170 * 3 = -26.5% → -2.65
    # WIF: (2.15-2.5)/2.5 * 5 = -70% → -7.0
    # Total: -9.65 → 9.65% (close but not over)
    rg._prices = {"SOL-USDT": 153.0, "WIF-USDT": 2.1}
    # SOL: (153-170)/170 * 3 = -30% → -3.0
    # WIF: (2.1-2.5)/2.5 * 5 = -80% → -8.0
    # Total: -11.0 → 11% > 10% ✓

    await rg._check_portfolio_drawdown()

    msg = await bus.receive("test_listener", timeout=1.0)
    assert msg is not None, "Should emit max_drawdown"
    assert msg['payload']['type'] == 'max_drawdown'
    print(f"  ✓ max_drawdown 触发, drawdown={msg['payload']['drawdown_pct']:.1f}%")

    print("\n✅ 测试2通过\n")
    return True


async def test_flash_move():
    """测试3: 60秒内价格变化>3% → flash_move"""
    print("=" * 60)
    print("测试3: 闪崩检测 (60s内-5%)")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    config = {"exchange": "okx", "max_trade_amount": 10}
    rg = PortfolioRiskGuard(config)

    bus.register("test_listener", ["risk_alert"])

    now = time.time()
    rg._price_history["BTC-USDT"] = [
        (now - 50, 100000.0),
        (now - 40, 99500.0),
        (now - 30, 98000.0),
        (now - 20, 97000.0),
        (now - 10, 96500.0),
        (now, 94500.0),  # -5.5% from first
    ]

    await rg._check_flash_move("BTC-USDT")

    msg = await bus.receive("test_listener", timeout=1.0)
    assert msg is not None, "Should emit flash_move"
    assert msg['payload']['type'] == 'flash_move'
    assert msg['payload']['direction'] == '暴跌'
    print(f"  ✓ flash_move 触发: {msg['payload']['direction']} {msg['payload']['magnitude_pct']:.1f}%")

    print("\n✅ 测试3通过\n")
    return True


async def test_high_leverage_danger():
    """测试4: 高杠杆+浮亏 → high_leverage_danger"""
    print("=" * 60)
    print("测试4: 杠杆>10x + 浮亏>5% → high_leverage_danger")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    config = {"exchange": "okx", "max_trade_amount": 10}
    rg = PortfolioRiskGuard(config)

    bus.register("test_listener", ["risk_alert"])

    rg._positions = {
        "DOGE-USDT": {
            "symbol": "DOGE-USDT", "side": "short",
            "entry_price": 0.15, "amount_usdt": 8.0,
            "leverage": 15, "stop_loss": 0.155, "take_profit": 0.14,
            "open_time": time.time(), "highest_price": 0.15, "lowest_price": 0.15,
        }
    }
    # 15x short, price goes up 0.5% raw → 7.5% leveraged loss
    # raw_loss = 7.5/15 = 0.5% ... wait, threshold is raw_loss > 5%
    # Need: raw loss > 5%, so price up 5% → 0.1575
    rg._prices = {"DOGE-USDT": 0.1575}

    await rg._check_high_leverage("DOGE-USDT", 0.1575)

    msg = await bus.receive("test_listener", timeout=1.0)
    assert msg is not None, "Should emit high_leverage_danger"
    assert msg['payload']['type'] == 'high_leverage_danger'
    assert msg['payload']['leverage'] == 15
    print(f"  ✓ high_leverage_danger: lev={msg['payload']['leverage']}x, pnl={msg['payload']['pnl_pct']:.1f}%")

    print("\n✅ 测试4通过\n")
    return True


async def test_trailing_stop():
    """测试5: 盈利后回撤 → trailing_stop"""
    print("=" * 60)
    print("测试5: trailing stop (盈利5%后回撤>50%)")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    config = {"exchange": "okx", "max_trade_amount": 10}
    rg = PortfolioRiskGuard(config)

    bus.register("test_listener", ["risk_alert"])

    # Long entry=100, peak=106 (6% profit), now=102.5
    # profit_pct from entry = 2.5% (current) — but peak was 6%
    # retrace_from_peak = (106-102.5)/(106-100) = 3.5/6 = 58.3%
    # At 5% profit level, trail_pct = 50%, so 58.3% > 50% → trigger
    rg._positions = {
        "ETH-USDT": {
            "symbol": "ETH-USDT", "side": "long",
            "entry_price": 100.0, "amount_usdt": 10.0,
            "leverage": 1, "stop_loss": 95.0, "take_profit": 110.0,
            "open_time": time.time(), "highest_price": 106.0, "lowest_price": 100.0,
        }
    }
    # Current price: still above entry but retraced from peak
    # profit_pct = (102.5-100)/100 = 2.5% — wait, need profit >= 3% for trailing to activate
    # Let's use peak=108 (8% profit), current=103
    # profit_pct = 3% (current), retrace = (108-103)/(108-100) = 5/8 = 62.5%
    # At profit 3-5%, trail_pct=100% (only triggers if retrace > 100%, i.e. below entry)
    # Hmm, need profit >= 5% for trail_pct=50%
    # peak=110 (10%), current=104 → profit=4%, retrace=(110-104)/(110-100)=60%
    # profit=4% → trail_pct=100% (between 3-5%), 60% < 100% → no trigger
    # Need profit >= 5%: peak=110, current=107 → profit=7%, retrace=(110-107)/(110-100)=30%
    # profit=7% → trail_pct=50%, 30% < 50% → no trigger
    # peak=110, current=104.5 → profit=4.5%... still < 5
    # Let me reconsider: profit_pct is calculated from CURRENT price, not peak
    # profit_pct = (current - entry) / entry * 100
    # For trail_pct=50%: need profit_pct >= 5%
    # entry=100, current=105.5 → profit=5.5%, peak=110
    # retrace = (110-105.5)/(110-100) = 4.5/10 = 45% < 50% → no trigger
    # entry=100, current=104.5 → profit=4.5% < 5% → trail_pct=100%
    # entry=100, current=105 → profit=5%, peak=112
    # retrace = (112-105)/(112-100) = 7/12 = 58.3% > 50% → TRIGGER!

    rg._positions["ETH-USDT"]["entry_price"] = 100.0
    rg._positions["ETH-USDT"]["highest_price"] = 112.0

    await rg._check_trailing_stop("ETH-USDT", 105.0)

    msg = await bus.receive("test_listener", timeout=1.0)
    assert msg is not None, "Should emit trailing_stop"
    assert msg['payload']['type'] == 'trailing_stop'
    assert msg['payload']['symbol'] == 'ETH-USDT'
    print(f"  ✓ trailing_stop: profit={msg['payload']['profit_pct']:.1f}%, retrace={msg['payload']['retrace_pct']:.0f}%")

    print("\n✅ 测试5通过\n")
    return True


async def test_correlation_risk():
    """测试6: 同方向敞口过大 → correlation_risk"""
    print("=" * 60)
    print("测试6: 同方向敞口 > 20 USDT → correlation_risk")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    config = {"exchange": "okx", "max_trade_amount": 10}
    rg = PortfolioRiskGuard(config)

    bus.register("test_listener", ["risk_alert"])

    rg._positions = {
        "SOL-USDT": {"symbol": "SOL-USDT", "side": "long", "amount_usdt": 10.0,
                     "entry_price": 170, "leverage": 3, "open_time": time.time(),
                     "highest_price": 170, "lowest_price": 170},
        "WIF-USDT": {"symbol": "WIF-USDT", "side": "long", "amount_usdt": 8.0,
                     "entry_price": 2.5, "leverage": 5, "open_time": time.time(),
                     "highest_price": 2.5, "lowest_price": 2.5},
        "DOGE-USDT": {"symbol": "DOGE-USDT", "side": "long", "amount_usdt": 6.0,
                      "entry_price": 0.15, "leverage": 3, "open_time": time.time(),
                      "highest_price": 0.15, "lowest_price": 0.15},
    }
    # Total long exposure: 10+8+6 = 24 > 20

    await rg._check_correlation_risk()

    msg = await bus.receive("test_listener", timeout=1.0)
    assert msg is not None, "Should emit correlation_risk"
    assert msg['payload']['type'] == 'correlation_risk'
    assert msg['payload']['direction'] == '多'
    print(f"  ✓ correlation_risk: 方向={msg['payload']['direction']}, 敞口={msg['payload']['exposure_usdt']}")

    print("\n✅ 测试6通过\n")
    return True


async def test_no_false_alarm():
    """测试7: 正常情况不误报"""
    print("=" * 60)
    print("测试7: 正常持仓不误报")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    config = {"exchange": "okx", "max_trade_amount": 10}
    rg = PortfolioRiskGuard(config)
    rg._account_balance = 100.0

    bus.register("test_listener", ["risk_alert"])

    rg._positions = {
        "SOL-USDT": {
            "symbol": "SOL-USDT", "side": "long",
            "entry_price": 170.0, "amount_usdt": 8.0,
            "leverage": 3, "stop_loss": 165.0, "take_profit": 180.0,
            "open_time": time.time(), "highest_price": 172.0, "lowest_price": 170.0,
        }
    }
    # 小幅盈利: (171-170)/170 * 3 = 1.76%
    rg._prices = {"SOL-USDT": 171.0}

    now = time.time()
    rg._price_history["SOL-USDT"] = [
        (now - 30, 170.5),
        (now - 20, 170.8),
        (now - 10, 171.0),
    ]

    await rg._check_position_pnl("SOL-USDT", 171.0)
    await rg._check_portfolio_drawdown()
    await rg._check_flash_move("SOL-USDT")
    await rg._check_high_leverage("SOL-USDT", 171.0)
    await rg._check_trailing_stop("SOL-USDT", 171.0)
    await rg._check_correlation_risk()

    msg = await bus.receive("test_listener", timeout=0.5)
    assert msg is None, f"Should NOT emit any alert, got: {msg}"
    print("  ✓ 正常持仓（小幅盈利）无告警")

    print("\n✅ 测试7通过\n")
    return True


async def test_e2e_riskguard_to_executor():
    """测试8: 端到端 RiskGuard → Executor 平仓"""
    print("=" * 60)
    print("测试8: 端到端 trailing_stop → Executor平仓")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    from unittest.mock import MagicMock
    from agents.trading.executor import MultiExecutor

    config = {"exchange": "okx", "leverage": 3, "max_trade_amount": 10}
    rg = PortfolioRiskGuard(config)
    executor_agent = MultiExecutor(config)

    mock_exec = MagicMock()
    mock_exec.get_position.return_value = {"symbol": "ETH-USDT", "side": "long"}
    mock_exec.get_all_positions.return_value = {
        "ETH-USDT": {"symbol": "ETH-USDT", "side": "long", "amount_usdt": 10, "sl_order_id": "sl_1"}
    }
    mock_exec.positions = {"ETH-USDT": {"sl_order_id": "sl_1"}}
    mock_exec.close_position.return_value = {"symbol": "ETH-USDT", "pnl": 3.5}
    mock_exec.cancel_order.return_value = True
    mock_exec.exchange = MagicMock()
    mock_exec.exchange.fetch_balance.return_value = {"total": {"USDT": 100}}
    mock_exec.risk_manager = MagicMock()
    executor_agent.executor = mock_exec

    # RiskGuard发出trailing_stop
    rg._positions = {
        "ETH-USDT": {
            "symbol": "ETH-USDT", "side": "long",
            "entry_price": 3000.0, "amount_usdt": 10.0,
            "leverage": 1, "stop_loss": 2900.0, "take_profit": 3300.0,
            "open_time": time.time(), "highest_price": 3200.0, "lowest_price": 3000.0,
        }
    }
    # profit = (3150-3000)/3000 = 5%, peak=3200
    # retrace = (3200-3150)/(3200-3000) = 50/200 = 25% < 50% → no trigger
    # profit = (3100-3000)/3000 = 3.33%, peak=3200
    # retrace = (3200-3100)/(3200-3000) = 100/200 = 50%
    # profit 3.33% → trail_pct=100%, 50% < 100% → no trigger
    # Need profit >= 5%: current=3150, peak=3200
    # profit=5%, retrace=(3200-3150)/(3200-3000)=25% < 50% → no
    # current=3160, peak=3300 → profit=5.33%, retrace=(3300-3160)/(3300-3000)=140/300=46.7% < 50%
    # current=3155, peak=3300 → profit=5.17%, retrace=(3300-3155)/(3300-3000)=145/300=48.3% < 50%
    # current=3145, peak=3300 → profit=4.83% < 5% → trail_pct=100%
    # Hmm, let me use: entry=3000, peak=3300 (10% profit at peak), current=3180
    # profit = (3180-3000)/3000 = 6%, retrace = (3300-3180)/(3300-3000) = 120/300 = 40% < 50%
    # current=3140 → profit=4.67% < 5% → trail_pct=100%
    # Let me just use entry=3000, peak=3200, current=3050
    # profit=1.67% < 3% → no trailing at all
    # OK let me use: entry=100, peak=112, current=105 (same as test5)
    rg._positions["ETH-USDT"]["entry_price"] = 100.0
    rg._positions["ETH-USDT"]["highest_price"] = 112.0

    await rg._check_trailing_stop("ETH-USDT", 105.0)

    # Executor收到alert
    msg = await bus.receive("executor", timeout=1.0)
    assert msg is not None, "Executor should receive trailing_stop"
    assert msg['payload']['type'] == 'trailing_stop'

    await executor_agent.on_message(msg)

    mock_exec.close_position.assert_called_once_with("ETH-USDT")
    mock_exec.cancel_order.assert_called_once_with("ETH-USDT", "sl_1")
    print("  ✓ RiskGuard trailing_stop → Executor 平仓 ETH-USDT")
    print("  ✓ 止损条件单被撤销")

    print("\n✅ 测试8通过\n")
    return True


async def main():
    results = []
    results.append(await test_position_danger())
    results.append(await test_portfolio_drawdown())
    results.append(await test_flash_move())
    results.append(await test_high_leverage_danger())
    results.append(await test_trailing_stop())
    results.append(await test_correlation_risk())
    results.append(await test_no_false_alarm())
    results.append(await test_e2e_riskguard_to_executor())

    print("=" * 60)
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"RiskGuard升级验证: {passed}/{total} 测试通过")
    print("=" * 60)
    return all(results)


if __name__ == '__main__':
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
