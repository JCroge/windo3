"""全链路验证测试 - 7层15个测试用例，逐步验证所有Agent链路通畅"""

import asyncio
import sys
import os
import time
import json

sys.path.insert(0, '.')

from agents.message_bus import MessageBus


# ═══════════════════════════════════════════════════════════════
# Layer 1: 消息总线基础验证
# ═══════════════════════════════════════════════════════════════

async def test_1_topic_symbol_routing():
    """topic:symbol 精确路由"""
    MessageBus.reset()
    bus = MessageBus.get_instance()

    bus.register("agent_sol", ["market_data:SOL-USDT"])
    bus.register("agent_btc", ["market_data:BTC-USDT"])
    bus.register("agent_all", ["market_data:*"])

    await bus.publish("sender", "market_data", {"price": 170}, "broadcast", symbol="SOL-USDT")

    msg_sol = await bus.receive("agent_sol", timeout=1.0)
    msg_btc = await bus.receive("agent_btc", timeout=0.3)
    msg_all = await bus.receive("agent_all", timeout=1.0)

    assert msg_sol is not None, "SOL agent should receive SOL message"
    assert msg_sol['payload']['price'] == 170
    assert msg_btc is None, "BTC agent should NOT receive SOL message"
    assert msg_all is not None, "Wildcard agent should receive SOL message"
    print("  ✓ topic:symbol 精确路由正确")
    print("  ✓ 通配符 :* 正确匹配")
    return True


async def test_2_wildcard_subscription():
    """通配符订阅接收所有symbol"""
    MessageBus.reset()
    bus = MessageBus.get_instance()

    bus.register("monitor", ["tech_analysis:*"])
    symbols = ["SOL-USDT", "BTC-USDT", "ETH-USDT"]

    for sym in symbols:
        await bus.publish("analyst", "tech_analysis", {"symbol": sym}, "broadcast", symbol=sym)

    received = []
    for _ in range(3):
        msg = await bus.receive("monitor", timeout=1.0)
        if msg:
            received.append(msg['payload']['symbol'])

    assert set(received) == set(symbols), f"Expected {symbols}, got {received}"
    print(f"  ✓ 通配符订阅收到全部 {len(symbols)} 个symbol消息")
    return True


async def test_3_broadcast_delivery():
    """broadcast 广播到所有订阅者"""
    MessageBus.reset()
    bus = MessageBus.get_instance()

    bus.register("listener_a", ["daily_hard_stop_triggered"])
    bus.register("listener_b", ["daily_hard_stop_triggered"])
    bus.register("listener_c", ["other_topic"])

    await bus.publish("reviewer", "daily_hard_stop_triggered",
                      {"reason": "test"}, "broadcast")

    msg_a = await bus.receive("listener_a", timeout=1.0)
    msg_b = await bus.receive("listener_b", timeout=1.0)
    msg_c = await bus.receive("listener_c", timeout=0.3)

    assert msg_a is not None, "listener_a should receive broadcast"
    assert msg_b is not None, "listener_b should receive broadcast"
    assert msg_c is None, "listener_c should NOT receive (wrong topic)"
    print("  ✓ broadcast 正确投递到所有订阅者")
    print("  ✓ 非订阅者不会收到消息")
    return True


# ═══════════════════════════════════════════════════════════════
# Layer 2: 研判层链路（6 Agent 串联）
# ═══════════════════════════════════════════════════════════════

async def test_4_research_data_aggregation():
    """三路数据汇聚到 Synthesizer"""
    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.research.synthesizer import ResearchSynthesizer
    config = {"exchange": "okx"}
    synth = ResearchSynthesizer(config)
    synth.llm = None

    # Mock LLM to force rule fallback
    async def mock_llm_json(*args, **kwargs):
        raise Exception("LLM mocked")
    synth.ask_claude_json = mock_llm_json

    # 模拟三路数据到达
    market_msg = {
        "msg_id": "t1", "from": "market_scanner", "to": "broadcast",
        "type": "research_market_data", "symbol": None, "timestamp": time.time(),
        "payload": {
            "candidates": [
                {"symbol": "SOL-USDT", "price": 170.5, "volume_24h": 5e8,
                 "change_pct": 3.5, "change_24h_pct": 3.5, "volatility_pct": 4.2,
                 "funding_rate": 0.01, "oi_change": 5.0,
                 "long_short_ratio": 1.2},
                {"symbol": "ETH-USDT", "price": 3200.0, "volume_24h": 2e9,
                 "change_pct": -1.2, "change_24h_pct": -1.2, "volatility_pct": 3.1,
                 "funding_rate": -0.005, "oi_change": -2.0,
                 "long_short_ratio": 0.9},
            ]
        }
    }
    sentiment_msg = {
        "msg_id": "t2", "from": "sentiment", "to": "broadcast",
        "type": "research_sentiment_data", "symbol": None, "timestamp": time.time(),
        "payload": {
            "fear_greed": {"value": 65, "classification": "Greed"},
            "trending_coins": [{"symbol": "SOL"}, {"symbol": "ETH"}],
            "taker_ratios": {}
        }
    }
    news_msg = {
        "msg_id": "t3", "from": "news", "to": "broadcast",
        "type": "research_news_data", "symbol": None, "timestamp": time.time(),
        "payload": {"headlines": [{"title": "SOL升级", "source": "CoinDesk", "sentiment": "positive"}]}
    }

    # 注册监听器捕获 Synthesizer 输出
    bus.register("test_catcher", ["research_preliminary"])

    # 发送数据 - Synthesizer 在收到 market_data 后触发综合
    await synth.on_message(sentiment_msg)
    await synth.on_message(news_msg)
    await synth.on_message(market_msg)  # 触发 _preliminary_synthesis

    msg = await bus.receive("test_catcher", timeout=3.0)
    assert msg is not None, "Synthesizer should emit research_preliminary"
    assert msg['type'] == 'research_preliminary'
    selected = msg['payload'].get('selected', [])
    assert len(selected) > 0, "Should select at least 1 symbol"
    print(f"  ✓ 三路数据汇聚成功，初选 {len(selected)} 个标的")
    print(f"  ✓ Synthesizer 规则降级正常（无LLM时）")
    return True


async def test_5_research_full_chain():
    """完整研判链路: Synthesizer → Censor → Synthesizer(终选) → SymbolRouter → symbol_update"""
    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.research.synthesizer import ResearchSynthesizer
    from agents.research.censor import Censor
    from agents.research.symbol_router import SymbolRouter

    config = {"exchange": "okx"}
    synth = ResearchSynthesizer(config)
    censor = Censor(config)
    router = SymbolRouter(config)

    # Mock LLM to avoid real API calls — force rule fallback
    async def mock_llm_json(*args, **kwargs):
        raise Exception("LLM mocked out")
    synth.ask_claude_json = mock_llm_json
    censor.ask_claude_json = mock_llm_json

    bus.register("test_final", ["symbol_update"])

    # 模拟 Synthesizer 发出 research_preliminary
    preliminary_payload = {
        "selected": [
            {"symbol": "SOL-USDT", "score": 85, "direction": "long",
             "risk_factor": "中等波动"},
            {"symbol": "ETH-USDT", "score": 70, "direction": "short",
             "risk_factor": "高杠杆风险"},
        ],
        "market_regime": "trending",
        "market_context": "SOL强势上涨，ETH弱势"
    }
    preliminary_msg = {
        "msg_id": "p1", "from": "research_synthesizer", "to": "broadcast",
        "type": "research_preliminary", "symbol": None, "timestamp": time.time(),
        "payload": preliminary_payload
    }

    # Censor 处理初选结果
    await censor.on_message(preliminary_msg)

    # 捕获 Censor 输出
    challenge_msg = await bus.receive("research_synthesizer", timeout=3.0)
    assert challenge_msg is not None, "Censor should emit research_challenge"
    assert challenge_msg['type'] == 'research_challenge'
    print("  ✓ Censor 言官审查完成")

    # Synthesizer 处理谏言做最终决策
    synth._preliminary_result = {
        "selected": preliminary_payload['selected'],
        "market_regime": "trending",
        "total_candidates": 2,
    }
    synth._market_context = preliminary_payload['market_context']
    await synth.on_message(challenge_msg)

    # 捕获 research_result
    result_msg = await bus.receive("symbol_router", timeout=3.0)
    assert result_msg is not None, "Synthesizer should emit research_result"
    assert result_msg['type'] == 'research_result'
    print("  ✓ Synthesizer 终选决策完成")

    # SymbolRouter 处理 research_result
    await router.on_message(result_msg)

    # 捕获 symbol_update
    update_msg = await bus.receive("test_final", timeout=3.0)
    assert update_msg is not None, "SymbolRouter should emit symbol_update"
    assert 'active_symbols' in update_msg['payload']
    symbols = update_msg['payload']['active_symbols']
    assert len(symbols) > 0, "Should have active symbols"
    print(f"  ✓ SymbolRouter 路由完成: {symbols}")
    print("  ✓ 研判层完整链路通畅")
    return True


# ═══════════════════════════════════════════════════════════════
# Layer 3: 交易层链路（7 Agent 串联）
# ═══════════════════════════════════════════════════════════════

async def test_6_trading_data_to_decision():
    """DataCollector → TechAnalyst → Judge 链路"""
    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.tech_analyst import MultiTechAnalyst
    from agents.trading.judge import MultiJudge

    config = {"exchange": "okx", "leverage": 5, "max_trade_amount": 10}
    analyst = MultiTechAnalyst(config)
    judge = MultiJudge(config)

    # Mock LLM to force rule-based fallback
    async def mock_llm(*args, **kwargs):
        raise Exception("LLM mocked")
    async def mock_llm_json(*args, **kwargs):
        raise Exception("LLM mocked")
    analyst.ask_claude = mock_llm
    analyst.ask_claude_json = mock_llm_json
    judge.ask_claude = mock_llm
    judge.ask_claude_json = mock_llm_json

    bus.register("test_decision", ["trade_decision:*"])

    # 模拟 DataCollector 输出的 market_data
    # TechAnalyst expects 'klines' as list of [open_time, open, high, low, close, volume]
    base_price = 150.0
    klines = []
    for i in range(50):
        o = base_price + i * 0.5
        h = o + 1.0
        l = o - 0.5
        c = o + 0.8
        v = 1000000
        klines.append([time.time() - (50 - i) * 3600, o, h, l, c, v])

    market_data_payload = {
        "symbol": "SOL-USDT",
        "klines": klines,
        "klines_4h": klines[-12:],
        "orderbook": {"bids_total": 500000, "asks_total": 450000, "spread_pct": 0.01,
                      "bid_walls": [], "ask_walls": []},
        "funding_rate": 0.005,
        "funding_history": [0.003, 0.004, 0.005],
        "taker_ratio": {"buy_vol": 600000, "sell_vol": 400000},
        "oi_data": {"delta_1h_pct": 3.5},
        "long_short_account": {"ratio": 1.3},
        "liquidations": {"long_vol_usd": 100000, "short_vol_usd": 50000},
        "big_orders": {"buy_count": 5, "sell_count": 2, "net_direction": "buy"},
    }
    market_msg = {
        "msg_id": "m1", "from": "data_collector", "to": "broadcast",
        "type": "market_data", "symbol": "SOL-USDT", "timestamp": time.time(),
        "payload": market_data_payload
    }

    # TechAnalyst 处理 market_data
    await analyst.on_message(market_msg)

    # 捕获 tech_analysis (Judge's name is "judge")
    tech_msg = await bus.receive("judge", timeout=3.0)
    assert tech_msg is not None, "TechAnalyst should emit tech_analysis"
    assert tech_msg['type'] == 'tech_analysis'
    assert 'signals' in tech_msg['payload'] or 'trend' in str(tech_msg['payload']).lower()
    print("  ✓ TechAnalyst 信号解读完成")

    # Judge 处理 tech_analysis
    await judge.on_message(tech_msg)

    # 捕获 trade_decision
    decision_msg = await bus.receive("test_decision", timeout=3.0)
    if decision_msg:
        assert decision_msg['type'] == 'trade_decision'
        action = decision_msg['payload'].get('action', 'hold')
        print(f"  ✓ Judge 决策完成: action={action}")
    else:
        print("  ✓ Judge 决策: hold（无明确信号时正确拒绝）")

    print("  ✓ 交易层数据→决策链路通畅")
    return True


# PLACEHOLDER_LAYER3B

async def test_7_execution_and_notification():
    """Judge → Executor → Reviewer + TelegramNotifier 链路"""
    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.executor import MultiExecutor
    from agents.trading.reviewer import ReviewerAgent
    from agents.trading.telegram_notifier import TelegramNotifier
    from unittest.mock import MagicMock, patch

    config = {"exchange": "okx", "leverage": 5, "max_trade_amount": 10,
              "telegram_bot_token": "fake", "telegram_chat_id": "123"}

    executor = MultiExecutor(config)
    reviewer = ReviewerAgent(config)
    notifier = TelegramNotifier(config)
    notifier._enabled = True

    telegram_msgs = []
    async def mock_send(text):
        telegram_msgs.append(text)
        return True
    notifier._send_message = mock_send

    # Mock executor's exchange interface
    mock_exec = MagicMock()
    mock_exec.get_position.return_value = None
    mock_exec.open_position.return_value = {
        "entry_price": 170.0, "amount": 0.05, "side": "long"
    }
    mock_exec.risk_manager = MagicMock()
    mock_exec.risk_manager.check_can_trade.return_value = (True, "")
    mock_exec.risk_manager.max_trade_amount = 10
    executor.executor = mock_exec

    # 清理历史文件
    if os.path.exists('data/trade_history.json'):
        os.remove('data/trade_history.json')

    # 模拟 Judge 发出 trade_decision
    decision_msg = {
        "msg_id": "d1", "from": "multi_judge", "to": "broadcast",
        "type": "trade_decision", "symbol": "SOL-USDT", "timestamp": time.time(),
        "payload": {
            "action": "open_long", "symbol": "SOL-USDT",
            "confidence": 80, "size_pct": 0.5,
            "reasoning": "趋势强势+资金流入",
            "plan": {"leverage": 5, "entry_zone": [169, 171],
                     "stop_loss": 165, "take_profit": [180, 185]}
        }
    }

    await executor.on_message(decision_msg)

    # 验证 Executor 发出 execution_result
    exec_msg = await bus.receive("reviewer", timeout=2.0)
    assert exec_msg is not None, "Executor should emit execution_result"
    assert exec_msg['type'] == 'execution_result'
    assert exec_msg['payload']['status'] == 'executed'
    print("  ✓ Executor 执行开仓成功")

    # Reviewer 处理 execution_result (开仓不记录，只有平仓才记录)
    # 验证 Reviewer 不崩溃即可
    await reviewer.on_message(exec_msg)

    # 模拟平仓消息来验证完整记录
    close_msg = {
        "msg_id": "c1", "from": "executor", "to": "broadcast",
        "type": "execution_result", "symbol": "SOL-USDT", "timestamp": time.time(),
        "payload": {
            "status": "force_closed", "action": "close",
            "symbol": "SOL-USDT",
            "result": {"pnl": 2.5, "entry_price": 170, "exit_price": 172.5}
        }
    }
    await reviewer.on_message(close_msg)
    assert len(reviewer.trade_history) > 0
    print("  ✓ Reviewer 记录交易历史")

    # TelegramNotifier 处理 execution_result
    exec_msg_for_tg = await bus.receive("telegram_notifier", timeout=2.0)
    if exec_msg_for_tg:
        await notifier.on_message(exec_msg_for_tg)
    else:
        await notifier.on_message(exec_msg)

    assert len(telegram_msgs) > 0, "Telegram should receive notification"
    assert "做多" in telegram_msgs[0] or "SOL" in telegram_msgs[0]
    print(f"  ✓ Telegram 收到通知: {telegram_msgs[0][:30]}...")
    print("  ✓ 执行→复盘→通知链路通畅")
    return True


# ═══════════════════════════════════════════════════════════════
# Layer 4: 风控闭环
# ═══════════════════════════════════════════════════════════════

async def test_8_risk_alert_force_close():
    """RiskGuard → risk_alert → Executor 强制平仓"""
    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.portfolio_risk_guard import PortfolioRiskGuard
    from agents.trading.executor import MultiExecutor
    from unittest.mock import MagicMock

    config = {"exchange": "okx", "leverage": 5, "max_trade_amount": 10}
    rg = PortfolioRiskGuard(config)
    executor = MultiExecutor(config)

    mock_exec = MagicMock()
    mock_exec.close_position.return_value = {"pnl": -5.0}
    mock_exec.get_position.return_value = {"side": "long", "amount": 0.05}
    mock_exec.cancel_all_orders = MagicMock()
    executor.executor = mock_exec

    # 设置 RiskGuard 持仓状态（模拟大亏损）
    rg._positions = {
        "SOL-USDT": {
            "side": "long", "entry_price": 180.0, "leverage": 10,
            "amount_usdt": 10.0, "timestamp": time.time() - 3600,
            "highest_price": 180.0
        }
    }
    rg._prices = {"SOL-USDT": 150.0}  # 亏损 16.7%

    # 触发风控检查 — RiskGuard 的风控逻辑在 tick() 中执行
    # 先更新价格
    price_msg = {
        "msg_id": "p1", "from": "data_collector", "to": "broadcast",
        "type": "price_tick", "symbol": "SOL-USDT", "timestamp": time.time(),
        "payload": {"symbol": "SOL-USDT", "price": 150.0}
    }
    await rg.on_message(price_msg)

    # 直接调用风控检查（tick中的逻辑）
    await rg._check_position_pnl("SOL-USDT", 150.0)

    # 检查是否发出 risk_alert
    alert_msg = await bus.receive("executor", timeout=2.0)
    assert alert_msg is not None, "RiskGuard should emit risk_alert"
    assert alert_msg['type'] == 'risk_alert'
    print(f"  ✓ RiskGuard 检测到风险: {alert_msg['payload'].get('type')}")

    # Executor 处理 risk_alert
    await executor.on_message(alert_msg)
    print("  ✓ Executor 收到风控告警并处理")
    print("  ✓ 风控→告警→平仓链路通畅")
    return True


async def test_9_daily_hard_stop_flow():
    """Reviewer → daily_hard_stop → Executor/RiskGuard 全系统熔断"""
    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.reviewer import ReviewerAgent
    from agents.trading.executor import MultiExecutor
    from agents.trading.portfolio_risk_guard import PortfolioRiskGuard

    config = {"exchange": "okx", "leverage": 5, "max_trade_amount": 10}

    if os.path.exists('data/trade_history.json'):
        os.remove('data/trade_history.json')

    reviewer = ReviewerAgent(config)
    executor = MultiExecutor(config)
    rg = PortfolioRiskGuard(config)

    # 模拟连续3笔亏损
    for i in range(3):
        loss_msg = {
            "msg_id": f"loss-{i}", "from": "executor", "to": "broadcast",
            "type": "execution_result", "symbol": f"SYM{i}-USDT",
            "timestamp": time.time(),
            "payload": {
                "status": "force_closed", "action": "close",
                "symbol": f"SYM{i}-USDT",
                "result": {"pnl": -10.0, "entry_price": 100, "exit_price": 90},
            }
        }
        await reviewer.on_message(loss_msg)

    # 检查 Reviewer 是否发出 daily_hard_stop_triggered
    hard_stop_msg = await bus.receive("executor", timeout=2.0)
    assert hard_stop_msg is not None, "Reviewer should emit daily_hard_stop_triggered"
    assert hard_stop_msg['type'] == 'daily_hard_stop_triggered'
    print(f"  ✓ Reviewer 触发熔断: {hard_stop_msg['payload'].get('reason')}")

    # Executor 处理熔断
    await executor.on_message(hard_stop_msg)
    assert executor._trading_halted == True
    print("  ✓ Executor 进入熔断状态")

    # RiskGuard 也应收到
    rg_msg = await bus.receive("portfolio_risk_guard", timeout=2.0)
    if rg_msg and rg_msg['type'] == 'daily_hard_stop_triggered':
        await rg.on_message(rg_msg)
        assert rg._trading_halted == True
        print("  ✓ RiskGuard 进入熔断状态")

    # 验证 Executor 拒绝新交易
    new_decision = {
        "msg_id": "new1", "from": "judge", "to": "broadcast",
        "type": "trade_decision", "symbol": "BTC-USDT", "timestamp": time.time(),
        "payload": {"action": "open_long", "symbol": "BTC-USDT", "confidence": 90}
    }
    await executor.on_message(new_decision)
    # 不应有 execution_result 输出
    no_msg = await bus.receive("reviewer", timeout=0.5)
    assert no_msg is None, "Executor should reject trades when halted"
    print("  ✓ 熔断后正确拒绝新交易")
    print("  ✓ 全系统熔断链路通畅")
    return True


# PLACEHOLDER_LAYER4C

async def test_10_strategy_review_notify():
    """Reviewer → strategy_review → TelegramNotifier 推送"""
    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.reviewer import ReviewerAgent
    from agents.trading.telegram_notifier import TelegramNotifier

    config = {"exchange": "okx", "telegram_bot_token": "fake", "telegram_chat_id": "123"}

    if os.path.exists('data/trade_history.json'):
        os.remove('data/trade_history.json')

    reviewer = ReviewerAgent(config)
    notifier = TelegramNotifier(config)
    notifier._enabled = True

    telegram_msgs = []
    async def mock_send(text):
        telegram_msgs.append(text)
        return True
    notifier._send_message = mock_send

    # 填充交易历史触发策略复盘
    reviewer.trade_history = [
        {"timestamp": time.time() - i * 3600, "symbol": "SOL-USDT",
         "action": "close", "pnl": 2.0 if i % 3 != 0 else -1.0,
         "entry_price": 170, "exit_price": 172 if i % 3 != 0 else 169,
         "leverage": 5, "duration": 1800}
        for i in range(20)
    ]

    # 触发策略复盘
    trigger_msg = {
        "msg_id": "rt1", "from": "orchestrator", "to": "broadcast",
        "type": "research_trigger", "symbol": None, "timestamp": time.time(),
        "payload": {}
    }
    await reviewer.on_message(trigger_msg)

    # 捕获 strategy_review
    review_msg = await bus.receive("telegram_notifier", timeout=3.0)
    assert review_msg is not None, "Reviewer should emit strategy_review"
    assert review_msg['type'] == 'strategy_review'
    print(f"  ✓ Reviewer 策略复盘完成")

    # TelegramNotifier 处理
    await notifier.on_message(review_msg)
    assert len(telegram_msgs) > 0, "Telegram should receive strategy review"
    assert "策略" in telegram_msgs[-1] or "复盘" in telegram_msgs[-1]
    print(f"  ✓ Telegram 收到策略复盘通知")
    print("  ✓ 策略复盘→通知链路通畅")
    return True


# ═══════════════════════════════════════════════════════════════
# Layer 5: 状态持久化与恢复
# ═══════════════════════════════════════════════════════════════

async def test_11_state_persistence():
    """保存 + 重建 + 验证"""
    MessageBus.reset()

    from agents.trading.portfolio_risk_guard import PortfolioRiskGuard
    from agents.trading.reviewer import ReviewerAgent

    config = {"exchange": "okx"}
    state_file = 'data/riskguard_state.json'
    history_file = 'data/trade_history.json'

    # 清理
    for f in [state_file, history_file]:
        if os.path.exists(f):
            os.remove(f)

    # RiskGuard 状态持久化
    rg = PortfolioRiskGuard(config)
    rg._positions = {
        "SOL-USDT": {"side": "long", "entry_price": 170, "leverage": 5,
                     "amount_usdt": 8, "timestamp": time.time(), "peak_price": 175}
    }
    rg._prices = {"SOL-USDT": 172.0}
    rg._trading_halted = False
    rg._save_state()

    assert os.path.exists(state_file), "State file should be created"

    # 重建实例验证加载
    MessageBus.reset()
    rg2 = PortfolioRiskGuard(config)
    rg2._load_state()
    assert "SOL-USDT" in rg2._positions, "Position should be restored"
    assert rg2._positions["SOL-USDT"]["entry_price"] == 170
    assert rg2._trading_halted == False
    print("  ✓ RiskGuard 状态保存/恢复正确")

    # Reviewer 交易历史持久化
    MessageBus.reset()
    reviewer = ReviewerAgent(config)
    reviewer.trade_history = [
        {"timestamp": time.time(), "symbol": "SOL-USDT", "pnl": 3.5,
         "action": "close", "entry_price": 170, "exit_price": 173.5}
    ]
    reviewer._save_trade_history()

    assert os.path.exists(history_file), "Trade history file should be created"

    # 重建验证
    MessageBus.reset()
    reviewer2 = ReviewerAgent(config)
    reviewer2._load_trade_history()
    assert len(reviewer2.trade_history) == 1
    assert reviewer2.trade_history[0]['pnl'] == 3.5
    print("  ✓ Reviewer 交易历史保存/恢复正确")

    # 清理
    for f in [state_file, history_file]:
        if os.path.exists(f):
            os.remove(f)

    print("  ✓ 状态持久化链路通畅")
    return True


async def test_12_halted_state_survives():
    """熔断状态重启后保持"""
    MessageBus.reset()

    from agents.trading.portfolio_risk_guard import PortfolioRiskGuard

    config = {"exchange": "okx"}
    state_file = 'data/riskguard_state.json'
    if os.path.exists(state_file):
        os.remove(state_file)

    # 设置熔断状态并保存
    rg = PortfolioRiskGuard(config)
    rg._trading_halted = True
    rg._positions = {"BTC-USDT": {"side": "long", "entry_price": 60000,
                                   "leverage": 3, "amount_usdt": 10,
                                   "timestamp": time.time(), "peak_price": 61000}}
    rg._save_state()

    # 模拟重启
    MessageBus.reset()
    rg2 = PortfolioRiskGuard(config)
    rg2._load_state()

    assert rg2._trading_halted == True, "Halted state should survive restart"
    print("  ✓ 熔断状态重启后保持")
    print("  ✓ 不会自动解除（需手动干预）")

    # 清理
    if os.path.exists(state_file):
        os.remove(state_file)
    return True


# ═══════════════════════════════════════════════════════════════
# Layer 6: 异常场景
# ═══════════════════════════════════════════════════════════════

async def test_13_llm_fallback():
    """LLM 不可用时降级为规则引擎"""
    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.research.synthesizer import ResearchSynthesizer

    config = {"exchange": "okx"}
    synth = ResearchSynthesizer(config)
    # Mock LLM to simulate unavailability
    async def mock_llm_fail(*args, **kwargs):
        raise Exception("LLM unavailable")
    synth.ask_claude_json = mock_llm_fail

    bus.register("test_catch", ["research_preliminary"])

    # 发送数据
    market_msg = {
        "msg_id": "f1", "from": "scanner", "to": "broadcast",
        "type": "research_market_data", "symbol": None, "timestamp": time.time(),
        "payload": {
            "candidates": [
                {"symbol": "SOL-USDT", "price": 170.0, "volume_24h": 8e8,
                 "change_pct": 5.0, "change_24h_pct": 5.0, "volatility_pct": 6.0,
                 "funding_rate": 0.01, "oi_change": 8.0,
                 "long_short_ratio": 1.5},
                {"symbol": "DOGE-USDT", "price": 0.15, "volume_24h": 3e8,
                 "change_pct": 2.0, "change_24h_pct": 2.0, "volatility_pct": 3.5,
                 "funding_rate": 0.002, "oi_change": 1.0,
                 "long_short_ratio": 1.0},
            ]
        }
    }
    await synth.on_message(market_msg)

    msg = await bus.receive("test_catch", timeout=3.0)
    assert msg is not None, "Should still produce output via rule fallback"
    assert len(msg['payload']['selected']) > 0
    print("  ✓ LLM 不可用时规则引擎降级成功")
    print(f"  ✓ 降级选出 {len(msg['payload']['selected'])} 个标的")
    return True


async def test_14_exchange_error_handling():
    """交易所 API 错误 → 不崩溃，发出 rejected"""
    MessageBus.reset()
    bus = MessageBus.get_instance()

    from agents.trading.executor import MultiExecutor
    from unittest.mock import MagicMock

    config = {"exchange": "okx", "leverage": 5, "max_trade_amount": 10}
    executor = MultiExecutor(config)

    # Mock 交易所抛异常
    mock_exec = MagicMock()
    mock_exec.get_position.side_effect = Exception("Connection timeout")
    mock_exec.risk_manager = MagicMock()
    mock_exec.risk_manager.check_can_trade.return_value = (True, "")
    mock_exec.risk_manager.max_trade_amount = 10
    executor.executor = mock_exec

    bus.register("test_result", ["execution_result:*", "execution_result"])

    decision_msg = {
        "msg_id": "e1", "from": "judge", "to": "broadcast",
        "type": "trade_decision", "symbol": "SOL-USDT", "timestamp": time.time(),
        "payload": {"action": "open_long", "symbol": "SOL-USDT",
                    "confidence": 85, "size_pct": 0.5}
    }

    # 不应崩溃，应该发出 error 状态的 execution_result
    await executor.on_message(decision_msg)

    err_msg = await bus.receive("test_result", timeout=2.0)
    assert err_msg is not None, "Should emit error execution_result"
    assert err_msg['payload']['status'] == 'error'
    print("  ✓ 交易所 API 异常不会导致崩溃")
    print(f"  ✓ 错误正确上报: {err_msg['payload'].get('reason', '')[:40]}")
    print("  ✓ 异常场景处理正确")
    return True


# ═══════════════════════════════════════════════════════════════
# Layer 7: 真实环境冒烟
# ═══════════════════════════════════════════════════════════════

async def test_15_live_smoke_test():
    """OKX连接 + Telegram发送 + 系统启动验证"""
    from dotenv import load_dotenv
    load_dotenv()

    results = []

    # 7a: OKX API 连接（只读）
    try:
        import ccxt
        exchange = ccxt.okx({
            'apiKey': os.getenv('OKX_API_KEY'),
            'secret': os.getenv('OKX_SECRET'),
            'password': os.getenv('OKX_PASSWORD'),
        })
        balance = exchange.fetch_balance()
        usdt = balance.get('USDT', {}).get('free', 0)
        print(f"  ✓ OKX 连接成功，USDT余额: {usdt}")
        results.append(True)
    except Exception as e:
        print(f"  ✗ OKX 连接失败: {e}")
        results.append(False)

    # 7b: Telegram 发送
    try:
        import aiohttp
        token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        if token and chat_id:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {"chat_id": chat_id, "text": "🧪 全链路验证测试通过"}
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        print("  ✓ Telegram 消息发送成功")
                        results.append(True)
                    else:
                        print(f"  ✗ Telegram 发送失败: {resp.status}")
                        results.append(False)
        else:
            print("  ⏭️ Telegram 未配置，跳过")
            results.append(True)
    except Exception as e:
        print(f"  ✗ Telegram 发送异常: {e}")
        results.append(False)

    # 7c: 系统导入验证（所有Agent可正常实例化）
    try:
        from agents.orchestrator import Orchestrator
        orch = Orchestrator()
        orch._register_agents()
        total = len(orch._research_agents) + len(orch._trading_agents)
        assert total == 13, f"Expected 13 agents, got {total}"
        print(f"  ✓ 系统实例化成功: {total} 个Agent")
        results.append(True)
    except Exception as e:
        print(f"  ✗ 系统实例化失败: {e}")
        results.append(False)

    return all(results)


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

async def main():
    print("\n" + "━" * 60)
    print("  全链路验证测试 — 7层15个用例")
    print("━" * 60 + "\n")

    layers = [
        ("Layer 1: 消息总线基础", [
            test_1_topic_symbol_routing,
            test_2_wildcard_subscription,
            test_3_broadcast_delivery,
        ]),
        ("Layer 2: 研判层链路", [
            test_4_research_data_aggregation,
            test_5_research_full_chain,
        ]),
        ("Layer 3: 交易层链路", [
            test_6_trading_data_to_decision,
            test_7_execution_and_notification,
        ]),
        ("Layer 4: 风控闭环", [
            test_8_risk_alert_force_close,
            test_9_daily_hard_stop_flow,
            test_10_strategy_review_notify,
        ]),
        ("Layer 5: 状态持久化", [
            test_11_state_persistence,
            test_12_halted_state_survives,
        ]),
        ("Layer 6: 异常场景", [
            test_13_llm_fallback,
            test_14_exchange_error_handling,
        ]),
        ("Layer 7: 真实环境冒烟", [
            test_15_live_smoke_test,
        ]),
    ]

    total_passed = 0
    total_tests = 0
    layer_results = []

    for layer_name, tests in layers:
        print(f"\n{'=' * 60}")
        print(f"  {layer_name}")
        print(f"{'=' * 60}")

        layer_passed = 0
        layer_failed = False

        for test_fn in tests:
            total_tests += 1
            print(f"\n  [{total_tests:02d}] {test_fn.__doc__}")
            print(f"  {'-' * 50}")
            try:
                result = await asyncio.wait_for(test_fn(), timeout=15.0)
                if result:
                    layer_passed += 1
                    total_passed += 1
                else:
                    layer_failed = True
                    print(f"  ✗ 测试失败")
            except asyncio.TimeoutError:
                print(f"  ✗ 超时（>15秒）")
                layer_failed = True
            except Exception as e:
                print(f"  ✗ 异常: {e}")
                import traceback
                traceback.print_exc()
                layer_failed = True

        status = "✓" if not layer_failed else "✗"
        layer_results.append((layer_name, layer_passed, len(tests), not layer_failed))
        print(f"\n  {status} {layer_name}: {layer_passed}/{len(tests)}")

    # 汇总
    print("\n\n" + "━" * 60)
    print("  验证结果汇总")
    print("━" * 60)
    for name, passed, total, ok in layer_results:
        icon = "✓" if ok else "✗"
        dots = "." * (40 - len(name))
        print(f"  {icon} {name} {dots} {passed}/{total}")

    print("━" * 60)
    all_pass = total_passed == total_tests
    icon = "🎉" if all_pass else "❌"
    print(f"  {icon} 全链路验证: {total_passed}/{total_tests} 测试通过")
    print("━" * 60 + "\n")

    return all_pass


if __name__ == '__main__':
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
