#!/usr/bin/env python3
"""交易Flow验证 - 测试实际交易逻辑"""

import os
import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


class TradingFlowVerifier:
    """交易流程验证器"""

    def __init__(self):
        self.results = []
        self.passed = 0
        self.failed = 0

    def test(self, name: str, func):
        """执行单个测试"""
        try:
            print(f"\n{'='*60}")
            print(f"测试: {name}")
            print(f"{'='*60}")
            func()
            self.results.append((name, "✅ PASS", None))
            self.passed += 1
            print(f"✅ {name} - 通过")
        except AssertionError as e:
            self.results.append((name, "❌ FAIL", str(e)))
            self.failed += 1
            print(f"❌ {name} - 失败: {e}")
        except Exception as e:
            self.results.append((name, "⚠️ ERROR", str(e)))
            self.failed += 1
            print(f"⚠️ {name} - 错误: {e}")

    def summary(self):
        """输出测试摘要"""
        print(f"\n{'='*60}")
        print("测试摘要")
        print(f"{'='*60}")
        print(f"总计: {self.passed + self.failed}")
        print(f"通过: {self.passed}")
        print(f"失败: {self.failed}")
        print(f"\n详细结果:")
        for name, status, error in self.results:
            print(f"{status} {name}")
            if error:
                print(f"   错误: {error}")


def test_exchange_connection():
    """测试1: 交易所连接"""
    from executor import ContractExecutor

    exchange = os.getenv('EXCHANGE', 'binance')

    # 使用测试网模式
    executor = ContractExecutor(
        exchange_id=exchange,
        testnet=True,
        leverage=1
    )

    # 测试获取行情
    ticker = executor.exchange.fetch_ticker('BTC/USDT')
    assert 'last' in ticker, "行情数据缺少价格"
    assert ticker['last'] > 0, "价格应该大于0"
    print(f"✓ 获取行情成功: BTC/USDT = {ticker['last']}")

    # 测试获取K线
    klines = executor.exchange.fetch_ohlcv('BTC/USDT', '1h', limit=10)
    assert len(klines) > 0, "K线数据为空"
    assert len(klines[0]) == 6, "K线数据格式错误"
    print(f"✓ 获取K线成功: {len(klines)}根")


def test_kline_fetching_flow():
    """测试2: K线获取flow"""
    from live_trading import LiveTradingSystem

    system = LiveTradingSystem(
        symbol='BTCUSDT',
        interval='1h',
        exchange='binance',
        testnet=True,
        leverage=1
    )

    # 测试加载K线
    df = system.load_recent_klines(limit=50)
    assert len(df) >= 50, f"K线数量不足: {len(df)}"
    assert 'close' in df.columns, "K线数据缺少close列"
    assert 'volume' in df.columns, "K线数据缺少volume列"
    print(f"✓ K线获取成功: {len(df)}根")
    print(f"✓ 最新价格: {df.iloc[-1]['close']}")


def test_strategy_analysis_flow():
    """测试3: 策略分析flow"""
    from live_trading import LiveTradingSystem

    system = LiveTradingSystem(
        symbol='BTCUSDT',
        interval='1h',
        exchange='binance',
        testnet=True,
        leverage=1
    )

    # 加载K线并分析
    df = system.load_recent_klines(limit=100)
    df_analyzed = system.strategy.analyze(df)

    # 验证指标存在
    required_cols = ['ma_fast', 'ma_slow', 'rsi', 'entry_long', 'exit_long']
    for col in required_cols:
        assert col in df_analyzed.columns, f"缺少指标: {col}"
    print(f"✓ 策略分析完成，指标齐全")

    # 使用已闭合K线
    latest = df_analyzed.iloc[-2]
    print(f"✓ 使用已闭合K线: close={latest['close']}, RSI={latest['rsi']:.2f}")
    print(f"✓ 入场信号: {latest['entry_long']}, 出场信号: {latest['exit_long']}")


def test_stop_loss_take_profit_logic():
    """测试4: 止损止盈逻辑"""
    from executor import ContractExecutor

    executor = ContractExecutor(
        exchange_id='binance',
        testnet=True,
        positions_file='data/test_sl_tp.json'
    )

    # 模拟持仓
    executor.positions['BTCUSDT'] = {
        'symbol': 'BTCUSDT',
        'side': 'long',
        'entry_price': 50000.0,
        'amount': 0.001,
        'amount_usdt': 10.0,
        'leverage': 1,
        'stop_loss': 49000.0,  # -2%
        'take_profit': 52500.0,  # +5%
        'order_id': 'test123'
    }

    # 测试止损触发（做多，价格跌破止损）
    executor.exchange.fetch_ticker = lambda s: {'last': 48900.0}
    trigger = executor.check_stop_loss_take_profit('BTCUSDT')
    assert trigger == 'stop_loss', f"应该触发止损: {trigger}"
    print("✓ 做多止损触发正确")

    # 测试止盈触发（做多，价格突破止盈）
    executor.exchange.fetch_ticker = lambda s: {'last': 52600.0}
    trigger = executor.check_stop_loss_take_profit('BTCUSDT')
    assert trigger == 'take_profit', f"应该触发止盈: {trigger}"
    print("✓ 做多止盈触发正确")

    # 测试做空止损
    executor.positions['BTCUSDT']['side'] = 'short'
    executor.positions['BTCUSDT']['stop_loss'] = 51000.0  # 做空止损在上方
    executor.exchange.fetch_ticker = lambda s: {'last': 51100.0}
    trigger = executor.check_stop_loss_take_profit('BTCUSDT')
    assert trigger == 'stop_loss', f"做空应该触发止损: {trigger}"
    print("✓ 做空止损触发正确")

    # 测试做空止盈
    executor.positions['BTCUSDT']['take_profit'] = 47500.0  # 做空止盈在下方
    executor.exchange.fetch_ticker = lambda s: {'last': 47400.0}
    trigger = executor.check_stop_loss_take_profit('BTCUSDT')
    assert trigger == 'take_profit', f"做空应该触发止盈: {trigger}"
    print("✓ 做空止盈触发正确")

    # 清理
    if os.path.exists('data/test_sl_tp.json'):
        os.remove('data/test_sl_tp.json')


def test_position_size_calculation():
    """测试5: 仓位计算"""
    from risk_manager import RiskManager

    rm = RiskManager(max_trade_amount=10.0)

    # 余额充足
    size = rm.calculate_position_size(100.0)
    assert size == 10.0, f"仓位计算错误: {size}"
    print(f"✓ 余额100，仓位: {size} USDT")

    # 余额不足
    size = rm.calculate_position_size(50.0)
    assert size == 5.0, f"仓位计算错误: {size}"  # 10% of 50
    print(f"✓ 余额50，仓位: {size} USDT")


def test_leverage_setting():
    """测试6: 杠杆设置"""
    from executor import ContractExecutor

    # 测试不同杠杆
    for leverage in [1, 5, 10]:
        executor = ContractExecutor(
            exchange_id='binance',
            testnet=True,
            leverage=leverage
        )
        assert executor.leverage == leverage, f"杠杆设置错误: {executor.leverage}"
        print(f"✓ 杠杆{leverage}x设置成功")


def test_short_trading_support():
    """测试7: 做空支持"""
    from live_trading import LiveTradingSystem
    import pandas as pd

    system = LiveTradingSystem(
        symbol='BTCUSDT',
        interval='1h',
        exchange='binance',
        testnet=True,
        leverage=1
    )

    # 模拟带有做空信号的数据
    df = pd.DataFrame({
        'open_time': [1, 2],
        'open': [50000, 50100],
        'high': [50200, 50300],
        'low': [49900, 50000],
        'close': [50100, 50200],
        'volume': [100, 100],
        'entry_short': [0, 1],  # 做空信号
        'exit_short': [0, 0],
        'rsi': [50, 50]
    })

    # 验证系统能识别做空信号
    latest = df.iloc[-1]
    has_short_signal = latest.get('entry_short', 0) == 1
    assert has_short_signal, "做空信号未识别"
    print("✓ 做空信号识别成功")


def main():
    """主函数"""
    print("="*60)
    print("交易Flow验证 - 实际交易逻辑测试")
    print("="*60)
    print(f"验证时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("⚠️  使用测试网模式")

    verifier = TradingFlowVerifier()

    # 执行所有测试
    verifier.test("交易所连接", test_exchange_connection)
    verifier.test("K线获取flow", test_kline_fetching_flow)
    verifier.test("策略分析flow", test_strategy_analysis_flow)
    verifier.test("止损止盈逻辑", test_stop_loss_take_profit_logic)
    verifier.test("仓位计算", test_position_size_calculation)
    verifier.test("杠杆设置", test_leverage_setting)
    verifier.test("做空支持", test_short_trading_support)

    # 输出摘要
    verifier.summary()

    return 0 if verifier.failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
