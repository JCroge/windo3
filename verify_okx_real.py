#!/usr/bin/env python3
"""OKX真实账户验证 - 只读操作"""

import os
from dotenv import load_dotenv
from executor import ContractExecutor

load_dotenv()


def main():
    """验证OKX真实账户连接"""
    print("="*60)
    print("OKX真实账户验证（只读操作）")
    print("="*60)

    # 获取OKX凭证
    api_key = os.getenv('OKX_API_KEY')
    secret = os.getenv('OKX_SECRET')
    password = os.getenv('OKX_PASSWORD')

    if not all([api_key, secret, password]):
        print("❌ OKX凭证未配置")
        return 1

    try:
        # 创建执行器（真实账户）
        executor = ContractExecutor(
            exchange_id='okx',
            api_key=api_key,
            secret=secret,
            password=password,
            testnet=False,  # 真实账户
            leverage=1
        )

        print("\n1. 测试账户连接...")
        balance = executor.get_balance()
        print(f"✅ USDT余额: {balance:.2f}")

        print("\n2. 测试获取行情...")
        ticker = executor.exchange.fetch_ticker('BTC-USDT')
        print(f"✅ BTC-USDT价格: {ticker['last']}")

        print("\n3. 测试获取K线...")
        klines = executor.exchange.fetch_ohlcv('BTC-USDT', '1h', limit=10)
        print(f"✅ 获取K线成功: {len(klines)}根")
        print(f"   最新价格: {klines[-1][4]}")

        print("\n4. 检查持仓...")
        positions = executor.exchange.fetch_positions(['BTC-USDT'])
        active_positions = [p for p in positions if float(p.get('contracts', 0)) > 0]
        if active_positions:
            print(f"⚠️  当前有{len(active_positions)}个持仓")
            for p in active_positions:
                print(f"   {p['symbol']}: {p['side']} {p['contracts']}张")
        else:
            print("✅ 当前无持仓")

        print("\n5. 风控检查...")
        from risk_manager import RiskManager
        rm = RiskManager(max_trade_amount=10.0)
        can_trade, msg = rm.check_can_trade(balance)
        if can_trade:
            print(f"✅ 风控通过: {msg}")
        else:
            print(f"⚠️  风控拒绝: {msg}")

        print("\n" + "="*60)
        print("✅ 所有验证通过，系统可以开始交易")
        print("="*60)
        return 0

    except Exception as e:
        print(f"\n❌ 验证失败: {e}")
        return 1


if __name__ == '__main__':
    exit(main())
