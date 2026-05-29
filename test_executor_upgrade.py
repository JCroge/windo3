"""Executor 升级验证 - 测试智能执行功能"""

import asyncio
import sys
sys.path.insert(0, '.')

from unittest.mock import MagicMock, patch, AsyncMock
from agents.message_bus import MessageBus


def make_mock_executor():
    """创建mock的ContractExecutor"""
    mock = MagicMock()
    mock.positions = {}
    mock.risk_manager = MagicMock()
    mock.risk_manager.check_can_trade.return_value = (True, "ok")
    mock.risk_manager.max_trade_amount = 10
    mock.get_position.return_value = None
    mock.get_all_positions.return_value = {}
    mock.open_position_with_plan.return_value = {
        'symbol': 'SOL-USDT', 'side': 'long', 'entry_price': 170.0,
        'amount': 0.059, 'leverage': 8, 'stop_loss': 165.0,
    }
    mock.open_long.return_value = {'symbol': 'SOL-USDT', 'side': 'long', 'entry_price': 170.0}
    mock.close_position.return_value = {'symbol': 'SOL-USDT', 'pnl': 2.5}
    mock.reduce_position.return_value = {'symbol': 'SOL-USDT', 'reduced_pct': 0.5}
    mock.cancel_order.return_value = True
    mock.exchange = MagicMock()
    mock.exchange.fetch_balance.return_value = {'total': {'USDT': 100.0}}
    mock.balance_adapter = None  # 走 fetch_balance 路径，避免 MagicMock 触发 _get_balance 实数校验
    return mock


async def test_plan_consumption():
    """测试1: Executor正确消费Judge的plan字段"""
    print("=" * 60)
    print("测试1: 消费Judge plan字段")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.executor import MultiExecutor
    config = {"exchange": "okx", "leverage": 3, "max_trade_amount": 10}
    executor_agent = MultiExecutor(config)

    mock_exec = make_mock_executor()
    executor_agent.executor = mock_exec

    decision = {
        "symbol": "SOL-USDT",
        "action": "open_long",
        "confidence": 75,
        "plan": {
            "leverage": 8,
            "stop_loss": 165.0,
            "take_profit": [180.0, 190.0],
            "order_type": "market",
            "entry_zone": {"low": 169.5, "high": 170.5},
            "size_usdt": 8.0,
            "risk_reward_ratio": 2.5,
        }
    }

    await executor_agent._execute_decision(decision)

    mock_exec.open_position_with_plan.assert_called_once()
    call_args = mock_exec.open_position_with_plan.call_args
    assert call_args[0][0] == "SOL-USDT"
    assert call_args[0][1] == "long"
    assert call_args[0][2]['leverage'] == 8
    assert call_args[0][2]['stop_loss'] == 165.0
    assert call_args[0][2]['order_type'] == "market"
    print("  ✓ open_position_with_plan 被调用，参数正确")
    print(f"    杠杆={call_args[0][2]['leverage']}x, SL={call_args[0][2]['stop_loss']}")

    mock_exec.open_long.assert_not_called()
    print("  ✓ 旧的open_long未被调用（正确走plan路径）")

    print("\n✅ 测试1通过: plan字段正确消费\n")
    return True


async def test_legacy_fallback():
    """测试2: 无plan时走旧逻辑"""
    print("=" * 60)
    print("测试2: 无plan时走旧逻辑（向后兼容）")
    print("=" * 60)

    MessageBus.reset()

    from agents.trading.executor import MultiExecutor
    config = {"exchange": "okx", "leverage": 3, "max_trade_amount": 10}
    executor_agent = MultiExecutor(config)

    mock_exec = make_mock_executor()
    executor_agent.executor = mock_exec

    decision = {
        "symbol": "SOL-USDT",
        "action": "open_long",
        "confidence": 70,
        "size_pct": 0.8,
    }

    await executor_agent._execute_decision(decision)

    mock_exec.open_long.assert_called_once_with("SOL-USDT", 8.0)
    mock_exec.open_position_with_plan.assert_not_called()
    print("  ✓ 无plan时调用旧的open_long")
    print(f"    金额=10*0.8=8.0 USDT")

    print("\n✅ 测试2通过: 向后兼容\n")
    return True


async def test_risk_alert_flash_move():
    """测试3: risk_alert flash_move scope=symbol 只平对应标的，scope=market 全平"""
    print("=" * 60)
    print("测试3: risk_alert flash_move scope 行为")
    print("=" * 60)

    # 3a: scope=symbol 只平单标的
    MessageBus.reset()
    from agents.trading.executor import MultiExecutor
    config = {"exchange": "okx", "leverage": 3, "max_trade_amount": 10}
    executor_agent = MultiExecutor(config)
    mock_exec = make_mock_executor()
    mock_exec._normalize_symbol.side_effect = lambda s: s.replace('-SWAP', '')
    mock_exec.get_position.return_value = {"side": "long", "amount": 0.05, "sl_order_id": "sl_123"}
    mock_exec.positions = {"SOL-USDT": {"sl_order_id": "sl_123"}}
    executor_agent.executor = mock_exec

    alert_symbol = {"type": "flash_move", "scope": "symbol", "symbol": "SOL-USDT"}
    await executor_agent._handle_risk_alert(alert_symbol)
    assert mock_exec.close_position.call_count == 1
    # FR-003: close_position() 内部撤保护单,Agent 层不再直接 cancel_order
    mock_exec.cancel_order.assert_not_called()
    print("  ✓ scope=symbol: 只平 SOL-USDT，保护单清理由 close_position 内部完成")

    # 3b: scope=market 全平所有持仓
    MessageBus.reset()
    executor_agent2 = MultiExecutor(config)
    mock_exec2 = make_mock_executor()
    mock_exec2.get_all_positions.return_value = {
        "SOL-USDT": {"symbol": "SOL-USDT", "side": "long", "amount": 0.05, "sl_order_id": "sl_123"},
        "WIF-USDT": {"symbol": "WIF-USDT", "side": "short", "amount": 3.0, "sl_order_id": None},
    }
    executor_agent2.executor = mock_exec2

    alert_market = {"type": "flash_move", "scope": "market", "details": "BTC -5% in 5min"}
    await executor_agent2._handle_risk_alert(alert_market)
    assert mock_exec2.close_position.call_count == 2
    print("  ✓ scope=market: 2个持仓全部平仓")

    print("\n✅ 测试3通过: flash_move scope 行为正确\n")
    return True


async def test_risk_alert_portfolio_exposure():
    """测试4: risk_alert portfolio_exposure → 减仓最大持仓"""
    print("=" * 60)
    print("测试4: risk_alert portfolio_exposure → 减仓50%")
    print("=" * 60)

    MessageBus.reset()

    from agents.trading.executor import MultiExecutor
    config = {"exchange": "okx", "leverage": 3, "max_trade_amount": 10}
    executor_agent = MultiExecutor(config)

    mock_exec = make_mock_executor()
    mock_exec.get_all_positions.return_value = {
        "SOL-USDT": {"symbol": "SOL-USDT", "amount_usdt": 8.0},
        "WIF-USDT": {"symbol": "WIF-USDT", "amount_usdt": 5.0},
    }
    executor_agent.executor = mock_exec

    alert = {"type": "portfolio_exposure", "details": "total exposure > 80%"}
    await executor_agent._handle_risk_alert(alert)

    mock_exec.reduce_position.assert_called_once_with("SOL-USDT", 0.5)
    print("  ✓ 最大持仓SOL-USDT被减仓50%")
    print("  ✓ 较小持仓WIF-USDT未被动")

    print("\n✅ 测试4通过: portfolio_exposure减仓\n")
    return True


async def test_dynamic_leverage():
    """测试5: 不同plan.leverage值正确传递"""
    print("=" * 60)
    print("测试5: 动态杠杆传递验证")
    print("=" * 60)

    MessageBus.reset()

    from agents.trading.executor import MultiExecutor
    config = {"exchange": "okx", "leverage": 3, "max_trade_amount": 10}
    executor_agent = MultiExecutor(config)

    test_cases = [
        (1, "极低风险"),
        (5, "中等风险"),
        (12, "高置信度"),
        (20, "极端信号"),
    ]

    for lev, desc in test_cases:
        mock_exec = make_mock_executor()
        executor_agent.executor = mock_exec

        decision = {
            "symbol": "SOL-USDT",
            "action": "open_long",
            "confidence": 80,
            "plan": {
                "leverage": lev,
                "stop_loss": 165.0,
                "take_profit": [180.0],
                "order_type": "market",
                "entry_zone": {"low": 169.5, "high": 170.5},
                "size_usdt": 8.0,
            }
        }
        await executor_agent._execute_decision(decision)

        call_args = mock_exec.open_position_with_plan.call_args
        assert call_args[0][2]['leverage'] == lev
        print(f"  ✓ {desc}: leverage={lev}x 正确传递")

    print("\n✅ 测试5通过: 动态杠杆1-20x全范围\n")
    return True


async def test_limit_order_path():
    """测试6: order_type=limit时走限价单路径"""
    print("=" * 60)
    print("测试6: 限价单路径验证")
    print("=" * 60)

    MessageBus.reset()

    from agents.trading.executor import MultiExecutor
    config = {"exchange": "okx", "leverage": 3, "max_trade_amount": 10}
    executor_agent = MultiExecutor(config)

    mock_exec = make_mock_executor()
    executor_agent.executor = mock_exec

    decision = {
        "symbol": "SOL-USDT",
        "action": "open_short",
        "confidence": 72,
        "plan": {
            "leverage": 5,
            "stop_loss": 175.0,
            "take_profit": [160.0, 155.0],
            "order_type": "limit",
            "entry_zone": {"low": 170.0, "high": 172.0},
            "size_usdt": 6.0,
        }
    }
    await executor_agent._execute_decision(decision)

    call_args = mock_exec.open_position_with_plan.call_args
    assert call_args[0][1] == "short"
    assert call_args[0][2]['order_type'] == "limit"
    assert call_args[0][2]['entry_zone'] == {"low": 170.0, "high": 172.0}
    print("  ✓ order_type=limit 正确传递")
    print("  ✓ entry_zone 正确传递")
    print("  ✓ side=short 正确")

    print("\n✅ 测试6通过: 限价单路径\n")
    return True


async def test_confidence_filter():
    """测试7: 置信度不足时拒绝执行"""
    print("=" * 60)
    print("测试7: 置信度过滤")
    print("=" * 60)

    MessageBus.reset()

    from agents.trading.executor import MultiExecutor
    config = {"exchange": "okx", "leverage": 3, "max_trade_amount": 10}
    executor_agent = MultiExecutor(config)

    mock_exec = make_mock_executor()
    executor_agent.executor = mock_exec

    decision = {
        "symbol": "SOL-USDT",
        "action": "open_long",
        "confidence": 45,
        "plan": {"leverage": 3, "stop_loss": 165.0, "take_profit": [180.0],
                 "order_type": "market", "entry_zone": {}, "size_usdt": 5.0}
    }
    await executor_agent._execute_decision(decision)

    mock_exec.open_position_with_plan.assert_not_called()
    mock_exec.open_long.assert_not_called()
    print("  ✓ confidence=45 < 60, 拒绝执行")

    print("\n✅ 测试7通过: 低置信度过滤\n")
    return True


async def test_message_bus_routing():
    """测试8: risk_alert通过消息总线正确路由到Executor"""
    print("=" * 60)
    print("测试8: risk_alert消息总线路由")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.executor import MultiExecutor
    config = {"exchange": "okx", "leverage": 3, "max_trade_amount": 10}
    executor_agent = MultiExecutor(config)

    assert "risk_alert" in executor_agent.subscriptions
    print("  ✓ MultiExecutor订阅了risk_alert")

    assert "trade_decision:*" in executor_agent.subscriptions
    print("  ✓ MultiExecutor订阅了trade_decision:*")

    print("\n✅ 测试8通过: 消息路由正确\n")
    return True


async def main():
    results = []
    results.append(await test_plan_consumption())
    results.append(await test_legacy_fallback())
    results.append(await test_risk_alert_flash_move())
    results.append(await test_risk_alert_portfolio_exposure())
    results.append(await test_dynamic_leverage())
    results.append(await test_limit_order_path())
    results.append(await test_confidence_filter())
    results.append(await test_message_bus_routing())

    print("=" * 60)
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"Executor升级验证: {passed}/{total} 测试通过")
    print("=" * 60)
    return all(results)


if __name__ == '__main__':
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
