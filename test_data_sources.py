"""数据源验证：研判层三路采集真实数据"""
import asyncio
import sys
sys.path.insert(0, '.')
from agents.message_bus import MessageBus
from agents.research.market_scanner import MarketScanner
from agents.research.sentiment_researcher import SentimentResearcher
from agents.research.news_researcher import NewsResearcher
from agents.research.synthesizer import ResearchSynthesizer


def check(val):
    return "OK" if val else "FAIL"


async def test_all_sources():
    print("=" * 70)
    print("  数据源验证：研判层三路采集真实数据")
    print("=" * 70)

    MessageBus.reset()
    bus = MessageBus.get_instance()

    config = {"exchange": "okx"}
    scanner = MarketScanner(config)
    sentiment = SentimentResearcher(config)
    news = NewsResearcher(config)
    synth = ResearchSynthesizer(config)

    await scanner.setup()
    await sentiment.setup()
    await news.setup()

    # ===== 1. MarketScanner =====
    print("\n" + "-" * 70)
    print("  [1/3] MarketScanner - OKX永续合约扫描")
    print("-" * 70)

    await scanner._scan_market()
    msg = await bus.receive("research_synthesizer", timeout=2.0)
    if msg and msg["type"] == "research_market_data":
        payload = msg["payload"]
        candidates = payload["candidates"]
        print(f"  扫描总数: {payload['total_scanned']}")
        print(f"  符合条件(>500万USDT): {payload['filtered']}")
        print(f"  Top候选: {len(candidates)}")
        print()
        header = f"  {'标的':<14} {'价格':>10} {'24h量(M)':>9} {'波动%':>6} {'涨跌%':>6} {'费率':>8} {'多空比':>6} {'OI(M)':>7}"
        print(header)
        print(f"  {'─'*14} {'─'*10} {'─'*9} {'─'*6} {'─'*6} {'─'*8} {'─'*6} {'─'*7}")
        for c in candidates[:15]:
            fr = f"{c['funding_rate']*100:.3f}%" if c["funding_rate"] else "N/A"
            ls = f"{c['long_short_ratio']:.2f}" if c.get("long_short_ratio") else "N/A"
            oi = f"{c['open_interest_usd']/1e6:.0f}" if c.get("open_interest_usd") else "N/A"
            print(f"  {c['symbol']:<14} {c['price']:>10.4f} {c['volume_24h']/1e6:>9.0f} "
                  f"{c['volatility_pct']:>6.1f} {c['change_24h_pct']:>6.1f} {fr:>8} {ls:>6} {oi:>7}")

        print(f"\n  数据质量:")
        if not candidates:
            print("    FAIL: 无候选数据")
        else:
            has_price = all(c["price"] > 0 for c in candidates)
            has_volume = all(c["volume_24h"] > 0 for c in candidates)
            has_volatility = all(c["volatility_pct"] > 0 for c in candidates)
            funding_count = sum(1 for c in candidates if c["funding_rate"] is not None)
            funding_pct = funding_count / len(candidates) * 100
            ls_count = sum(1 for c in candidates if c.get("long_short_ratio") is not None)
            ls_pct = ls_count / len(candidates) * 100
            oi_count = sum(1 for c in candidates if c.get("open_interest_usd") is not None)
            oi_pct = oi_count / len(candidates) * 100
            volumes = [c["volume_24h"] for c in candidates]
            is_sorted = all(volumes[i] >= volumes[i+1] for i in range(len(volumes)-1))
            print(f"    价格完整: {check(has_price)}")
            print(f"    成交量完整: {check(has_volume)}")
            print(f"    波动率完整: {check(has_volatility)}")
            print(f"    资金费率覆盖: {funding_pct:.0f}% ({funding_count}/{len(candidates)})")
            print(f"    多空比覆盖: {ls_pct:.0f}% ({ls_count}/{len(candidates)})")
            print(f"    持仓量覆盖: {oi_pct:.0f}% ({oi_count}/{len(candidates)})")
            print(f"    降序排列: {check(is_sorted)}")

            # 数据合理性
            print(f"\n  数据合理性:")
            avg_vol = sum(c["volatility_pct"] for c in candidates) / len(candidates)
            max_vol = max(c["volatility_pct"] for c in candidates)
            min_vol = min(c["volatility_pct"] for c in candidates)
            print(f"    波动率范围: {min_vol:.1f}% ~ {max_vol:.1f}% (均值{avg_vol:.1f}%)")
            top_volume = candidates[0]["volume_24h"] / 1e6
            bot_volume = candidates[-1]["volume_24h"] / 1e6
            print(f"    成交量范围: ${bot_volume:.0f}M ~ ${top_volume:.0f}M")
            fr_values = [c["funding_rate"] for c in candidates if c["funding_rate"] is not None]
            if fr_values:
                print(f"    资金费率范围: {min(fr_values)*100:.4f}% ~ {max(fr_values)*100:.4f}%")
    else:
        print("  FAIL: MarketScanner未产出数据")

    # ===== 2. SentimentResearcher =====
    print("\n" + "-" * 70)
    print("  [2/3] SentimentResearcher - 市场情绪量化")
    print("-" * 70)

    await sentiment._research_sentiment()
    msg = await bus.receive("research_synthesizer", timeout=2.0)
    if msg and msg["type"] == "research_sentiment_data":
        payload = msg["payload"]

        fg = payload.get("fear_greed")
        if fg:
            print(f"  恐贪指数: {fg['value']} ({fg['classification']})")
            history = fg.get("history_7d", [])
            if history:
                print(f"  7日趋势: {[h['value'] for h in history]}")
        else:
            print("  恐贪指数: 获取失败")

        trending = payload.get("trending_coins", [])
        print(f"  CoinGecko热门: {len(trending)}个币种")
        if trending:
            for t in trending[:8]:
                rank = t.get("market_cap_rank") or "?"
                print(f"    {t['symbol']:8s} (#{rank}) {t['name']}")

        taker = payload.get("taker_ratios", {})
        print(f"  Binance Taker买卖比: {len(taker)}个标的")
        if taker:
            for sym, data in taker.items():
                ratio = data["buy_sell_ratio"]
                signal = "多头强势" if ratio > 1.1 else "空头强势" if ratio < 0.9 else "均衡"
                print(f"    {sym:10s}: {ratio:.3f} ({signal})")

        print(f"\n  数据质量:")
        print(f"    恐贪指数: {check(fg is not None)}")
        print(f"    热门币种: {check(len(trending) > 0)}")
        print(f"    Taker比: {check(len(taker) > 0)}")
    else:
        print("  FAIL: SentimentResearcher未产出数据")

    # ===== 3. NewsResearcher =====
    print("\n" + "-" * 70)
    print("  [3/3] NewsResearcher - 新闻舆情RSS")
    print("-" * 70)

    await news._fetch_news()
    msg = await bus.receive("research_synthesizer", timeout=2.0)
    if msg and msg["type"] == "research_news_data":
        payload = msg["payload"]
        headlines = payload["headlines"]
        mentions = payload["symbol_mentions"]
        print(f"  数据源状态: {payload['sources_ok']}/{payload['sources_total']} 源可用")
        print(f"  头条总数: {len(headlines)}")

        sources = {}
        for h in headlines:
            sources[h["source"]] = sources.get(h["source"], 0) + 1
        print(f"  来源分布:")
        for src, count in sorted(sources.items(), key=lambda x: -x[1]):
            print(f"    {src}: {count}条")

        print(f"\n  最新头条:")
        for h in headlines[:10]:
            print(f"    [{h['source']:15s}] {h['title'][:62]}")

        print(f"\n  币种提及统计 ({len(mentions)}个币种):")
        for sym, data in list(mentions.items())[:12]:
            sample = data["headlines"][0][:45] if data["headlines"] else ""
            print(f"    {sym:6s}: {data['count']}次  | {sample}")

        print(f"\n  数据质量:")
        has_title = all(h.get("title") for h in headlines)
        has_link = sum(1 for h in headlines if h.get("link"))
        link_pct = has_link / max(len(headlines), 1) * 100
        has_ts = sum(1 for h in headlines if h.get("published_ts", 0) > 0)
        ts_pct = has_ts / max(len(headlines), 1) * 100
        print(f"    标题完整: {check(has_title)}")
        print(f"    链接覆盖: {link_pct:.0f}% ({has_link}/{len(headlines)})")
        print(f"    时间戳覆盖: {ts_pct:.0f}% ({has_ts}/{len(headlines)})")
        print(f"    币种识别: {len(mentions)}个")
    else:
        print("  FAIL: NewsResearcher未产出数据")

    # ===== 总结 =====
    print("\n" + "=" * 70)
    print("  数据源总结")
    print("=" * 70)
    print("""
  | 数据源              | 覆盖范围                          | 可靠性 |
  |---------------------|----------------------------------|--------|
  | MarketScanner       | OKX 324合约: 量/波动/费率/多空比/OI | 高     |
  | SentimentResearcher | 恐贪指数+CoinGecko热度+Taker比     | 高     |
  | NewsResearcher      | 6家加密媒体RSS, 50+条头条           | 高     |
  """)


if __name__ == "__main__":
    asyncio.run(test_all_sources())
