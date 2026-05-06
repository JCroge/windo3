#!/usr/bin/env python3
"""WebSocket实时套利监控"""

import asyncio
import json
from datetime import datetime
import websockets

class WebSocketMonitor:
    def __init__(self, symbols):
        self.symbols = symbols
        self.prices = {}  # {symbol: {'binance': {bid, ask}, 'okx': {bid, ask}}}
        self.opportunities = []

    async def binance_stream(self):
        """币安WebSocket"""
        # bookTicker提供最优bid/ask
        streams = [f"{s.lower().replace('/', '')}@bookTicker" for s in self.symbols]
        url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"

        async with websockets.connect(url) as ws:
            print("✅ 币安WebSocket已连接")
            async for msg in ws:
                data = json.loads(msg)
                if 'data' in data:
                    self._process_binance(data['data'])

    def _process_binance(self, data):
        """处理币安数据"""
        symbol = data['s'].replace('USDT', '/USDT')
        bid = float(data['b'])
        ask = float(data['a'])

        if symbol not in self.prices:
            self.prices[symbol] = {}
        self.prices[symbol]['binance'] = {'bid': bid, 'ask': ask, 'time': datetime.now()}

        self._check_opportunity(symbol)

    async def okx_stream(self):
        """OKX WebSocket"""
        url = "wss://ws.okx.com:8443/ws/v5/public"

        async with websockets.connect(url) as ws:
            # 订阅bookTicker
            args = [{"channel": "bbo-tbt", "instId": s.replace('/', '-')} for s in self.symbols]
            await ws.send(json.dumps({"op": "subscribe", "args": args}))
            print("✅ OKX WebSocket已连接")

            async for msg in ws:
                data = json.loads(msg)
                if 'data' in data:
                    for item in data['data']:
                        self._process_okx(item)

    def _process_okx(self, data):
        """处理OKX数据"""
        symbol = data['instId'].replace('-', '/')
        if 'bids' in data and 'asks' in data and data['bids'] and data['asks']:
            bid = float(data['bids'][0][0])
            ask = float(data['asks'][0][0])

            if symbol not in self.prices:
                self.prices[symbol] = {}
            self.prices[symbol]['okx'] = {'bid': bid, 'ask': ask, 'time': datetime.now()}

            self._check_opportunity(symbol)

    def _check_opportunity(self, symbol):
        """检测套利机会"""
        if symbol not in self.prices:
            return

        p = self.prices[symbol]
        if 'binance' not in p or 'okx' not in p:
            return

        b = p['binance']
        o = p['okx']

        # 场景1: 币安买 OKX卖
        if b['ask'] > 0 and o['bid'] > 0:
            profit1 = (o['bid'] / b['ask'] - 1) - 0.002
            if profit1 >= 0.003:
                self._record_opportunity(symbol, profit1, 'binance_buy', b['ask'], o['bid'])

        # 场景2: OKX买 币安卖
        if o['ask'] > 0 and b['bid'] > 0:
            profit2 = (b['bid'] / o['ask'] - 1) - 0.002
            if profit2 >= 0.003:
                self._record_opportunity(symbol, profit2, 'okx_buy', o['ask'], b['bid'])

    def _record_opportunity(self, symbol, profit, direction, buy_price, sell_price):
        """记录机会"""
        opp = {
            'symbol': symbol,
            'timestamp': datetime.now().isoformat(),
            'profit': profit,
            'direction': direction,
            'buy_price': buy_price,
            'sell_price': sell_price
        }
        self.opportunities.append(opp)
        print(f"\n🎉 {symbol}: {profit*100:.3f}% ({direction}) - 买:{buy_price:.6f} 卖:{sell_price:.6f}")

    async def run(self, duration_minutes=30):
        """运行监控"""
        print("=" * 60)
        print(f"WebSocket实时监控 - {duration_minutes}分钟")
        print(f"币种: {', '.join(self.symbols)}")
        print("=" * 60)

        # 并发运行两个WebSocket
        tasks = [
            asyncio.create_task(self.binance_stream()),
            asyncio.create_task(self.okx_stream())
        ]

        # 运行指定时间
        await asyncio.sleep(duration_minutes * 60)

        # 取消任务
        for task in tasks:
            task.cancel()

        # 等待任务完成
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except:
            pass

        print(f"\n\n{'='*60}")
        print(f"监控完成 - 发现{len(self.opportunities)}次机会")
        print("=" * 60)

        if self.opportunities:
            coin_stats = {}
            for opp in self.opportunities:
                s = opp['symbol']
                coin_stats[s] = coin_stats.get(s, 0) + 1

            print("\n机会分布:")
            for s, count in sorted(coin_stats.items(), key=lambda x: x[1], reverse=True):
                print(f"  {s}: {count}次")

async def main():
    # 先测试Top 10币种
    symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'DOGE/USDT', 'TON/USDT',
               'XRP/USDT', 'BNB/USDT', 'ADA/USDT', 'TRX/USDT', 'LINK/USDT']

    monitor = WebSocketMonitor(symbols)
    await monitor.run(duration_minutes=30)

if __name__ == '__main__':
    asyncio.run(main())

