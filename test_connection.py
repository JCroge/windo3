import asyncio
import ccxt

async def test_connection():
    print("测试交易所连接...")

    # 测试Binance
    try:
        binance = ccxt.binance()
        ticker = binance.fetch_ticker('ETH/USDT')
        print(f"✅ Binance连接成功 - ETH/USDT: {ticker['last']}")
    except Exception as e:
        print(f"❌ Binance连接失败: {e}")

    # 测试OKX
    try:
        okx = ccxt.okx()
        ticker = okx.fetch_ticker('ETH/USDT')
        print(f"✅ OKX连接成功 - ETH/USDT: {ticker['last']}")
    except Exception as e:
        print(f"❌ OKX连接失败: {e}")

if __name__ == '__main__':
    asyncio.run(test_connection())
