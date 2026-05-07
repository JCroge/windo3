#!/usr/bin/env python3
"""实时交易系统 - 整合策略+执行+风控"""

import time
import sqlite3
import pandas as pd
from datetime import datetime
from optimize_1h import RobustStrategy
from executor import ContractExecutor
from utils.logger import setup_logger


class LiveTradingSystem:
    """实时交易系统"""

    def __init__(self, symbol='BTCUSDT', interval='1h',
                 exchange='binance', api_key=None, secret=None,
                 password=None, testnet=True, leverage=1):
        """
        Args:
            symbol: 交易对
            interval: K线周期
            exchange: 交易所 (binance/okx)
            api_key: API密钥
            secret: API密钥
            password: API密码（OKX需要）
            testnet: 是否使用测试网
            leverage: 杠杆倍数
        """
        self.logger = setup_logger('live_trading')
        self.symbol = symbol
        self.interval = interval

        # 初始化策略（使用最佳参数）
        self.strategy = RobustStrategy(
            ma_fast=7,
            ma_slow=25,
            rsi_period=14,
            rsi_threshold=75,
            volume_factor=1.0
        )

        # 初始化执行器
        self.executor = ContractExecutor(
            exchange_id=exchange,
            api_key=api_key,
            secret=secret,
            password=password,
            testnet=testnet,
            leverage=leverage
        )

        self.logger.info(f"实时交易系统启动: {exchange} {symbol} {interval}")
        self.logger.info(f"测试网模式: {testnet}, 杠杆: {leverage}x")

    def load_recent_klines(self, limit=100):
        """加载最近的K线数据（从交易所实时获取）"""
        try:
            # 从交易所获取实时K线
            klines = self.executor.exchange.fetch_ohlcv(
                self.symbol,
                self.interval,
                limit=limit
            )

            # 转换为DataFrame
            df = pd.DataFrame(
                klines,
                columns=['open_time', 'open', 'high', 'low', 'close', 'volume']
            )

            return df

        except Exception as e:
            self.logger.error(f"获取K线失败: {e}")
            # 降级：从数据库加载
            self.logger.warning("降级使用数据库K线数据")
            conn = sqlite3.connect('data/klines.db')
            query = '''
                SELECT open_time, open, high, low, close, volume
                FROM klines
                WHERE symbol = ? AND interval = ?
                ORDER BY open_time DESC
                LIMIT ?
            '''
            df = pd.read_sql_query(query, conn, params=(self.symbol, self.interval, limit))
            conn.close()
            return df.sort_values('open_time').reset_index(drop=True)

    def run(self, check_interval=60):
        """运行实时交易"""
        self.logger.info("开始实时交易循环...")

        while True:
            try:
                # 加载最近K线
                df = self.load_recent_klines(100)
                if len(df) < 50:
                    self.logger.warning("K线数据不足，等待...")
                    time.sleep(check_interval)
                    continue

                # 运行策略分析
                df_analyzed = self.strategy.analyze(df)

                # 使用倒数第二根K线（已闭合）
                if len(df_analyzed) < 2:
                    self.logger.warning("K线数据不足，需要至少2根")
                    time.sleep(check_interval)
                    continue

                latest = df_analyzed.iloc[-2]  # 使用已闭合的K线

                # 获取当前持仓
                position = self.executor.get_position(self.symbol)

                # 无持仓时检查入场信号
                if position is None:
                    if latest.get('entry_long', 0) == 1:
                        self.logger.info(f"检测到做多信号: 价格={latest['close']}, RSI={latest['rsi']:.2f}")
                        result = self.executor.open_long(self.symbol, 10.0)
                        if result:
                            self.logger.info(f"开多仓成功: {result}")
                        else:
                            self.logger.warning("开仓失败（风控拒绝或其他错误）")

                    elif latest.get('entry_short', 0) == 1:
                        self.logger.info(f"检测到做空信号: 价格={latest['close']}, RSI={latest['rsi']:.2f}")
                        result = self.executor.open_short(self.symbol, 10.0)
                        if result:
                            self.logger.info(f"开空仓成功: {result}")
                        else:
                            self.logger.warning("开仓失败（风控拒绝或其他错误）")

                # 有持仓时检查出场信号
                else:
                    # 检查止损止盈
                    trigger = self.executor.check_stop_loss_take_profit(self.symbol)
                    if trigger:
                        self.logger.info(f"触发{trigger}: 价格={latest['close']}")
                        result = self.executor.close_position(self.symbol)
                        if result:
                            self.logger.info(f"平仓成功: {result}")

                    # 检查策略出场信号
                    elif position['side'] == 'long' and latest.get('exit_long', 0) == 1:
                        self.logger.info(f"检测到多头出场信号: 价格={latest['close']}")
                        result = self.executor.close_position(self.symbol)
                        if result:
                            self.logger.info(f"平仓成功: {result}")

                    elif position['side'] == 'short' and latest.get('exit_short', 0) == 1:
                        self.logger.info(f"检测到空头出场信号: 价格={latest['close']}")
                        result = self.executor.close_position(self.symbol)
                        if result:
                            self.logger.info(f"平仓成功: {result}")

                # 显示当前状态
                rsi_val = latest.get('rsi', float('nan'))
                ma_fast_val = latest.get('ma_fast', float('nan'))
                ma_slow_val = latest.get('ma_slow', float('nan'))
                pos_str = f"{position['side']}@{position['entry_price']}" if position else "无持仓"
                self.logger.info(
                    f"[扫描] 价格={latest['close']:.2f} RSI={rsi_val:.1f} "
                    f"MA({self.strategy.ma_fast}/{self.strategy.ma_slow})={ma_fast_val:.2f}/{ma_slow_val:.2f} "
                    f"多头信号={int(latest.get('entry_long',0))} 空头信号={int(latest.get('entry_short',0))} "
                    f"持仓={pos_str}"
                )
                risk_status = self.executor.risk_manager.get_status()
                self.logger.info(f"风控状态: 今日盈亏={risk_status['daily_pnl']:.2f}, "
                               f"回撤={risk_status['current_drawdown_pct']:.2f}%")

                # 等待下一次检查
                time.sleep(check_interval)

            except KeyboardInterrupt:
                self.logger.info("用户中断，退出...")
                break
            except Exception as e:
                self.logger.error(f"运行错误: {e}")
                time.sleep(check_interval)


def main():
    """主函数"""
    import os
    from dotenv import load_dotenv

    # 加载环境变量
    load_dotenv()

    # 选择交易所
    exchange = os.getenv('EXCHANGE', 'binance').lower()

    if exchange == 'binance':
        api_key = os.getenv('BINANCE_API_KEY')
        secret = os.getenv('BINANCE_SECRET')
        password = None
        symbol = 'BTCUSDT'
    elif exchange == 'okx':
        api_key = os.getenv('OKX_API_KEY')
        secret = os.getenv('OKX_SECRET')
        password = os.getenv('OKX_PASSWORD')
        symbol = 'BTC-USDT'  # OKX格式
    else:
        print(f"❌ 不支持的交易所: {exchange}")
        return

    if not api_key or not secret:
        print(f"⚠️  未配置{exchange.upper()} API密钥，请在.env文件中配置:")
        if exchange == 'binance':
            print("BINANCE_API_KEY=your_key")
            print("BINANCE_SECRET=your_secret")
        else:
            print("OKX_API_KEY=your_key")
            print("OKX_SECRET=your_secret")
            print("OKX_PASSWORD=your_passphrase")
        print("\n将使用模拟模式（仅分析信号，不实际交易）")
        return

    # 读取配置
    testnet = os.getenv('USE_TESTNET', 'false').lower() == 'true'
    leverage = int(os.getenv('LEVERAGE', '1'))

    # 创建交易系统
    system = LiveTradingSystem(
        symbol=symbol,
        interval='1h',
        exchange=exchange,
        api_key=api_key,
        secret=secret,
        password=password,
        testnet=testnet,
        leverage=leverage
    )

    # 运行
    system.run(check_interval=60)  # 每1分钟检查一次


if __name__ == '__main__':
    main()
