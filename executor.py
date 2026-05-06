#!/usr/bin/env python3
"""合约执行器 - 基于ccxt的统一接口"""

import ccxt
import json
import os
from typing import Dict, Optional
from risk_manager import RiskManager
from utils.logger import setup_logger


class ContractExecutor:
    """合约执行器"""

    def __init__(self, exchange_id: str = 'binance',
                 api_key: str = None,
                 secret: str = None,
                 password: str = None,
                 testnet: bool = True,
                 leverage: int = 1,
                 positions_file: str = 'data/positions.json'):
        """
        Args:
            exchange_id: 交易所ID (binance/okx)
            api_key: API密钥
            secret: API密钥
            password: API密码（OKX需要）
            testnet: 是否使用测试网
            leverage: 杠杆倍数（默认1倍，不使用杠杆）
            positions_file: 持仓持久化文件路径
        """
        self.logger = setup_logger('executor')
        self.exchange_id = exchange_id
        self.testnet = testnet
        self.leverage = leverage
        self.positions_file = positions_file

        # 初始化交易所
        exchange_class = getattr(ccxt, exchange_id)
        config = {
            'apiKey': api_key,
            'secret': secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'}  # 使用永续合约
        }

        # OKX需要password
        if exchange_id == 'okx' and password:
            config['password'] = password

        self.exchange = exchange_class(config)

        if testnet:
            self.exchange.set_sandbox_mode(True)
            self.logger.info(f"使用 {exchange_id} 测试网")

        # 风控管理器
        self.risk_manager = RiskManager()

        # 持仓记录
        self.positions = {}
        self._load_positions()

        self.logger.info(f"杠杆设置: {leverage}x")

    def get_balance(self) -> float:
        """获取USDT余额"""
        try:
            balance = self.exchange.fetch_balance()
            return balance['USDT']['free']
        except Exception as e:
            self.logger.error(f"获取余额失败: {e}")
            return 0.0

    def open_long(self, symbol: str, amount_usdt: float) -> Optional[Dict]:
        """开多仓"""
        return self._open_position(symbol, 'long', amount_usdt)

    def open_short(self, symbol: str, amount_usdt: float) -> Optional[Dict]:
        """开空仓"""
        return self._open_position(symbol, 'short', amount_usdt)

    def _open_position(self, symbol: str, side: str, amount_usdt: float) -> Optional[Dict]:
        """开仓"""
        try:
            # 风控检查
            balance = self.get_balance()
            can_trade, msg = self.risk_manager.check_can_trade(balance)
            if not can_trade:
                self.logger.warning(f"风控拒绝: {msg}")
                return None

            # 获取当前价格
            ticker = self.exchange.fetch_ticker(symbol)
            current_price = ticker['last']

            # 计算仓位
            position_size = self.risk_manager.calculate_position_size(balance)
            position_size = min(position_size, amount_usdt)

            # 计算数量（币数）
            amount = position_size / current_price

            # 设置杠杆
            try:
                self.exchange.set_leverage(self.leverage, symbol)
                self.logger.info(f"设置杠杆: {self.leverage}x")
            except Exception as e:
                self.logger.warning(f"设置杠杆失败（可能已设置）: {e}")

            # 创建合约订单
            order_side = 'buy' if side == 'long' else 'sell'
            order = self.exchange.create_order(
                symbol=symbol,
                type='market',
                side=order_side,
                amount=amount,
                params={'reduceOnly': False}  # 开仓
            )

            # 计算止损止盈
            stop_loss = self.risk_manager.calculate_stop_loss(current_price, side)
            take_profit = self.risk_manager.calculate_take_profit(current_price, side)

            # 记录持仓
            position = {
                'symbol': symbol,
                'side': side,
                'entry_price': current_price,
                'amount': amount,
                'amount_usdt': position_size,
                'leverage': self.leverage,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'order_id': order['id']
            }
            self.positions[symbol] = position
            self._save_positions()

            self.logger.info(f"开仓成功: {side} {symbol}, 价格: {current_price}, 数量: {amount}, 杠杆: {self.leverage}x")
            return position

        except Exception as e:
            self.logger.error(f"开仓失败: {e}")
            return None

    def close_position(self, symbol: str) -> Optional[Dict]:
        """平仓"""
        if symbol not in self.positions:
            self.logger.warning(f"没有持仓: {symbol}")
            return None

        try:
            position = self.positions[symbol]

            # 获取当前价格
            ticker = self.exchange.fetch_ticker(symbol)
            exit_price = ticker['last']

            # 平仓（使用reduceOnly）
            order_side = 'sell' if position['side'] == 'long' else 'buy'
            order = self.exchange.create_order(
                symbol=symbol,
                type='market',
                side=order_side,
                amount=position['amount'],
                params={'reduceOnly': True}  # 平仓
            )

            # 计算盈亏（考虑杠杆）
            leverage = position.get('leverage', 1)
            if position['side'] == 'long':
                pnl = (exit_price - position['entry_price']) / position['entry_price'] * position['amount_usdt'] * leverage
            else:
                pnl = (position['entry_price'] - exit_price) / position['entry_price'] * position['amount_usdt'] * leverage

            # 扣除手续费（开仓+平仓，各0.1%）
            pnl -= position['amount_usdt'] * leverage * 0.002

            # 记录盈亏
            self.risk_manager.record_trade(pnl)

            result = {
                'symbol': symbol,
                'side': position['side'],
                'entry_price': position['entry_price'],
                'exit_price': exit_price,
                'leverage': leverage,
                'pnl': pnl,
                'pnl_pct': pnl / position['amount_usdt'] * 100
            }

            # 删除持仓
            del self.positions[symbol]
            self._save_positions()

            self.logger.info(f"平仓成功: {symbol}, 盈亏: {pnl:.2f} USDT ({result['pnl_pct']:.2f}%)")
            return result

        except Exception as e:
            self.logger.error(f"平仓失败: {e}")
            return None

    def check_stop_loss_take_profit(self, symbol: str) -> Optional[str]:
        """检查止损止盈"""
        if symbol not in self.positions:
            return None

        try:
            position = self.positions[symbol]
            ticker = self.exchange.fetch_ticker(symbol)
            current_price = ticker['last']

            # 检查止损
            if position['side'] == 'long' and current_price <= position['stop_loss']:
                return 'stop_loss'
            if position['side'] == 'short' and current_price >= position['stop_loss']:
                return 'stop_loss'

            # 检查止盈
            if position['side'] == 'long' and current_price >= position['take_profit']:
                return 'take_profit'
            if position['side'] == 'short' and current_price <= position['take_profit']:
                return 'take_profit'

            return None

        except Exception as e:
            self.logger.error(f"检查止损止盈失败: {e}")
            return None

    def _load_positions(self):
        """加载持仓记录"""
        if os.path.exists(self.positions_file):
            try:
                with open(self.positions_file, 'r') as f:
                    self.positions = json.load(f)
                    self.logger.info(f"加载持仓记录: {len(self.positions)}个")
            except Exception as e:
                self.logger.warning(f"加载持仓失败: {e}")

    def _save_positions(self):
        """保存持仓记录"""
        try:
            os.makedirs(os.path.dirname(self.positions_file), exist_ok=True)
            with open(self.positions_file, 'w') as f:
                json.dump(self.positions, f, indent=2)
        except Exception as e:
            self.logger.error(f"保存持仓失败: {e}")

    def get_position(self, symbol: str) -> Optional[Dict]:
        """获取持仓信息"""
        return self.positions.get(symbol)

    def get_all_positions(self) -> Dict:
        """获取所有持仓"""
        return self.positions.copy()
