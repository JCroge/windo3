"""P0功能测试 - Reviewer + Daily Hard Stop + Graceful Shutdown"""

import asyncio
import sys
import time
import datetime
import os
sys.path.insert(0, '.')

from agents.message_bus import MessageBus
from agents.trading.reviewer import ReviewerAgent
from agents.trading.portfolio_risk_guard import PortfolioRiskGuard
from agents.trading.executor import MultiExecutor
from unittest.mock import MagicMock


async def test_reviewer_trade_history():
    """测试1: ReviewerAgent交易历史追踪"""
    print("=" * 60)
    print("测试1: ReviewerAgent交易历史追踪")
    print("=" * 60)

    # 清理之前的测试数据
    if os.path.exists('data/trade_history.json'):
        os.remove('data/trade_history.json')

    MessageBus.reset()
    bus = MessageBus.get_instance()

    config = {"exchange": "okx", "max_trade_amount": 10}
    reviewer = ReviewerAgent(config)

    await reviewer.setup()

    # 模拟3笔交易结果
    trades = [
        {"symbol": "SOL-USDT", "pnl": 2.5, "confidence": 75},
        {"symbol": "WIF-USDT", "pnl": -1.2, "confidence": 68},
        {"symbol": "BTC-USDT", "pnl": 3.8, "confidence": 82},
    ]

    for trade in trades:
        msg = {
            "msg_id": f"test-{trade['symbol']}",
            "from": "executor",
            "to": "broadcast",
            "type": "execution_result",
            "symbol": trade['symbol'],
            "timestamp": time.time(),
            "payload": {
                "status": "force_closed",
                "action": "close",
                "symbol": trade['symbol'],
                "result": {"pnl": trade['pnl']},
                "confidence": trade['confidence']
            }
        }
        await reviewer.on_message(msg)

    assert len(reviewer.trade_history) == 3
    assert reviewer.trade_history[0]['pnl'] == 2.5
    assert reviewer.trade_history[1]['pnl'] == -1.2
    print(f"  ✓ 交易历史记录: {len(reviewer.trade_history)}笔")

    # 验证持久化
    assert os.path.exists('data/trade_history.json')
    print("  ✓ trade_history.json已创建")

    # 验证滚动窗口指标
    metrics = reviewer._calculate_rolling_metrics()
    assert metrics['total_trades'] == 3
    assert metrics['winning_trades'] == 2
    assert metrics['losing_trades'] == 1
    assert metrics['win_rate'] == 2/3
    print(f"  ✓ 滚动窗口指标: 胜率{metrics['win_rate']:.1%}, 盈亏比{metrics['profit_factor']:.2f}")

    print("\n✅ 测试1通过\n")
    return True


async def test_daily_hard_stop_loss_limit():
    """测试2: Daily Hard Stop - 单日亏损触发"""
    print("=" * 60)
    print("测试2: Daily Hard Stop - 单日亏损触发")
    print("=" * 60)

    # 清理之前的测试数据
    if os.path.exists('data/trade_history.json'):
        os.remove('data/trade_history.json')

    MessageBus.reset()
    bus = MessageBus.get_instance()

    config = {"exchange": "okx", "max_trade_amount": 10, "daily_pnl_hard_stop": -50.0}
    reviewer = ReviewerAgent(config)

    await reviewer.setup()

    bus.register("test_listener", ["daily_hard_stop_triggered"])

    # 模拟单日累计亏损达到-50 USDT
    today = datetime.datetime.utcnow().date()
    losses = [
        {"symbol": "SOL-USDT", "pnl": -15.0},
        {"symbol": "WIF-USDT", "pnl": -18.0},
        {"symbol": "BTC-USDT", "pnl": -17.5},
    ]

    for loss in losses:
        msg = {
            "msg_id": f"test-{loss['symbol']}",
            "from": "executor",
            "to": "broadcast",
            "type": "execution_result",
            "symbol": loss['symbol'],
            "timestamp": time.time(),
            "payload": {
                "status": "force_closed",
                "action": "close",
                "symbol": loss['symbol'],
                "result": {"pnl": loss['pnl']},
                "confidence": 70
            }
        }
        await reviewer.on_message(msg)

    # 验证触发daily hard stop
    msg = await bus.receive("test_listener", timeout=1.0)
    assert msg is not None, "Should emit daily_hard_stop_triggered"
    assert msg['payload']['reason'] == 'daily_loss_limit'
    assert msg['payload']['daily_pnl'] <= -50.0
    print(f"  ✓ Daily hard stop触发: 单日亏损{msg['payload']['daily_pnl']:.2f} USDT")

    print("\n✅ 测试2通过\n")
    return True


async def test_daily_hard_stop_consecutive_losses():
    """测试3: Daily Hard Stop - 连续亏损触发"""
    print("=" * 60)
    print("测试3: Daily Hard Stop - 连续亏损触发")
    print("=" * 60)

    # 清理之前的测试数据
    if os.path.exists('data/trade_history.json'):
        os.remove('data/trade_history.json')

    MessageBus.reset()
    bus = MessageBus.get_instance()

    config = {"exchange": "okx", "max_trade_amount": 10, "consecutive_loss_limit": 3}
    reviewer = ReviewerAgent(config)

    await reviewer.setup()

    bus.register("test_listener", ["daily_hard_stop_triggered"])

    # 模拟连续3笔亏损
    losses = [
        {"symbol": "SOL-USDT", "pnl": -2.0},
        {"symbol": "WIF-USDT", "pnl": -1.5},
        {"symbol": "BTC-USDT", "pnl": -3.0},
    ]

    for loss in losses:
        msg = {
            "msg_id": f"test-{loss['symbol']}",
            "from": "executor",
            "to": "broadcast",
            "type": "execution_result",
            "symbol": loss['symbol'],
            "timestamp": time.time(),
            "payload": {
                "status": "force_closed",
                "action": "close",
                "symbol": loss['symbol'],
                "result": {"pnl": loss['pnl']},
                "confidence": 70
            }
        }
        await reviewer.on_message(msg)

    # 验证触发daily hard stop
    msg = await bus.receive("test_listener", timeout=1.0)
    assert msg is not None, "Should emit daily_hard_stop_triggered"
    assert msg['payload']['reason'] == 'consecutive_losses'
    assert msg['payload']['count'] == 3
    print(f"  ✓ Daily hard stop触发: 连续{msg['payload']['count']}次亏损")

    print("\n✅ 测试3通过\n")
    return True


async def test_strategy_decay_detection():
    """测试4: 策略衰减检测"""
    print("=" * 60)
    print("测试4: 策略衰减检测")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    config = {"exchange": "okx", "rolling_window_size": 10}
    reviewer = ReviewerAgent(config)

    await reviewer.setup()

    # 模拟20笔交易：前10笔胜率80%，后10笔胜率30%
    # 前10笔：8胜2负
    for i in range(8):
        msg = {
            "msg_id": f"test-win-{i}",
            "from": "executor",
            "to": "broadcast",
            "type": "execution_result",
            "symbol": f"SYM{i}-USDT",
            "timestamp": time.time(),
            "payload": {
                "status": "force_closed",
                "action": "close",
                "symbol": f"SYM{i}-USDT",
                "result": {"pnl": 2.0},
                "confidence": 75
            }
        }
        await reviewer.on_message(msg)
        await asyncio.sleep(0.01)

    for i in range(2):
        msg = {
            "msg_id": f"test-loss-{i}",
            "from": "executor",
            "to": "broadcast",
            "type": "execution_result",
            "symbol": f"SYML{i}-USDT",
            "timestamp": time.time(),
            "payload": {
                "status": "force_closed",
                "action": "close",
                "symbol": f"SYML{i}-USDT",
                "result": {"pnl": -1.0},
                "confidence": 70
            }
        }
        await reviewer.on_message(msg)
        await asyncio.sleep(0.01)

    # 后10笔：3胜7负
    for i in range(3):
        msg = {
            "msg_id": f"test-win2-{i}",
            "from": "executor",
            "to": "broadcast",
            "type": "execution_result",
            "symbol": f"SYM2{i}-USDT",
            "timestamp": time.time(),
            "payload": {
                "status": "force_closed",
                "action": "close",
                "symbol": f"SYM2{i}-USDT",
                "result": {"pnl": 1.5},
                "confidence": 65
            }
        }
        await reviewer.on_message(msg)
        await asyncio.sleep(0.01)

    for i in range(7):
        msg = {
            "msg_id": f"test-loss2-{i}",
            "from": "executor",
            "to": "broadcast",
            "type": "execution_result",
            "symbol": f"SYML2{i}-USDT",
            "timestamp": time.time(),
            "payload": {
                "status": "force_closed",
                "action": "close",
                "symbol": f"SYML2{i}-USDT",
                "result": {"pnl": -2.0},
                "confidence": 60
            }
        }
        await reviewer.on_message(msg)
        await asyncio.sleep(0.01)

    # 检测策略衰减
    decay_signals = reviewer._detect_strategy_decay()
    assert decay_signals is not None, "Should detect strategy decay"
    assert len(decay_signals) > 0
    print(f"  ✓ 检测到策略衰减: {len(decay_signals)}个指标异常")
    for signal in decay_signals:
        print(f"    - {signal['metric']}: {signal['recent']:.2f} < 阈值{signal['threshold']:.2f}")

    print("\n✅ 测试4通过\n")
    return True


async def test_riskguard_state_persistence():
    """测试5: RiskGuard状态持久化"""
    print("=" * 60)
    print("测试5: RiskGuard状态持久化")
    print("=" * 60)

    MessageBus.reset()

    config = {"exchange": "okx", "max_trade_amount": 10}
    rg1 = PortfolioRiskGuard(config)

    await rg1.setup()

    # 模拟2个持仓
    rg1._positions = {
        "SOL-USDT": {
            "symbol": "SOL-USDT", "side": "long",
            "entry_price": 170.0, "amount_usdt": 8.0,
            "leverage": 5, "stop_loss": 165.0,
            "open_time": time.time(),
            "highest_price": 172.0, "lowest_price": 170.0
        },
        "WIF-USDT": {
            "symbol": "WIF-USDT", "side": "short",
            "entry_price": 2.5, "amount_usdt": 6.0,
            "leverage": 10, "stop_loss": 2.6,
            "open_time": time.time(),
            "highest_price": 2.5, "lowest_price": 2.4
        }
    }
    rg1._prices = {"SOL-USDT": 171.0, "WIF-USDT": 2.45}

    # 保存状态
    rg1._save_state()
    assert os.path.exists('data/riskguard_state.json')
    print("  ✓ riskguard_state.json已创建")

    # 重新实例化RiskGuard
    rg2 = PortfolioRiskGuard(config)
    await rg2.setup()

    # 验证状态恢复
    assert len(rg2._positions) == 2
    assert "SOL-USDT" in rg2._positions
    assert "WIF-USDT" in rg2._positions
    assert rg2._positions["SOL-USDT"]["leverage"] == 5
    assert rg2._positions["WIF-USDT"]["leverage"] == 10
    assert rg2._prices["SOL-USDT"] == 171.0
    print(f"  ✓ 状态恢复: {len(rg2._positions)}个持仓")
    print(f"    - SOL-USDT: long 5x @ 170.0")
    print(f"    - WIF-USDT: short 10x @ 2.5")

    print("\n✅ 测试5通过\n")
    return True


async def test_e2e_hard_stop_flow():
    """测试6: 端到端 - 亏损触发hard stop → Executor/RiskGuard响应"""
    print("=" * 60)
    print("测试6: 端到端 hard stop流程")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    config = {"exchange": "okx", "max_trade_amount": 10, "daily_pnl_hard_stop": -20.0}
    reviewer = ReviewerAgent(config)
    executor = MultiExecutor(config)
    rg = PortfolioRiskGuard(config)

    # Mock executor
    mock_exec = MagicMock()
    mock_exec.get_position.return_value = {"symbol": "SOL-USDT", "side": "long"}
    mock_exec.get_all_positions.return_value = {
        "SOL-USDT": {"symbol": "SOL-USDT", "side": "long", "amount_usdt": 10}
    }
    mock_exec.positions = {"SOL-USDT": {}}
    mock_exec.close_position.return_value = {"symbol": "SOL-USDT", "pnl": -5.0}
    mock_exec.cancel_order.return_value = True
    executor.executor = mock_exec

    await reviewer.setup()
    await rg.setup()

    # Step 1: 模拟单日亏损达到-20 USDT
    print("  [Step 1] 模拟单日亏损-20 USDT")
    losses = [
        {"symbol": "SOL-USDT", "pnl": -8.0},
        {"symbol": "WIF-USDT", "pnl": -7.0},
        {"symbol": "BTC-USDT", "pnl": -5.5},
    ]

    for loss in losses:
        msg = {
            "msg_id": f"test-{loss['symbol']}",
            "from": "executor",
            "to": "broadcast",
            "type": "execution_result",
            "symbol": loss['symbol'],
            "timestamp": time.time(),
            "payload": {
                "status": "force_closed",
                "action": "close",
                "symbol": loss['symbol'],
                "result": {"pnl": loss['pnl']},
                "confidence": 70
            }
        }
        await reviewer.on_message(msg)

    # Step 2: Executor收到daily_hard_stop_triggered
    print("  [Step 2] Executor收到熔断信号")
    msg = await bus.receive("executor", timeout=1.0)
    assert msg is not None
    assert msg['type'] == 'daily_hard_stop_triggered'

    await executor.on_message(msg)
    assert executor._trading_halted == True
    print("    ✓ Executor进入熔断状态")

    # Step 3: RiskGuard收到daily_hard_stop_triggered
    print("  [Step 3] RiskGuard收到熔断信号")
    msg = await bus.receive("portfolio_risk_guard", timeout=1.0)
    assert msg is not None
    assert msg['type'] == 'daily_hard_stop_triggered'

    # 模拟RiskGuard有持仓
    rg._positions = {"SOL-USDT": {"symbol": "SOL-USDT", "side": "long"}}

    await rg.on_message(msg)
    assert rg._trading_halted == True
    print("    ✓ RiskGuard进入熔断状态")

    # Step 4: 验证Executor拒绝新交易
    print("  [Step 4] 验证Executor拒绝新交易")
    decision = {
        "symbol": "ETH-USDT",
        "action": "open_long",
        "confidence": 80,
        "plan": {"leverage": 3}
    }
    await executor._execute_decision(decision)
    # 不应该调用open_position_with_plan
    mock_exec.open_position_with_plan.assert_not_called()
    print("    ✓ Executor拒绝新交易决策")

    print("\n✅ 测试6通过: 完整熔断流程\n")
    return True


async def test_backward_compatibility():
    """测试7: 向后兼容 - 运行现有测试"""
    print("=" * 60)
    print("测试7: 向后兼容性验证")
    print("=" * 60)

    # 运行现有的test_full_pipeline.py确保新功能不破坏现有流水线
    import subprocess
    result = subprocess.run(
        ["python3", "test_full_pipeline.py"],
        capture_output=True,
        text=True,
        timeout=60
    )

    if result.returncode == 0:
        print("  ✓ test_full_pipeline.py 全部通过")
    else:
        print(f"  ✗ test_full_pipeline.py 失败")
        print(result.stdout)
        print(result.stderr)
        return False

    print("\n✅ 测试7通过: 向后兼容\n")
    return True


async def main():
    print("\n" + "=" * 60)
    print("P0功能测试套件")
    print("=" * 60 + "\n")

    results = []
    results.append(await test_reviewer_trade_history())
    results.append(await test_daily_hard_stop_loss_limit())
    results.append(await test_daily_hard_stop_consecutive_losses())
    results.append(await test_strategy_decay_detection())
    results.append(await test_riskguard_state_persistence())
    results.append(await test_e2e_hard_stop_flow())
    results.append(await test_backward_compatibility())

    print("=" * 60)
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"P0功能测试: {passed}/{total} 测试通过")
    if all(results):
        print("🎉 所有P0功能验证通过！")
    print("=" * 60)

    # 清理测试文件
    import os
    for f in ['data/trade_history.json', 'data/riskguard_state.json']:
        if os.path.exists(f):
            os.remove(f)

    return all(results)


if __name__ == '__main__':
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
