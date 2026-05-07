#!/usr/bin/env python3
"""多Agent系统集成测试 - 验证消息流水线（无需交易所/LLM连接）"""

import asyncio
import sys
import time

sys.path.insert(0, '.')

from agents.message_bus import MessageBus


async def test_message_flow():
    """测试完整消息流: market_data → tech_analysis → trade_decision"""
    print("=" * 60)
    print("多Agent系统集成测试")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    bus.register("data_collector", [])
    bus.register("tech_analyst", ["market_data"])
    bus.register("judge", ["tech_analysis", "sentiment_analysis", "prediction"])
    bus.register("executor", ["trade_decision"])
    bus.register("risk_guard", ["execution_result", "market_data"])

    print("\n[1] 测试Agent注册和消息路由...")

    await bus.publish("data_collector", "market_data", {
        "symbol": "BTC-USDT",
        "interval": "1h",
        "klines": [[1715100000000, 60000, 60500, 59800, 60200, 1000]] * 50,
        "funding_rate": 0.0001,
        "latest_price": 60200,
    })

    msg = await bus.receive("tech_analyst", timeout=1.0)
    assert msg is not None, "tech_analyst 未收到 market_data"
    assert msg['type'] == 'market_data'
    print("  ✓ DataCollector → TechAnalyst 消息路由正常")

    msg_risk = await bus.receive("risk_guard", timeout=1.0)
    assert msg_risk is not None, "risk_guard 未收到 market_data"
    print("  ✓ DataCollector → RiskGuard 消息路由正常")

    print("\n[2] 测试分析结果→裁判路由...")

    await bus.publish("tech_analyst", "tech_analysis", {
        "symbol": "BTC-USDT",
        "rule_signal": {"entry_long": 1, "entry_short": 0, "exit_long": 0, "exit_short": 0},
        "indicators": {"price": 60200, "rsi": 55, "ma_fast": 60100, "ma_slow": 59800},
        "llm_analysis": {
            "direction": "bullish",
            "confidence": 72,
            "pattern": None,
            "reasoning": "LLM不可用，基于MA排列的规则判断"
        }
    })

    msg = await bus.receive("judge", timeout=1.0)
    assert msg is not None, "judge 未收到 tech_analysis"
    assert msg['type'] == 'tech_analysis'
    print("  ✓ TechAnalyst → Judge 消息路由正常")

    print("\n[3] 测试决策→执行路由...")

    await bus.publish("judge", "trade_decision", {
        "action": "open_long",
        "confidence": 70,
        "size_pct": 0.5,
        "reasoning": "规则引擎降级：技术面做多信号",
        "symbol": "BTC-USDT",
        "timestamp": time.time(),
    })

    msg = await bus.receive("executor", timeout=1.0)
    assert msg is not None, "executor 未收到 trade_decision"
    assert msg['payload']['action'] == 'open_long'
    print("  ✓ Judge → Executor 消息路由正常")

    print("\n[4] 测试执行结果→风控路由...")

    await bus.publish("executor", "execution_result", {
        "status": "executed",
        "action": "open_long",
        "symbol": "BTC-USDT",
        "amount": 5.0,
    })

    msg = await bus.receive("risk_guard", timeout=1.0)
    assert msg is not None, "risk_guard 未收到 execution_result"
    print("  ✓ Executor → RiskGuard 消息路由正常")

    print("\n[5] 测试广播隔离（发送者不收到自己的消息）...")

    await bus.publish("tech_analyst", "market_data", {"test": True})
    msg = await bus.receive("tech_analyst", timeout=0.5)
    assert msg is None, "tech_analyst 不应收到自己发的 market_data"
    print("  ✓ 广播隔离正常（发送者不收自己的消息）")

    print("\n[6] 测试定向消息...")

    await bus.publish("judge", "direct_msg", {"info": "test"}, to="executor")
    msg = await bus.receive("executor", timeout=1.0)
    assert msg is not None, "executor 未收到定向消息"
    assert msg['payload']['info'] == 'test'
    print("  ✓ 定向消息投递正常")

    print("\n" + "=" * 60)
    print("所有集成测试通过！消息流水线工作正常。")
    print("=" * 60)
    print("\n流水线验证:")
    print("  DataCollector →[market_data]→ TechAnalyst, RiskGuard")
    print("  TechAnalyst →[tech_analysis]→ Judge")
    print("  Judge →[trade_decision]→ Executor")
    print("  Executor →[execution_result]→ RiskGuard")
    print("  广播隔离 ✓ | 定向消息 ✓")


async def test_judge_rule_fallback():
    """测试Judge在无LLM时的规则降级"""
    print("\n" + "=" * 60)
    print("Judge规则降级测试")
    print("=" * 60)

    from agents.judge import JudgeAgent

    MessageBus.reset()
    config = {"exchange": "okx", "symbol": "BTC-USDT", "max_trade_amount": 10}
    judge = JudgeAgent(config)

    tech_data = {
        "symbol": "BTC-USDT",
        "rule_signal": {"entry_long": 1, "entry_short": 0, "exit_long": 0, "exit_short": 0},
        "indicators": {"price": 60200, "rsi": 55, "ma_fast": 60100, "ma_slow": 59800},
        "llm_analysis": {"direction": "bullish", "confidence": 72}
    }

    result = judge._rule_fallback(tech_data)
    assert result['action'] == 'open_long'
    assert result['confidence'] == 60
    print("  ✓ 做多信号 → action=open_long")

    tech_data['rule_signal'] = {"entry_long": 0, "entry_short": 1, "exit_long": 0, "exit_short": 0}
    result = judge._rule_fallback(tech_data)
    assert result['action'] == 'open_short'
    print("  ✓ 做空信号 → action=open_short")

    tech_data['rule_signal'] = {"entry_long": 0, "entry_short": 0, "exit_long": 0, "exit_short": 0}
    result = judge._rule_fallback(tech_data)
    assert result['action'] == 'hold'
    print("  ✓ 无信号 → action=hold")

    print("\nJudge规则降级测试通过！")


if __name__ == '__main__':
    asyncio.run(test_message_flow())
    asyncio.run(test_judge_rule_fallback())
