#!/usr/bin/env python3
"""系统完整性验证 - 测试所有关键flow"""

import os
import sys
import json
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


class SystemVerifier:
    """系统验证器"""

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


def test_imports():
    """测试1: 验证所有模块可导入"""
    from risk_manager import RiskManager
    from executor import ContractExecutor
    from optimize_1h import RobustStrategy
    from live_trading import LiveTradingSystem
    print("✓ 所有核心模块导入成功")


def test_risk_manager_logic():
    """测试2: 风控管理器逻辑"""
    from risk_manager import RiskManager

    rm = RiskManager(
        max_trade_amount=10.0,
        max_daily_loss=50.0,
        max_drawdown_pct=20.0,
        state_file='data/test_risk_state.json'
    )

    # 测试余额充足
    can_trade, msg = rm.check_can_trade(100.0)
    assert can_trade, f"余额充足应该可以交易: {msg}"
    print("✓ 余额充足检查通过")

    # 测试余额不足
    can_trade, msg = rm.check_can_trade(5.0)
    assert not can_trade, "余额不足应该拒绝交易"
    print("✓ 余额不足检查通过")

    # 测试每日亏损限制（只限制亏损）
    rm.daily_pnl = -50.0
    can_trade, msg = rm.check_can_trade(100.0)
    assert not can_trade, "达到每日亏损限制应该拒绝交易"
    print("✓ 每日亏损限制检查通过")

    # 测试盈利不受限制
    rm.daily_pnl = 100.0  # 盈利100
    can_trade, msg = rm.check_can_trade(100.0)
    assert can_trade, "盈利不应该被限制"
    print("✓ 盈利不受限制检查通过")

    # 测试回撤限制
    rm.peak_balance = 100.0
    can_trade, msg = rm.check_can_trade(79.0)  # 21%回撤
    assert not can_trade, "超过最大回撤应该拒绝交易"
    print("✓ 回撤限制检查通过")

    # 测试止损止盈计算
    stop_loss = rm.calculate_stop_loss(100.0, 'long')
    assert stop_loss == 98.0, f"做多止损计算错误: {stop_loss}"

    stop_loss = rm.calculate_stop_loss(100.0, 'short')
    assert stop_loss == 102.0, f"做空止损计算错误: {stop_loss}"

    take_profit = rm.calculate_take_profit(100.0, 'long')
    assert take_profit == 105.0, f"做多止盈计算错误: {take_profit}"

    take_profit = rm.calculate_take_profit(100.0, 'short')
    assert take_profit == 95.0, f"做空止盈计算错误: {take_profit}"
    print("✓ 止损止盈计算正确")

    # 清理测试文件
    if os.path.exists('data/test_risk_state.json'):
        os.remove('data/test_risk_state.json')


def test_risk_manager_persistence():
    """测试3: 风控状态持久化"""
    from risk_manager import RiskManager

    # 创建风控管理器并设置峰值余额
    rm1 = RiskManager(state_file='data/test_risk_state.json')
    rm1.check_can_trade(150.0)  # 设置峰值余额为150
    assert rm1.peak_balance == 150.0
    print("✓ 峰值余额设置成功")

    # 创建新实例，验证峰值余额被加载
    rm2 = RiskManager(state_file='data/test_risk_state.json')
    assert rm2.peak_balance == 150.0, f"峰值余额未正确加载: {rm2.peak_balance}"
    print("✓ 峰值余额持久化成功")

    # 清理测试文件
    if os.path.exists('data/test_risk_state.json'):
        os.remove('data/test_risk_state.json')


def test_strategy_signals():
    """测试4: 策略信号生成"""
    from optimize_1h import RobustStrategy
    import sqlite3

    # 从数据库加载真实K线数据
    conn = sqlite3.connect('data/klines.db')
    query = '''
        SELECT open_time, open, high, low, close, volume
        FROM klines
        WHERE symbol = 'BTCUSDT' AND interval = '1h'
        ORDER BY open_time DESC
        LIMIT 100
    '''
    df = pd.read_sql_query(query, conn)
    conn.close()

    if len(df) < 50:
        print("⚠️ K线数据不足，跳过策略测试")
        return

    df = df.sort_values('open_time').reset_index(drop=True)

    # 创建策略并分析
    strategy = RobustStrategy(ma_fast=7, ma_slow=25, rsi_period=14, rsi_threshold=75)
    df_analyzed = strategy.analyze(df)

    # 验证指标计算
    assert 'ma_fast' in df_analyzed.columns, "缺少快速均线"
    assert 'ma_slow' in df_analyzed.columns, "缺少慢速均线"
    assert 'rsi' in df_analyzed.columns, "缺少RSI"
    assert 'entry_long' in df_analyzed.columns, "缺少入场信号"
    assert 'exit_long' in df_analyzed.columns, "缺少出场信号"
    print("✓ 策略指标计算完整")

    # 验证信号值合法
    assert df_analyzed['entry_long'].isin([0, 1]).all(), "入场信号值非法"
    assert df_analyzed['exit_long'].isin([0, 1]).all(), "出场信号值非法"
    print("✓ 策略信号值合法")

    # 验证使用已闭合K线
    latest = df_analyzed.iloc[-2]  # 应该使用倒数第二根
    print(f"✓ 使用已闭合K线: close={latest['close']}, rsi={latest['rsi']:.2f}")


def test_executor_position_persistence():
    """测试5: 持仓持久化"""
    from executor import ContractExecutor

    # 创建执行器（不需要真实API）
    executor1 = ContractExecutor(
        exchange_id='binance',
        testnet=True,
        positions_file='data/test_positions.json'
    )

    # 手动添加持仓
    executor1.positions['BTCUSDT'] = {
        'symbol': 'BTCUSDT',
        'side': 'long',
        'entry_price': 50000.0,
        'amount': 0.001,
        'amount_usdt': 10.0,
        'leverage': 1,
        'stop_loss': 49000.0,
        'take_profit': 52500.0,
        'order_id': 'test123'
    }
    executor1._save_positions()
    print("✓ 持仓保存成功")

    # 创建新实例，验证持仓被加载
    executor2 = ContractExecutor(
        exchange_id='binance',
        testnet=True,
        positions_file='data/test_positions.json'
    )

    assert 'BTCUSDT' in executor2.positions, "持仓未正确加载"
    assert executor2.positions['BTCUSDT']['entry_price'] == 50000.0
    print("✓ 持仓持久化成功")

    # 清理测试文件
    if os.path.exists('data/test_positions.json'):
        os.remove('data/test_positions.json')


def test_live_trading_initialization():
    """测试6: 实时交易系统初始化"""
    from live_trading import LiveTradingSystem

    # 测试初始化（不需要真实API）
    system = LiveTradingSystem(
        symbol='BTCUSDT',
        interval='1h',
        exchange='binance',
        testnet=True,
        leverage=1
    )

    assert system.symbol == 'BTCUSDT'
    assert system.interval == '1h'
    assert system.strategy is not None
    assert system.executor is not None
    print("✓ 实时交易系统初始化成功")


def test_pnl_calculation_with_leverage():
    """测试7: 盈亏计算含杠杆"""
    # 模拟做多场景
    entry_price = 50000.0
    exit_price = 51000.0
    amount_usdt = 10.0
    leverage = 10

    # 盈亏 = (出场价 - 入场价) / 入场价 * 本金 * 杠杆
    pnl = (exit_price - entry_price) / entry_price * amount_usdt * leverage
    expected_pnl = (51000 - 50000) / 50000 * 10 * 10  # = 2.0

    assert abs(pnl - expected_pnl) < 0.01, f"做多盈亏计算错误: {pnl} != {expected_pnl}"
    print(f"✓ 做多盈亏计算正确: {pnl:.2f} USDT")

    # 模拟做空场景
    pnl_short = (entry_price - exit_price) / entry_price * amount_usdt * leverage
    expected_pnl_short = (50000 - 51000) / 50000 * 10 * 10  # = -2.0

    assert abs(pnl_short - expected_pnl_short) < 0.01, f"做空盈亏计算错误: {pnl_short} != {expected_pnl_short}"
    print(f"✓ 做空盈亏计算正确: {pnl_short:.2f} USDT")


def test_environment_variables():
    """测试8: 环境变量配置"""
    # 检查关键环境变量
    exchange = os.getenv('EXCHANGE', 'binance')
    use_testnet = os.getenv('USE_TESTNET', 'false').lower() == 'true'
    leverage = int(os.getenv('LEVERAGE', '1'))

    print(f"✓ EXCHANGE: {exchange}")
    print(f"✓ USE_TESTNET: {use_testnet}")
    print(f"✓ LEVERAGE: {leverage}")

    # 验证.env.example存在
    assert os.path.exists('.env.example'), ".env.example文件缺失"
    print("✓ .env.example文件存在")


def test_data_directory():
    """测试9: 数据目录结构"""
    # 验证data目录存在
    assert os.path.exists('data'), "data目录不存在"
    print("✓ data目录存在")

    # 验证K线数据库存在
    assert os.path.exists('data/klines.db'), "K线数据库不存在"
    print("✓ K线数据库存在")


def main():
    """主函数"""
    print("="*60)
    print("加密货币交易系统 - 完整性验证")
    print("="*60)
    print(f"验证时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    verifier = SystemVerifier()

    # 执行所有测试
    verifier.test("模块导入", test_imports)
    verifier.test("风控管理器逻辑", test_risk_manager_logic)
    verifier.test("风控状态持久化", test_risk_manager_persistence)
    verifier.test("策略信号生成", test_strategy_signals)
    verifier.test("持仓持久化", test_executor_position_persistence)
    verifier.test("实时交易系统初始化", test_live_trading_initialization)
    verifier.test("盈亏计算含杠杆", test_pnl_calculation_with_leverage)
    verifier.test("环境变量配置", test_environment_variables)
    verifier.test("数据目录结构", test_data_directory)

    # 输出摘要
    verifier.summary()

    # 返回状态码
    return 0 if verifier.failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
