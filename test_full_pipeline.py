"""全流水线集成测试 - 11个Agent端到端协作验证"""

import asyncio
import sys
import time
sys.path.insert(0, '.')

from unittest.mock import MagicMock, patch, AsyncMock
from agents.message_bus import MessageBus


async def test_research_to_trading_pipeline():
    """测试1: 研判层 → 交易层完整流水线"""
    print("=" * 60)
    print("测试1: 研判层 → SymbolRouter → 交易层路由")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.research.symbol_router import SymbolRouter
    from agents.trading.multi_data_collector import MultiDataCollector

    sr = SymbolRouter({"max_active_symbols": 3})
    dc = MultiDataCollector({"exchange": "okx"})

    assert "symbol_update" in dc.subscriptions
    print("  ✓ DataCollector 订阅了 symbol_update")

    bus.register("test_listener", ["symbol_update"])

    await sr.publish("symbol_update", {
        "symbols": ["SOL-USDT", "WIF-USDT"],
        "action": "activate",
        "source": "research_synthesizer",
    })

    msg = await bus.receive("multi_data_collector", timeout=1.0)
    assert msg is not None, "DataCollector should receive symbol_update"
    assert msg['type'] == 'symbol_update'
    assert "SOL-USDT" in msg['payload']['symbols']
    print("  ✓ SymbolRouter → DataCollector 路由成功")

    print("\n✅ 测试1通过\n")
    return True


async def test_data_to_analysis_pipeline():
    """测试2: DataCollector → TechAnalyst 路由"""
    print("=" * 60)
    print("测试2: DataCollector → TechAnalyst (market_data:symbol)")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.multi_data_collector import MultiDataCollector
    from agents.trading.tech_analyst import MultiTechAnalyst

    dc = MultiDataCollector({"exchange": "okx"})
    ta = MultiTechAnalyst({"exchange": "okx"})

    assert "market_data:*" in ta.subscriptions
    print("  ✓ TechAnalyst 订阅了 market_data:*")

    await dc.publish("market_data", {
        "symbol": "SOL-USDT",
        "latest_price": 170.5,
        "klines_1h": [[time.time(), 170, 172, 169, 170.5, 1000]],
        "orderbook": {"asks": [[171, 100]], "bids": [[170, 100]]},
        "funding_rate": 0.0001,
    }, symbol="SOL-USDT")

    msg = await bus.receive("tech_analyst", timeout=1.0)
    assert msg is not None, "TechAnalyst should receive market_data"
    assert msg['type'] == 'market_data'
    assert msg['symbol'] == 'SOL-USDT'
    assert msg['payload']['latest_price'] == 170.5
    print("  ✓ DataCollector → TechAnalyst 路由成功 (symbol=SOL-USDT)")

    print("\n✅ 测试2通过\n")
    return True


async def test_analysis_to_judge_pipeline():
    """测试3: TechAnalyst → Judge 路由"""
    print("=" * 60)
    print("测试3: TechAnalyst → Judge (tech_analysis:symbol)")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.tech_analyst import MultiTechAnalyst
    from agents.trading.judge import MultiJudge

    ta = MultiTechAnalyst({"exchange": "okx"})
    judge = MultiJudge({"exchange": "okx"})

    assert "tech_analysis:*" in judge.subscriptions
    print("  ✓ Judge 订阅了 tech_analysis:*")

    await ta.publish("tech_analysis", {
        "symbol": "SOL-USDT",
        "trend": {"direction": "bullish", "strength": 0.7},
        "momentum": {"rsi": 55, "macd_signal": "bullish"},
        "key_levels": {"support": [165, 160], "resistance": [175, 180]},
        "risk_score": 4,
    }, symbol="SOL-USDT")

    msg = await bus.receive("judge", timeout=1.0)
    assert msg is not None, "Judge should receive tech_analysis"
    assert msg['type'] == 'tech_analysis'
    assert msg['symbol'] == 'SOL-USDT'
    assert msg['payload']['trend']['direction'] == 'bullish'
    print("  ✓ TechAnalyst → Judge 路由成功")

    print("\n✅ 测试3通过\n")
    return True


async def test_judge_to_executor_pipeline():
    """测试4: Judge → Executor (trade_decision:symbol)"""
    print("=" * 60)
    print("测试4: Judge → Executor (trade_decision:symbol + plan)")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.judge import MultiJudge
    from agents.trading.executor import MultiExecutor

    judge = MultiJudge({"exchange": "okx"})
    executor = MultiExecutor({"exchange": "okx", "leverage": 3, "max_trade_amount": 10})

    assert "trade_decision:*" in executor.subscriptions
    print("  ✓ Executor 订阅了 trade_decision:*")

    await judge.publish("trade_decision", {
        "symbol": "SOL-USDT",
        "action": "open_long",
        "confidence": 78,
        "reasoning": "趋势+动量+资金流共振",
        "plan": {
            "leverage": 8,
            "stop_loss": 165.0,
            "take_profit": [180.0, 190.0],
            "order_type": "market",
            "entry_zone": {"low": 169.5, "high": 170.5},
            "size_usdt": 8.0,
            "risk_reward_ratio": 2.5,
        }
    }, symbol="SOL-USDT")

    msg = await bus.receive("executor", timeout=1.0)
    assert msg is not None, "Executor should receive trade_decision"
    assert msg['type'] == 'trade_decision'
    assert msg['symbol'] == 'SOL-USDT'
    assert msg['payload']['plan']['leverage'] == 8
    assert msg['payload']['confidence'] == 78
    print("  ✓ Judge → Executor 路由成功 (含plan字段)")

    print("\n✅ 测试4通过\n")
    return True


async def test_executor_to_riskguard_pipeline():
    """测试5: Executor → RiskGuard (execution_result)"""
    print("=" * 60)
    print("测试5: Executor → RiskGuard (execution_result + symbol)")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.executor import MultiExecutor
    from agents.trading.portfolio_risk_guard import PortfolioRiskGuard

    executor = MultiExecutor({"exchange": "okx", "leverage": 3, "max_trade_amount": 10})
    rg = PortfolioRiskGuard({"exchange": "okx", "max_trade_amount": 10})

    assert "execution_result" in rg.subscriptions
    print("  ✓ RiskGuard 订阅了 execution_result (bare topic)")

    await executor.publish("execution_result", {
        "status": "executed",
        "action": "open_long",
        "symbol": "SOL-USDT",
        "result": {
            "entry_price": 170.0,
            "amount_usdt": 8.0,
            "leverage": 8,
            "stop_loss": 165.0,
            "take_profit": 180.0,
        },
        "confidence": 78,
    }, symbol="SOL-USDT")

    msg = await bus.receive("portfolio_risk_guard", timeout=1.0)
    assert msg is not None, "RiskGuard should receive execution_result (bare topic matches symbol-scoped publish)"
    assert msg['type'] == 'execution_result'
    assert msg['payload']['status'] == 'executed'
    assert msg['payload']['result']['leverage'] == 8
    print("  ✓ Executor → RiskGuard 路由成功 (bare topic匹配symbol-scoped消息)")

    print("\n✅ 测试5通过\n")
    return True


async def test_riskguard_to_executor_pipeline():
    """测试6: RiskGuard → Executor (risk_alert)"""
    print("=" * 60)
    print("测试6: RiskGuard → Executor (risk_alert)")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.portfolio_risk_guard import PortfolioRiskGuard
    from agents.trading.executor import MultiExecutor

    rg = PortfolioRiskGuard({"exchange": "okx", "max_trade_amount": 10})
    executor = MultiExecutor({"exchange": "okx", "leverage": 3, "max_trade_amount": 10})

    assert "risk_alert" in executor.subscriptions
    print("  ✓ Executor 订阅了 risk_alert")

    await rg.publish("risk_alert", {
        "type": "trailing_stop",
        "symbol": "SOL-USDT",
        "profit_pct": 5.5,
        "retrace_pct": 55.0,
        "action": "close_position",
    }, symbol="SOL-USDT")

    msg = await bus.receive("executor", timeout=1.0)
    assert msg is not None, "Executor should receive risk_alert"
    assert msg['type'] == 'risk_alert'
    assert msg['payload']['type'] == 'trailing_stop'
    assert msg['payload']['symbol'] == 'SOL-USDT'
    print("  ✓ RiskGuard → Executor 路由成功")

    print("\n✅ 测试6通过\n")
    return True


async def test_multi_symbol_isolation():
    """测试7: 多标的并行隔离 - 不同symbol消息不串台"""
    print("=" * 60)
    print("测试7: 多标的并行隔离 (SOL vs WIF)")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.multi_data_collector import MultiDataCollector
    from agents.trading.tech_analyst import MultiTechAnalyst

    dc = MultiDataCollector({"exchange": "okx"})
    ta = MultiTechAnalyst({"exchange": "okx"})

    await dc.publish("market_data", {
        "symbol": "SOL-USDT", "latest_price": 170.0
    }, symbol="SOL-USDT")

    await dc.publish("market_data", {
        "symbol": "WIF-USDT", "latest_price": 2.5
    }, symbol="WIF-USDT")

    msg1 = await bus.receive("tech_analyst", timeout=1.0)
    msg2 = await bus.receive("tech_analyst", timeout=1.0)

    assert msg1 is not None and msg2 is not None
    symbols = {msg1['payload']['symbol'], msg2['payload']['symbol']}
    assert symbols == {"SOL-USDT", "WIF-USDT"}
    print("  ✓ TechAnalyst 收到两个不同symbol的market_data")
    print("  ✓ 消息不串台，各自携带正确symbol")

    print("\n✅ 测试7通过\n")
    return True


async def test_price_tick_to_riskguard():
    """测试8: DataCollector price_tick → RiskGuard 实时价格更新"""
    print("=" * 60)
    print("测试8: price_tick → RiskGuard 价格追踪")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.multi_data_collector import MultiDataCollector
    from agents.trading.portfolio_risk_guard import PortfolioRiskGuard

    dc = MultiDataCollector({"exchange": "okx"})
    rg = PortfolioRiskGuard({"exchange": "okx", "max_trade_amount": 10})

    assert "price_tick:*" in rg.subscriptions
    print("  ✓ RiskGuard 订阅了 price_tick:*")

    await dc.publish("price_tick", {
        "symbol": "SOL-USDT", "price": 172.5
    }, symbol="SOL-USDT")

    msg = await bus.receive("portfolio_risk_guard", timeout=1.0)
    assert msg is not None, "RiskGuard should receive price_tick"
    assert msg['type'] == 'price_tick'
    assert msg['payload']['price'] == 172.5

    await rg.on_message(msg)
    assert rg._prices.get("SOL-USDT") == 172.5
    print("  ✓ price_tick 路由到 RiskGuard 并更新内部价格")

    print("\n✅ 测试8通过\n")
    return True


async def test_full_trading_cycle():
    """测试9: 完整交易周期 - 开仓→持仓追踪→风控触发→平仓"""
    print("=" * 60)
    print("测试9: 完整交易周期 (开仓→追踪→风控→平仓)")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.executor import MultiExecutor
    from agents.trading.portfolio_risk_guard import PortfolioRiskGuard

    config = {"exchange": "okx", "leverage": 3, "max_trade_amount": 10}
    executor = MultiExecutor(config)
    rg = PortfolioRiskGuard(config)

    mock_exec = MagicMock()
    mock_exec._normalize_symbol = lambda s: s if s.endswith('-SWAP') else f"{s}-SWAP"
    mock_exec.get_position.return_value = None
    mock_exec.get_all_positions.return_value = {}
    mock_exec.open_position_with_plan.return_value = {
        'symbol': 'SOL-USDT', 'side': 'long', 'entry_price': 170.0,
        'amount': 0.047, 'amount_usdt': 8.0, 'leverage': 5,
        'stop_loss': 165.0, 'take_profit': 180.0,
    }
    mock_exec.close_position.return_value = {'symbol': 'SOL-USDT', 'pnl': -1.5}
    mock_exec.cancel_order.return_value = True
    mock_exec.positions = {}
    mock_exec.exchange = MagicMock()
    mock_exec.exchange.fetch_balance.return_value = {'total': {'USDT': 100.0}}
    mock_exec.balance_adapter = None  # 走 fetch_balance 路径，满足 _get_balance 实数校验
    mock_exec.risk_manager = MagicMock()
    mock_exec.risk_manager.check_can_trade.return_value = (True, "ok")
    executor.executor = mock_exec

    # Step 1: Judge发出trade_decision
    print("  [Step 1] Judge → trade_decision")
    decision_msg = {
        "msg_id": "test-1", "from": "judge", "to": "broadcast",
        "type": "trade_decision", "symbol": "SOL-USDT",
        "timestamp": time.time(),
        "payload": {
            "symbol": "SOL-USDT", "action": "open_long", "confidence": 75,
            "plan": {
                "leverage": 5, "stop_loss": 165.0, "take_profit": [180.0],
                "order_type": "market", "entry_zone": {"low": 169.5, "high": 170.5},
                "size_usdt": 8.0,
            }
        }
    }
    await executor.on_message(decision_msg)
    mock_exec.open_position_with_plan.assert_called_once()
    print("    ✓ Executor 执行开仓")

    # Step 2: RiskGuard收到execution_result
    print("  [Step 2] Executor → execution_result → RiskGuard")
    exec_msg = await bus.receive("portfolio_risk_guard", timeout=1.0)
    assert exec_msg is not None, "RiskGuard should receive execution_result"
    assert exec_msg['payload']['status'] == 'executed'

    await rg.on_message(exec_msg)
    assert "SOL-USDT" in rg._positions
    assert rg._positions["SOL-USDT"]["entry_price"] == 170.0
    assert rg._positions["SOL-USDT"]["leverage"] == 5
    print("    ✓ RiskGuard 记录持仓 (entry=170, lev=5x)")

    # Step 3: 价格下跌，RiskGuard检测到浮亏
    print("  [Step 3] 价格下跌 → 浮亏检测")
    rg._prices["SOL-USDT"] = 163.0
    # PnL: (163-170)/170 * 5 * 100 = -20.6% > -15% threshold
    await rg._check_position_pnl("SOL-USDT", 163.0)

    risk_msg = await bus.receive("executor", timeout=1.0)
    assert risk_msg is not None, "Executor should receive risk_alert"
    assert risk_msg['payload']['type'] == 'position_danger'
    print("    ✓ RiskGuard 发出 position_danger (pnl=-20.6%)")

    # Step 4: Executor执行风控平仓
    print("  [Step 4] Executor 风控平仓")
    # 实际生产中 _normalize_symbol 会把 SOL-USDT → SOL-USDT-SWAP，positions 用 SWAP 格式
    mock_exec.get_position.return_value = {"symbol": "SOL-USDT-SWAP", "side": "long"}
    mock_exec.positions = {"SOL-USDT-SWAP": {"sl_order_id": "sl_abc"}}
    await executor._handle_risk_alert(risk_msg['payload'])
    mock_exec.close_position.assert_called_once_with("SOL-USDT-SWAP")
    mock_exec.cancel_order.assert_called_once_with("SOL-USDT-SWAP", "sl_abc")
    print("    ✓ Executor 平仓 SOL-USDT-SWAP + 撤销止损单")

    # Step 5: RiskGuard收到force_closed，移除持仓
    print("  [Step 5] force_closed → RiskGuard 移除持仓")
    force_msg = await bus.receive("portfolio_risk_guard", timeout=1.0)
    assert force_msg is not None
    assert force_msg['payload']['status'] == 'force_closed'

    await rg.on_message(force_msg)
    assert "SOL-USDT" not in rg._positions
    print("    ✓ RiskGuard 移除持仓记录")

    print("\n✅ 测试9通过: 完整交易周期闭环\n")
    return True


async def test_research_layer_internal_routing():
    """测试10: 研判层内部路由 (Scanner/Sentiment/News → Synthesizer → Censor → Synthesizer → SymbolRouter)"""
    print("=" * 60)
    print("测试10: 研判层内部消息路由")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.research.market_scanner import MarketScanner
    from agents.research.sentiment_researcher import SentimentResearcher
    from agents.research.news_researcher import NewsResearcher
    from agents.research.synthesizer import ResearchSynthesizer
    from agents.research.censor import Censor
    from agents.research.symbol_router import SymbolRouter

    ms = MarketScanner({})
    sr = SentimentResearcher({})
    nr = NewsResearcher({})
    synth = ResearchSynthesizer({})
    censor = Censor({})
    symbol_router = SymbolRouter({"max_active_symbols": 3})

    # 验证订阅关系
    assert "research_trigger" in ms.subscriptions
    assert "research_trigger" in sr.subscriptions
    assert "research_trigger" in nr.subscriptions
    print("  ✓ Scanner/Sentiment/News 都订阅 research_trigger")

    assert "research_market_data" in synth.subscriptions
    assert "research_sentiment_data" in synth.subscriptions
    assert "research_news_data" in synth.subscriptions
    print("  ✓ Synthesizer 订阅三路研判数据")

    assert "research_preliminary" in censor.subscriptions
    print("  ✓ Censor 订阅 research_preliminary")

    assert "research_result" in symbol_router.subscriptions
    print("  ✓ SymbolRouter 订阅 research_result")

    # 模拟 Scanner 发布 → Synthesizer 收到
    await ms.publish("research_market_data", {
        "top_movers": [{"symbol": "SOL-USDT", "change_24h": 5.2}],
        "high_volume": [{"symbol": "WIF-USDT", "volume_24h": 50000000}],
    })

    msg = await bus.receive("research_synthesizer", timeout=1.0)
    assert msg is not None
    assert msg['type'] == 'research_market_data'
    print("  ✓ MarketScanner → Synthesizer 路由成功")

    # 模拟 Synthesizer 发布 preliminary → Censor 收到
    await synth.publish("research_preliminary", {
        "candidates": ["SOL-USDT", "WIF-USDT"],
        "reasoning": "高波动+高成交量",
    })

    msg = await bus.receive("censor", timeout=1.0)
    assert msg is not None
    assert msg['type'] == 'research_preliminary'
    print("  ✓ Synthesizer → Censor 路由成功")

    # 模拟 Synthesizer 发布 final result → SymbolRouter 收到
    await synth.publish("research_result", {
        "selected_symbols": ["SOL-USDT", "WIF-USDT"],
        "reasoning": "综合研判通过言官审查",
    })

    msg = await bus.receive("symbol_router", timeout=1.0)
    assert msg is not None
    assert msg['type'] == 'research_result'
    print("  ✓ Synthesizer → SymbolRouter 路由成功")

    print("\n✅ 测试10通过: 研判层内部路由完整\n")
    return True


async def test_no_self_receive():
    """测试11: Agent不会收到自己发的消息"""
    print("=" * 60)
    print("测试11: 消息总线自过滤 (不收自己的消息)")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.executor import MultiExecutor
    executor = MultiExecutor({"exchange": "okx", "leverage": 3, "max_trade_amount": 10})

    # Executor订阅了risk_alert，如果它自己发risk_alert不应该收到
    await executor.publish("risk_alert", {
        "type": "test_self", "symbol": "TEST"
    })

    msg = await bus.receive("executor", timeout=0.5)
    assert msg is None, "Agent should NOT receive its own message"
    print("  ✓ Executor 不会收到自己发的 risk_alert")

    print("\n✅ 测试11通过\n")
    return True


async def test_concurrent_symbols_full_flow():
    """测试12: 多标的并发完整流 - 2个symbol同时走完交易层"""
    print("=" * 60)
    print("测试12: 多标的并发 (SOL + WIF 同时走完交易层)")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.executor import MultiExecutor
    from agents.trading.portfolio_risk_guard import PortfolioRiskGuard

    config = {"exchange": "okx", "leverage": 3, "max_trade_amount": 10}
    executor = MultiExecutor(config)
    rg = PortfolioRiskGuard(config)

    mock_exec = MagicMock()
    mock_exec.get_position.return_value = None
    mock_exec.open_position_with_plan.side_effect = [
        {'symbol': 'SOL-USDT', 'side': 'long', 'entry_price': 170.0,
         'amount': 0.047, 'amount_usdt': 8.0, 'leverage': 5,
         'stop_loss': 165.0, 'take_profit': 180.0},
        {'symbol': 'WIF-USDT', 'side': 'short', 'entry_price': 2.5,
         'amount': 3.2, 'amount_usdt': 6.0, 'leverage': 10,
         'stop_loss': 2.6, 'take_profit': 2.3},
    ]
    mock_exec.exchange = MagicMock()
    mock_exec.exchange.fetch_balance.return_value = {'total': {'USDT': 100.0}}
    mock_exec.balance_adapter = None  # 走 fetch_balance 路径，满足 _get_balance 实数校验
    mock_exec.risk_manager = MagicMock()
    mock_exec.risk_manager.check_can_trade.return_value = (True, "ok")
    executor.executor = mock_exec

    # 两个trade_decision同时到达
    for symbol, side, lev, sl in [("SOL-USDT", "open_long", 5, 165.0), ("WIF-USDT", "open_short", 10, 2.6)]:
        msg = {
            "msg_id": f"test-{symbol}", "from": "judge", "to": "broadcast",
            "type": "trade_decision", "symbol": symbol,
            "timestamp": time.time(),
            "payload": {
                "symbol": symbol, "action": side, "confidence": 72,
                "plan": {"leverage": lev, "stop_loss": sl, "take_profit": [],
                         "order_type": "market", "entry_zone": {}, "size_usdt": 8.0}
            }
        }
        await executor.on_message(msg)

    assert mock_exec.open_position_with_plan.call_count == 2
    print("  ✓ 两个标的都成功开仓")

    # RiskGuard收到两个execution_result
    msg1 = await bus.receive("portfolio_risk_guard", timeout=1.0)
    msg2 = await bus.receive("portfolio_risk_guard", timeout=1.0)
    assert msg1 is not None and msg2 is not None

    await rg.on_message(msg1)
    await rg.on_message(msg2)

    assert len(rg._positions) == 2
    assert "SOL-USDT" in rg._positions
    assert "WIF-USDT" in rg._positions
    assert rg._positions["SOL-USDT"]["side"] == "long"
    assert rg._positions["WIF-USDT"]["side"] == "short"
    assert rg._positions["WIF-USDT"]["leverage"] == 10
    print("  ✓ RiskGuard 同时追踪 SOL(long 5x) + WIF(short 10x)")

    print("\n✅ 测试12通过: 多标的并发\n")
    return True


async def main():
    results = []
    results.append(await test_research_to_trading_pipeline())
    results.append(await test_data_to_analysis_pipeline())
    results.append(await test_analysis_to_judge_pipeline())
    results.append(await test_judge_to_executor_pipeline())
    results.append(await test_executor_to_riskguard_pipeline())
    results.append(await test_riskguard_to_executor_pipeline())
    results.append(await test_multi_symbol_isolation())
    results.append(await test_price_tick_to_riskguard())
    results.append(await test_full_trading_cycle())
    results.append(await test_research_layer_internal_routing())
    results.append(await test_no_self_receive())
    results.append(await test_concurrent_symbols_full_flow())

    print("=" * 60)
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"全流水线集成测试: {passed}/{total} 测试通过")
    if all(results):
        print("🎉 所有Agent协作路由验证通过！")
    print("=" * 60)
    return all(results)


if __name__ == '__main__':
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
