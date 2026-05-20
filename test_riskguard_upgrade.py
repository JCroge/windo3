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
    """测试2: 组合回撤 > 15% → max_drawdown

    字段语义校验：amount_usdt = 保证金（margin），_calc_pnl_pct 已含 leverage。
    总盈亏 = sum(margin × pnl_pct_with_leverage / 100)
    """
    print("=" * 60)
    print("测试2: 组合回撤 > 15% → max_drawdown")
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
    # 期望：total_pnl < -15 USDT → drawdown > 15% → 触发熔断
    # SOL @ 140: (140-170)/170 × 3 × 100 = -52.9% → 10 × -0.529 = -5.29
    # WIF @ 1.9: (1.9-2.5)/2.5 × 5 × 100 = -120% → 10 × -1.20 = -12.0
    # Total ≈ -17.29 → drawdown = 17.29% > 15% ✓
    rg._prices = {"SOL-USDT": 140.0, "WIF-USDT": 1.9}

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
    # 生产阈值 20x（OKX 上限），测试用 10x 验证检查逻辑
    rg._high_leverage_threshold = 10

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
    # 生产阈值 2400 USDT（适合中等规模账户），测试用 20 验证检查逻辑
    rg._correlation_exposure_limit = 20.0

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


async def test_persist_after_external_close():
    """测试7: 外部平仓后立即持久化，防止重启恢复幽灵持仓"""
    print("=" * 60)
    print("测试7: external_close 后持久化移除持仓")
    print("=" * 60)

    import json
    import os
    import tempfile

    MessageBus.reset()
    config = {"exchange": "okx", "max_trade_amount": 10}
    rg = PortfolioRiskGuard(config)

    with tempfile.TemporaryDirectory() as tmpdir:
        rg._state_file = os.path.join(tmpdir, "riskguard_state.json")
        rg._positions = {
            "SAHARA-USDT": {
                "symbol": "SAHARA-USDT", "side": "long",
                "entry_price": 0.03547, "amount_usdt": 30.0,
                "leverage": 10, "stop_loss": 0.03519, "take_profit": 0.03682,
                "open_time": time.time(), "highest_price": 0.03677, "lowest_price": 0.03547,
            }
        }

        await rg.on_message({
            "type": "execution_result",
            "payload": {
                "status": "closed_externally",
                "action": "close",
                "symbol": "SAHARA-USDT-SWAP",
                "result": {"symbol": "SAHARA-USDT-SWAP"},
            },
        })

        assert "SAHARA-USDT" not in rg._positions
        with open(rg._state_file, "r") as f:
            saved = json.load(f)
        assert "SAHARA-USDT" not in saved.get("positions", {})

    print("  ✓ external_close 后 state 文件不再包含 SAHARA-USDT")
    print("\n✅ 测试8通过\n")
    return True


async def test_no_false_alarm():
    """测试8: 正常情况不误报"""
    print("=" * 60)
    print("测试8: 正常持仓不误报")
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
    """测试9: 端到端 RiskGuard → Executor 平仓"""
    print("=" * 60)
    print("测试9: 端到端 trailing_stop → Executor平仓")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    from unittest.mock import MagicMock
    from agents.trading.executor import MultiExecutor

    config = {"exchange": "okx", "leverage": 3, "max_trade_amount": 10}
    rg = PortfolioRiskGuard(config)
    executor_agent = MultiExecutor(config)

    mock_exec = MagicMock()
    mock_exec._normalize_symbol = lambda s: s if s.endswith('-SWAP') else f"{s}-SWAP"
    mock_exec.get_position.return_value = {"symbol": "ETH-USDT-SWAP", "side": "long"}
    mock_exec.get_all_positions.return_value = {
        "ETH-USDT-SWAP": {"symbol": "ETH-USDT-SWAP", "side": "long", "amount_usdt": 10, "sl_order_id": "sl_1"}
    }
    mock_exec.positions = {"ETH-USDT-SWAP": {"sl_order_id": "sl_1"}}
    mock_exec.close_position.return_value = {"symbol": "ETH-USDT-SWAP", "pnl": 3.5}
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

    mock_exec.close_position.assert_called_once_with("ETH-USDT-SWAP")
    mock_exec.cancel_order.assert_called_once_with("ETH-USDT-SWAP", "sl_1")
    print("  ✓ RiskGuard trailing_stop → Executor 平仓 ETH-USDT")
    print("  ✓ 止损条件单被撤销")

    print("\n✅ 测试9通过\n")
    return True


async def main():
    results = []
    results.append(await test_position_danger())
    results.append(await test_portfolio_drawdown())
    results.append(await test_flash_move())
    results.append(await test_high_leverage_danger())
    results.append(await test_trailing_stop())
    results.append(await test_correlation_risk())
    results.append(await test_persist_after_external_close())
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
