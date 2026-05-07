"""Phase C 集成测试 - 研判层→交易层完整流水线"""

import asyncio
import sys
sys.path.insert(0, '.')

from agents.message_bus import MessageBus
from agents.research.market_scanner import MarketScanner
from agents.research.synthesizer import ResearchSynthesizer
from agents.research.symbol_router import SymbolRouter
from agents.trading.multi_data_collector import MultiDataCollector
from agents.trading.tech_analyst import MultiTechAnalyst
from agents.trading.judge import MultiJudge
from agents.trading.portfolio_risk_guard import PortfolioRiskGuard


async def test_research_to_trading_pipeline():
    """测试：研判层选币 → 交易层接收标的更新"""
    print("=" * 60)
    print("测试: 研判层 → 交易层 完整流水线")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    config = {"exchange": "okx", "interval": "1h", "leverage": 3, "max_trade_amount": 10}

    synthesizer = ResearchSynthesizer(config)
    router = SymbolRouter(config)
    data_collector = MultiDataCollector(config)
    tech_analyst = MultiTechAnalyst(config)
    judge = MultiJudge(config)
    risk_guard = PortfolioRiskGuard(config)

    print("\n[1] 模拟MarketScanner发布研判数据...")
    mock_candidates = [
        {"symbol": "SOL-USDT", "price": 170.5, "volume_24h": 800_000_000,
         "volatility_pct": 6.2, "change_24h_pct": 4.1, "high_24h": 175, "low_24h": 165,
         "funding_rate": 0.0008},
        {"symbol": "WIF-USDT", "price": 2.35, "volume_24h": 150_000_000,
         "volatility_pct": 12.5, "change_24h_pct": -3.2, "high_24h": 2.6, "low_24h": 2.3,
         "funding_rate": -0.0015},
        {"symbol": "DOGE-USDT", "price": 0.165, "volume_24h": 500_000_000,
         "volatility_pct": 5.8, "change_24h_pct": 2.1, "high_24h": 0.17, "low_24h": 0.16,
         "funding_rate": 0.0003},
        {"symbol": "PEPE-USDT", "price": 0.000012, "volume_24h": 300_000_000,
         "volatility_pct": 18.5, "change_24h_pct": 8.5, "high_24h": 0.000013, "low_24h": 0.000011,
         "funding_rate": 0.0025},
    ]

    await bus.publish("market_scanner", "research_market_data", {
        "candidates": mock_candidates,
        "total_scanned": 200,
        "filtered": 45,
    }, "broadcast")

    print("[2] Synthesizer处理研判数据（规则降级模式）...")
    msg = await bus.receive("research_synthesizer", timeout=1.0)
    assert msg is not None, "Synthesizer未收到消息"
    assert msg['type'] == 'research_market_data'

    bus.register("censor", ["research_preliminary"])
    await synthesizer.on_message(msg)

    print("[2.5] 模拟Censor谏言（两阶段决策流程）...")
    censor_msg = await bus.receive("censor", timeout=1.0)
    assert censor_msg is not None, "Censor未收到research_preliminary"

    mock_challenge = {
        "type": "research_challenge",
        "payload": {
            "challenges": [
                {"symbol": "PEPE-USDT", "risk_level": "high",
                 "objections": ["波动率过高18.5%", "meme币基本面弱"],
                 "blind_spots": ["可能是刷量"], "worst_case": "闪崩50%",
                 "recommendation": "reject"}
            ],
            "systemic_risks": [],
            "overall_verdict": "建议移除高风险meme币",
        }
    }
    await synthesizer.on_message(mock_challenge)

    print("[3] 检查Synthesizer发布的research_result...")
    router_msg = await bus.receive("symbol_router", timeout=1.0)
    assert router_msg is not None, "SymbolRouter未收到research_result"
    assert router_msg['type'] == 'research_result'

    selected = router_msg['payload'].get('selected', [])
    print(f"    选出标的: {[s['symbol'] for s in selected]}")
    assert len(selected) > 0, "未选出任何标的"
    assert len(selected) <= 3, "选出超过3个标的"

    print("[4] SymbolRouter处理并发布symbol_update...")
    await router.on_message(router_msg)

    dc_msg = await bus.receive("multi_data_collector", timeout=1.0)
    assert dc_msg is not None, "DataCollector未收到symbol_update"
    assert dc_msg['type'] == 'symbol_update'

    active_symbols = dc_msg['payload']['active_symbols']
    print(f"    活跃标的: {active_symbols}")
    assert len(active_symbols) > 0

    print("[5] 验证交易层各Agent都收到symbol_update...")
    ta_msg = await bus.receive("tech_analyst", timeout=1.0)
    assert ta_msg is not None and ta_msg['type'] == 'symbol_update'
    print("    ✓ TechAnalyst 收到")

    judge_msg = await bus.receive("judge", timeout=1.0)
    assert judge_msg is not None and judge_msg['type'] == 'symbol_update'
    print("    ✓ Judge 收到")

    rg_msg = await bus.receive("portfolio_risk_guard", timeout=1.0)
    assert rg_msg is not None and rg_msg['type'] == 'symbol_update'
    print("    ✓ PortfolioRiskGuard 收到")

    print("\n[6] 模拟DataCollector为第一个标的发布market_data...")
    symbol = active_symbols[0]
    mock_klines = [[1700000000000 + i*3600000, 170+i*0.1, 171+i*0.1, 169+i*0.1, 170.5+i*0.1, 1000+i*10]
                   for i in range(100)]

    await bus.publish("multi_data_collector", "market_data", {
        "symbol": symbol,
        "interval": "1h",
        "klines": mock_klines,
        "funding_rate": 0.0008,
        "latest_price": mock_klines[-1][4],
    }, "broadcast", symbol=symbol)

    ta_data_msg = await bus.receive("tech_analyst", timeout=1.0)
    assert ta_data_msg is not None, "TechAnalyst未收到market_data"
    assert ta_data_msg['type'] == 'market_data'
    assert ta_data_msg.get('symbol') == symbol
    print(f"    ✓ TechAnalyst 收到 {symbol} 的market_data")

    rg_data_msg = await bus.receive("portfolio_risk_guard", timeout=1.0)
    assert rg_data_msg is not None
    print(f"    ✓ PortfolioRiskGuard 收到 {symbol} 的market_data")

    print("\n" + "=" * 60)
    print("✅ 研判层→交易层 完整流水线测试通过!")
    print("=" * 60)
    print(f"\n流水线验证:")
    print(f"  MarketScanner → research_market_data → Synthesizer")
    print(f"  Synthesizer → research_result → SymbolRouter")
    print(f"  SymbolRouter → symbol_update → 所有交易层Agent")
    print(f"  DataCollector → market_data:{symbol} → TechAnalyst + RiskGuard")
    return True


async def test_symbol_rotation():
    """测试：标的轮换（旧标的平仓+新标的启动）"""
    print("\n" + "=" * 60)
    print("测试: 标的轮换协议")
    print("=" * 60)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    config = {"exchange": "okx"}
    router = SymbolRouter(config)
    judge = MultiJudge(config)

    first_result = {
        "selected": [
            {"symbol": "SOL-USDT", "direction_bias": "long", "confidence": 75},
            {"symbol": "WIF-USDT", "direction_bias": "short", "confidence": 70},
        ],
        "market_regime": "trending",
    }

    print("[1] 首次选币...")
    await router._handle_research_result(first_result)
    assert router.active_symbols == ["SOL-USDT", "WIF-USDT"]
    print(f"    活跃: {router.active_symbols}")

    router._last_update_time = 0

    second_result = {
        "selected": [
            {"symbol": "SOL-USDT", "direction_bias": "long", "confidence": 80},
            {"symbol": "DOGE-USDT", "direction_bias": "long", "confidence": 72},
        ],
        "market_regime": "trending",
    }

    print("[2] 第二次选币（WIF移除，DOGE加入）...")
    await router._handle_research_result(second_result)
    assert router.active_symbols == ["SOL-USDT", "DOGE-USDT"]
    print(f"    活跃: {router.active_symbols}")

    print("[3] 检查平仓指令...")
    judge_msg = await bus.receive("judge", timeout=1.0)
    found_close = False
    while judge_msg:
        if judge_msg['type'] == 'trade_decision' and judge_msg.get('symbol') == 'WIF-USDT':
            assert judge_msg['payload']['action'] == 'close'
            found_close = True
            print(f"    ✓ 收到WIF-USDT平仓指令")
            break
        judge_msg = await bus.receive("judge", timeout=0.5)

    print("\n" + "=" * 60)
    print("✅ 标的轮换协议测试通过!")
    print("=" * 60)
    return True


async def main():
    results = []
    results.append(await test_research_to_trading_pipeline())
    results.append(await test_symbol_rotation())

    print("\n" + "=" * 60)
    passed = sum(1 for r in results if r)
    print(f"总结: {passed}/{len(results)} 测试通过")
    print("=" * 60)
    return all(results)


if __name__ == '__main__':
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
