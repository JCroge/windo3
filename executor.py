#!/usr/bin/env python3
"""合约执行器 - 基于ccxt的统一接口"""

import ccxt
import json
import os
import time
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
        max_amount = float(os.getenv('MAX_TRADE_AMOUNT', 10))
        self.risk_manager = RiskManager(
            max_trade_amount=max_amount,
            state_file='data/risk_state.json'
        )

        # 持仓记录
        self.positions = {}
        self._load_positions()

        # 止损检查连续失败计数器（key=symbol）
        self._sl_check_failures = {}
        self._sl_max_failures = 3  # 连续失败N次后强制平仓

        self.logger.info(f"杠杆设置: {leverage}x")

    def get_balance(self) -> float:
        """获取USDT余额（total，含持仓保证金，用于回撤计算）"""
        try:
            balance = self.exchange.fetch_balance()
            return balance['USDT']['total']
        except Exception as e:
            self.logger.error(f"获取余额失败: {e}")
            return 0.0

    def open_long(self, symbol: str, amount_usdt: float) -> Optional[Dict]:
        """开多仓"""
        return self._open_position(symbol, 'long', amount_usdt)

    def open_short(self, symbol: str, amount_usdt: float) -> Optional[Dict]:
        """开空仓"""
        return self._open_position(symbol, 'short', amount_usdt)

    def _normalize_symbol(self, symbol: str) -> str:
        """确保使用SWAP格式"""
        if not symbol.endswith('-SWAP') and '-USDT' in symbol:
            return symbol + '-SWAP'
        return symbol

    def _open_position(self, symbol: str, side: str, amount_usdt: float) -> Optional[Dict]:
        """开仓"""
        symbol = self._normalize_symbol(symbol)
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

            # 计算仓位（保证金）
            position_size = self.risk_manager.calculate_position_size(balance)
            position_size = min(position_size, amount_usdt)

            # 设置杠杆
            try:
                self.exchange.set_leverage(self.leverage, symbol)
                self.logger.info(f"设置杠杆: {self.leverage}x")
            except Exception as e:
                self.logger.warning(f"设置杠杆失败（可能已设置）: {e}")

            # 计算数量（合约张数）：名义价值 / (价格 × 合约面值)
            market = self.exchange.market(symbol)
            contract_size = market.get('contractSize', 1)
            notional = position_size * self.leverage
            amount = notional / (current_price * contract_size)
            amount = float(self.exchange.amount_to_precision(symbol, amount))
            if amount < market.get('limits', {}).get('amount', {}).get('min', 1):
                self.logger.warning(f"下单数量{amount}低于最小限制，放弃")
                return None

            # 创建合约订单
            order_side = 'buy' if side == 'long' else 'sell'
            order = self.exchange.create_order(
                symbol=symbol,
                type='market',
                side=order_side,
                amount=amount,
                params={'reduceOnly': False}
            )

            # 计算止损止盈
            stop_loss = self.risk_manager.calculate_stop_loss(current_price, side)
            take_profit = self.risk_manager.calculate_take_profit(current_price, side)

            # 在交易所设置SL条件单
            sl_order_id = None
            try:
                sl_side = 'sell' if side == 'long' else 'buy'
                sl_order = self.exchange.create_order(
                    symbol=symbol,
                    type='market',
                    side=sl_side,
                    amount=amount,
                    params={
                        'reduceOnly': True,
                        'stopLossPrice': stop_loss,
                    }
                )
                sl_order_id = sl_order['id']
                self.logger.info(f"SL条件单设置成功: {stop_loss:.6f}")
            except Exception as e:
                self.logger.warning(f"设置SL条件单失败（本地兜底）: {e}")

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
                'order_id': order['id'],
                'sl_order_id': sl_order_id,
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
            # 冷却：防止sync在API延迟期间重新发现该持仓
            if not hasattr(self, '_close_cooldown'):
                self._close_cooldown = {}
            self._close_cooldown[symbol] = time.time() + 60

            self.logger.info(f"平仓成功: {symbol}, 盈亏: {pnl:.2f} USDT ({result['pnl_pct']:.2f}%)")
            return result

        except Exception as e:
            error_msg = str(e)
            # 51205: Reduce Only不可用 = 持仓已不存在
            if '51205' in error_msg or 'Reduce Only' in error_msg:
                self.logger.warning(f"持仓已不存在，清理本地记录: {symbol}")
                if symbol in self.positions:
                    del self.positions[symbol]
                    self._save_positions()
                return None
            self.logger.error(f"平仓失败: {e}")
            return None

    def check_stop_loss_take_profit(self, symbol: str) -> Optional[str]:
        """检查止损止盈 — 多源价格获取 + 连续失败强制平仓"""
        if symbol not in self.positions:
            self._sl_check_failures.pop(symbol, None)
            return None

        position = self.positions[symbol]
        if 'stop_loss' not in position or 'take_profit' not in position:
            return None

        current_price = self._fetch_price_robust(symbol)

        if current_price is None:
            count = self._sl_check_failures.get(symbol, 0) + 1
            self._sl_check_failures[symbol] = count
            self.logger.warning(
                f"止损检查: {symbol} 价格获取失败 (连续{count}次)"
            )
            if count >= self._sl_max_failures:
                self.logger.error(
                    f"止损检查: {symbol} 连续{count}次失败，强制平仓保护资金"
                )
                return 'price_fetch_failed'
            return None

        self._sl_check_failures[symbol] = 0

        if position['side'] == 'long' and current_price <= position['stop_loss']:
            return 'stop_loss'
        if position['side'] == 'short' and current_price >= position['stop_loss']:
            return 'stop_loss'

        if position['side'] == 'long' and current_price >= position['take_profit']:
            return 'take_profit'
        if position['side'] == 'short' and current_price <= position['take_profit']:
            return 'take_profit'

        return None

    def _fetch_price_robust(self, symbol: str) -> Optional[float]:
        """多源价格获取：ticker → orderbook mid → 短暂重试"""
        # 方法1: fetch_ticker
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            if ticker and ticker.get('last'):
                return float(ticker['last'])
        except Exception:
            pass

        # 方法2: orderbook中间价
        try:
            ob = self.exchange.fetch_order_book(symbol, limit=5)
            if ob.get('asks') and ob.get('bids'):
                best_ask = ob['asks'][0][0]
                best_bid = ob['bids'][0][0]
                return (best_ask + best_bid) / 2
        except Exception:
            pass

        # 方法3: 等1秒重试ticker
        time.sleep(1)
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            if ticker and ticker.get('last'):
                return float(ticker['last'])
        except Exception:
            pass

        return None

    def _load_positions(self):
        """加载持仓记录"""
        if os.path.exists(self.positions_file):
            try:
                with open(self.positions_file, 'r') as f:
                    raw = json.load(f)
                # 过滤掉缺少止损/止盈字段的残缺持仓，避免重启后崩溃
                self.positions = {
                    k: v for k, v in raw.items()
                    if 'stop_loss' in v and 'take_profit' in v
                }
                skipped = len(raw) - len(self.positions)
                if skipped:
                    self.logger.warning(f"跳过{skipped}个残缺持仓记录（缺少止损/止盈）")
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

    def open_position_with_plan(self, symbol: str, side: str, plan: dict) -> Optional[Dict]:
        """基于Judge plan的智能开仓"""
        symbol = self._normalize_symbol(symbol)
        try:
            balance = self.get_balance()
            can_trade, msg = self.risk_manager.check_can_trade(balance)
            if not can_trade:
                self.logger.warning(f"风控拒绝: {msg}")
                return None

            leverage = plan.get('leverage', self.leverage)
            size_usdt = plan.get('size_usdt', self.risk_manager.max_trade_amount)
            size_usdt = min(size_usdt, self.risk_manager.max_trade_amount)
            required_margin = size_usdt
            free_balance = self.exchange.fetch_balance()['USDT']['free']
            if free_balance < required_margin * 1.1:
                self.logger.warning(f"可用余额不足: free={free_balance:.2f} < 需要{required_margin:.2f}")
                return None

            order_type = plan.get('order_type', 'market')
            entry_zone = plan.get('entry_zone', {})
            stop_loss = plan.get('stop_loss')
            take_profit = plan.get('take_profit', [])

            try:
                self.exchange.set_leverage(leverage, symbol)
            except Exception as e:
                self.logger.warning(f"设置杠杆失败: {e}")

            ticker = self.exchange.fetch_ticker(symbol)
            current_price = ticker['last']

            # 预计算止盈止损价格（开仓时一并提交）
            if not stop_loss:
                stop_loss = self.risk_manager.calculate_stop_loss(current_price, side)
            tp_first = take_profit[0] if take_profit else self.risk_manager.calculate_take_profit(current_price, side)

            # SL方向校验：价格可能在Judge决策后变动，导致SL落在错误一侧
            if side == 'short' and stop_loss <= current_price:
                stop_loss = current_price * 1.015
                self.logger.warning(f"SL方向修正(short): SL={stop_loss:.4f} > entry={current_price:.4f}")
            elif side == 'long' and stop_loss >= current_price:
                stop_loss = current_price * 0.985
                self.logger.warning(f"SL方向修正(long): SL={stop_loss:.4f} < entry={current_price:.4f}")

            # TP方向校验
            if tp_first:
                if side == 'short' and tp_first >= current_price:
                    tp_first = current_price * 0.97
                    self.logger.warning(f"TP方向修正(short): TP={tp_first:.4f} < entry={current_price:.4f}")
                elif side == 'long' and tp_first <= current_price:
                    tp_first = current_price * 1.03
                    self.logger.warning(f"TP方向修正(long): TP={tp_first:.4f} > entry={current_price:.4f}")

            # 构建附带TP/SL的下单参数
            tp_sl_params = self._build_tp_sl_params(side, stop_loss, tp_first)

            if order_type == 'limit' and entry_zone:
                filled = self._execute_limit_order(symbol, side, size_usdt, current_price, entry_zone, leverage, tp_sl_params)
                if filled is None:
                    return None
                amount, fill_price = filled
            else:
                if not self._check_slippage(symbol, size_usdt, current_price):
                    self.logger.info(f"滑点过大，降级为限价单")
                    if entry_zone:
                        filled = self._execute_limit_order(symbol, side, size_usdt, current_price, entry_zone, leverage, tp_sl_params)
                        if filled is None:
                            return None
                        amount, fill_price = filled
                    else:
                        return None
                else:
                    contract_value = size_usdt * leverage
                    market = self.exchange.market(symbol)
                    contract_size = float(market.get('contractSize', 1) or 1)
                    amount = float(self.exchange.amount_to_precision(
                        symbol, contract_value / (current_price * contract_size)
                    ))

                    min_amount = market.get('limits', {}).get('amount', {}).get('min', 0)
                    if min_amount and amount < min_amount:
                        self.logger.warning(f"订单数量{amount:.4f}低于最小值{min_amount}，放弃交易")
                        return None

                    order_side = 'buy' if side == 'long' else 'sell'
                    params = {'reduceOnly': False}
                    params.update(tp_sl_params)
                    self.exchange.create_order(
                        symbol=symbol, type='market', side=order_side,
                        amount=amount, params=params
                    )
                    fill_price = current_price

            # 成交后用实际成交价修正止盈止损（如果偏差较大）
            if abs(fill_price - current_price) / current_price > 0.002:
                stop_loss = self.risk_manager.calculate_stop_loss(fill_price, side) if not plan.get('stop_loss') else stop_loss
                tp_first = take_profit[0] if take_profit else self.risk_manager.calculate_take_profit(fill_price, side)

            position = {
                'symbol': symbol,
                'side': side,
                'entry_price': fill_price,
                'amount': amount,
                'amount_usdt': size_usdt,
                'leverage': leverage,
                'stop_loss': stop_loss,
                'take_profit': tp_first,
                'take_profit_levels': take_profit,
                'sl_order_id': None,
                'order_type': order_type,
            }
            self.positions[symbol] = position
            self._save_positions()

            self.logger.info(
                f"智能开仓: {side} {symbol} @ {fill_price:.2f}, "
                f"杠杆={leverage}x, SL={stop_loss}, TP={tp_first}"
            )
            return position

        except Exception as e:
            self.logger.error(f"智能开仓失败: {e}")
            return None

    def _build_tp_sl_params(self, side: str, stop_loss: float, take_profit: float) -> dict:
        """构建OKX附带止盈止损的下单参数（OCO条件单，触发后市价平仓）"""
        if not stop_loss and not take_profit:
            return {}
        algo_ord = {}
        if stop_loss:
            algo_ord['slTriggerPx'] = str(stop_loss)
            algo_ord['slOrdPx'] = '-1'
        if take_profit:
            algo_ord['tpTriggerPx'] = str(take_profit)
            algo_ord['tpOrdPx'] = '-1'
        return {'attachAlgoOrds': [algo_ord]}

    def _execute_limit_order(self, symbol: str, side: str, size_usdt: float,
                             current_price: float, entry_zone: dict,
                             leverage: int = 1, tp_sl_params: dict = None) -> Optional[tuple]:
        """限价单执行，30秒超时，附带TP/SL"""
        import time

        # 获取实时价格，防止plan过期导致限价单超出交易所允许范围
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            live_price = ticker['last']
        except Exception:
            live_price = current_price

        if isinstance(entry_zone, list):
            low, high = entry_zone[0], entry_zone[1]
        else:
            low = entry_zone.get('low', current_price * 0.999)
            high = entry_zone.get('high', current_price * 1.001)
        limit_price = (low + high) / 2

        # 限价单价格偏离实时价格超过2%时，基于实时价格重新计算
        if abs(limit_price - live_price) / live_price > 0.02:
            self.logger.warning(f"限价单价格{limit_price:.4f}偏离实时价{live_price:.4f}超2%，重新校准")
            limit_price = live_price * (0.999 if side == 'long' else 1.001)

        market = self.exchange.market(symbol)
        contract_size = float(market.get('contractSize', 1) or 1)
        amount = float(self.exchange.amount_to_precision(
            symbol, (size_usdt * leverage) / (limit_price * contract_size)
        ))
        order_side = 'buy' if side == 'long' else 'sell'

        params = {'reduceOnly': False}
        if tp_sl_params:
            params.update(tp_sl_params)

        order = self.exchange.create_order(
            symbol=symbol, type='limit', side=order_side,
            amount=amount, price=limit_price,
            params=params
        )
        order_id = order['id']
        self.logger.info(f"限价单挂出: {order_side} {amount:.6f} @ {limit_price:.2f}")

        deadline = time.time() + 30
        while time.time() < deadline:
            time.sleep(3)
            try:
                status = self.exchange.fetch_order(order_id, symbol)
                if status['status'] == 'closed':
                    fill_price = status.get('average', limit_price)
                    filled_amount = status.get('filled', amount)
                    self.logger.info(f"限价单成交: {filled_amount:.6f} @ {fill_price:.2f}")
                    return (filled_amount, fill_price)
                elif status['status'] == 'canceled':
                    return None
            except Exception:
                pass

        try:
            self.exchange.cancel_order(order_id, symbol)
        except Exception:
            pass

        ticker = self.exchange.fetch_ticker(symbol)
        new_price = ticker['last']
        price_change = abs(new_price - current_price) / current_price
        if price_change > 0.005:
            self.logger.info(f"价格变化>{price_change*100:.1f}%，放弃入场")
            return None

        amount = float(self.exchange.amount_to_precision(
            symbol, (size_usdt * leverage) / (new_price * contract_size)
        ))
        fallback_params = {'reduceOnly': False}
        if tp_sl_params:
            fallback_params.update(tp_sl_params)
        order = self.exchange.create_order(
            symbol=symbol, type='market', side=order_side,
            amount=amount, params=fallback_params
        )
        self.logger.info(f"限价单超时，市价成交: {amount:.6f} @ ~{new_price:.2f}")
        return (amount, new_price)

    def _check_slippage(self, symbol: str, size_usdt: float, current_price: float) -> bool:
        """检查滑点：spread > 0.1% 或深度不足则返回False"""
        try:
            ob = self.exchange.fetch_order_book(symbol, limit=5)
            if not ob['asks'] or not ob['bids']:
                return False
            best_ask = ob['asks'][0][0]
            best_bid = ob['bids'][0][0]
            spread = (best_ask - best_bid) / best_bid
            if spread > 0.001:
                self.logger.warning(f"spread过大: {spread*100:.3f}%")
                return False
            depth_usdt = sum(p * q for p, q in ob['asks'][:5])
            if depth_usdt < size_usdt * 3:
                self.logger.warning(f"深度不足: {depth_usdt:.0f} < {size_usdt*3:.0f}")
                return False
            return True
        except Exception:
            return True

    def place_stop_loss_order(self, symbol: str, side: str, stop_price: float,
                              amount: float) -> Optional[str]:
        """挂交易所止损条件单"""
        try:
            close_side = 'sell' if side == 'long' else 'buy'
            order = self.exchange.create_order(
                symbol=symbol,
                type='stop',
                side=close_side,
                amount=amount,
                price=stop_price,
                params={
                    'stopPrice': stop_price,
                    'reduceOnly': True,
                    'triggerPrice': stop_price,
                }
            )
            self.logger.info(f"止损条件单: {symbol} {close_side} @ {stop_price}")
            return order.get('id')
        except Exception as e:
            self.logger.warning(f"挂止损条件单失败（将用本地轮询兜底）: {e}")
            return None

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """撤单"""
        try:
            self.exchange.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            self.logger.warning(f"撤单失败: {e}")
            return False

    def sync_positions(self) -> dict:
        """从交易所同步真实持仓，以交易所为准。返回新发现的持仓列表"""
        try:
            exchange_positions = self.exchange.fetch_positions()
            active = {}
            for pos in exchange_positions:
                if pos['contracts'] and float(pos['contracts']) > 0:
                    # 统一转换为内部格式 LAYER/USDT:USDT → LAYER-USDT-SWAP
                    raw_sym = pos['symbol']
                    if '/' in raw_sym and ':' in raw_sym:
                        base = raw_sym.split('/')[0]
                        sym = f"{base}-USDT-SWAP"
                    else:
                        sym = raw_sym
                    side = 'long' if pos['side'] == 'long' else 'short'
                    lev = int(pos.get('leverage', 1)) or 1
                    notional = float(pos.get('notional', 0))
                    active[sym] = {
                        'symbol': sym,
                        'side': side,
                        'entry_price': float(pos.get('entryPrice', 0)),
                        'amount': float(pos['contracts']),
                        'amount_usdt': notional / lev,
                        'leverage': lev,
                        'unrealized_pnl': float(pos.get('unrealizedPnl', 0)),
                    }

            removed_symbols = []
            if not hasattr(self, '_removed_positions_data'):
                self._removed_positions_data = []
            cooldown = getattr(self, '_close_cooldown', {})
            now_ts = time.time()
            for sym in list(self.positions.keys()):
                if sym not in active:
                    if sym in cooldown and now_ts < cooldown[sym]:
                        continue
                    pos_data = self.positions[sym].copy()
                    pos_data['symbol'] = sym
                    self._removed_positions_data.append(pos_data)
                    self.logger.info(f"仓位同步: {sym} 已不在交易所，移除本地记录")
                    removed_symbols.append(sym)
                    del self.positions[sym]
                    self._sl_check_failures.pop(sym, None)
            if not hasattr(self, '_last_removed_symbols'):
                self._last_removed_symbols = []
            self._last_removed_symbols.extend(removed_symbols)

            newly_synced = []
            cooldown = getattr(self, '_close_cooldown', {})
            now = time.time()
            for sym, ex_pos in active.items():
                # 刚平仓的symbol在冷却期内不重新补录（防止API延迟导致幽灵持仓）
                if sym in cooldown and now < cooldown[sym]:
                    continue
                if sym in self.positions:
                    local = self.positions[sym]
                    if abs(local['amount'] - ex_pos['amount']) / max(ex_pos['amount'], 1e-8) > 0.01:
                        self.logger.info(f"仓位同步: {sym} 数量不一致，以交易所为准")
                        local['amount'] = ex_pos['amount']
                        local['amount_usdt'] = ex_pos['amount_usdt']
                    local['unrealized_pnl'] = ex_pos['unrealized_pnl']
                else:
                    entry = ex_pos['entry_price']
                    if ex_pos['side'] == 'long':
                        ex_pos['stop_loss'] = entry * 0.97
                        ex_pos['take_profit'] = entry * 1.03
                    else:
                        ex_pos['stop_loss'] = entry * 1.03
                        ex_pos['take_profit'] = entry * 0.97
                    ex_pos['take_profit_levels'] = [ex_pos['take_profit']]
                    ex_pos['sl_order_id'] = None
                    ex_pos['order_type'] = 'market'
                    self.logger.info(f"仓位同步: 发现交易所持仓 {sym}，补录本地 (SL={ex_pos['stop_loss']:.6f} TP={ex_pos['take_profit']:.6f})")
                    self.positions[sym] = ex_pos
                    newly_synced.append(ex_pos)

            self._save_positions()
            self._last_sync_result = newly_synced
            return self.positions.copy()

        except Exception as e:
            self.logger.error(f"仓位同步失败: {e}")
            self._last_sync_result = []
            return self.positions.copy()

    def get_newly_synced(self) -> list:
        """获取上次sync_positions发现的新持仓（供agent层发布通知）"""
        result = getattr(self, '_last_sync_result', [])
        self._last_sync_result = []
        return result

    def get_removed_symbols(self) -> list:
        """获取上次sync_positions发现的已被交易所平仓的标的"""
        result = getattr(self, '_last_removed_symbols', [])
        self._last_removed_symbols = []
        return result

    def get_removed_positions_data(self) -> list:
        """获取被移除持仓的完整数据（含entry_price/side/stop_loss，用于计算PnL）"""
        result = getattr(self, '_removed_positions_data', [])
        self._removed_positions_data = []
        return result

    def reduce_position(self, symbol: str, pct: float) -> Optional[Dict]:
        """减仓指定百分比

        参考：Binance/OKX reduceOnly模式 + Freqtrade partial exit
        减仓后取消旧SL条件单（数量不匹配会导致交易所拒绝）
        """
        if symbol not in self.positions:
            return None
        try:
            position = self.positions[symbol]
            reduce_amount = position['amount'] * pct

            # 精度处理：用交易所精度格式化
            try:
                reduce_amount = float(self.exchange.amount_to_precision(symbol, reduce_amount))
            except Exception:
                pass

            if reduce_amount <= 0:
                return None

            # 减仓前取消旧SL条件单（数量变化后旧单无效）
            if position.get('sl_order_id'):
                try:
                    self.exchange.cancel_order(position['sl_order_id'], symbol)
                    self.logger.info(f"减仓前取消旧SL单: {position['sl_order_id']}")
                except Exception:
                    pass
                position['sl_order_id'] = None

            order_side = 'sell' if position['side'] == 'long' else 'buy'
            order = self.exchange.create_order(
                symbol=symbol, type='market', side=order_side,
                amount=reduce_amount, params={'reduceOnly': True}
            )

            position['amount'] -= reduce_amount
            position['amount_usdt'] *= (1 - pct)

            # 浮点精度兜底：剩余量极小时视为全平
            min_amount = self.exchange.market(symbol).get('limits', {}).get('amount', {}).get('min', 1e-8)
            if position['amount'] < max(min_amount, 1e-8):
                del self.positions[symbol]
                self.logger.info(f"减仓后剩余量过小，视为全平: {symbol}")
            self._save_positions()

            self.logger.info(f"减仓: {symbol} 减{pct*100:.0f}%, 剩余{position.get('amount', 0):.6f}")
            return {'symbol': symbol, 'reduced_pct': pct, 'order': order}

        except Exception as e:
            self.logger.error(f"减仓失败: {e}")
            return None

    def add_to_position(self, symbol: str, side: str, size_pct: float = 0.3) -> Optional[Dict]:
        """加仓：在已有持仓方向上追加仓位

        Args:
            symbol: 标的
            side: 方向（必须与现有持仓一致）
            size_pct: 加仓比例（相对于max_trade_amount）

        参考：Freqtrade adjust_trade_position + stoploss_on_exchange_update
        加仓后重新计算SL/TP以反映新均价
        """
        symbol = self._normalize_symbol(symbol)
        if symbol not in self.positions:
            self.logger.warning(f"加仓失败: {symbol} 无持仓")
            return None

        position = self.positions[symbol]
        if position['side'] != side:
            self.logger.warning(f"加仓方向冲突: 持仓{position['side']} vs 请求{side}")
            return None

        # 加仓上限：总保证金不超过max_trade_amount的2倍
        current_margin = position.get('amount_usdt', 0)
        max_total = self.risk_manager.max_trade_amount * 2
        if current_margin >= max_total:
            self.logger.warning(f"加仓拒绝: 已达保证金上限 {current_margin:.2f} >= {max_total:.2f}")
            return None

        try:
            balance = self.get_balance()
            can_trade, msg = self.risk_manager.check_can_trade(balance)
            if not can_trade:
                self.logger.warning(f"加仓风控拒绝: {msg}")
                return None

            add_usdt = self.risk_manager.max_trade_amount * size_pct
            # 不超过上限
            add_usdt = min(add_usdt, max_total - current_margin)
            if add_usdt < 1.0:
                self.logger.warning(f"加仓金额过小: {add_usdt:.2f} USDT，放弃")
                return None

            free_balance = self.exchange.fetch_balance()['USDT']['free']
            if free_balance < add_usdt * 1.1:
                self.logger.warning(f"加仓余额不足: free={free_balance:.2f} < 需要{add_usdt:.2f}")
                return None

            leverage = position.get('leverage', self.leverage)
            try:
                self.exchange.set_leverage(leverage, symbol)
            except Exception:
                pass

            ticker = self.exchange.fetch_ticker(symbol)
            current_price = ticker['last']

            contract_value = add_usdt * leverage
            market = self.exchange.market(symbol)
            contract_size = float(market.get('contractSize', 1) or 1)
            amount = float(self.exchange.amount_to_precision(
                symbol, contract_value / (current_price * contract_size)
            ))

            min_amount = market.get('limits', {}).get('amount', {}).get('min', 0)
            if min_amount and amount < min_amount:
                self.logger.warning(f"加仓数量{amount:.4f}低于最小值{min_amount}，放弃")
                return None

            order_side = 'buy' if side == 'long' else 'sell'
            order = self.exchange.create_order(
                symbol=symbol, type='market', side=order_side,
                amount=amount, params={'reduceOnly': False}
            )

            # 更新持仓记录：加权平均入场价
            old_amount = position['amount']
            old_entry = position['entry_price']
            new_total = old_amount + amount
            position['entry_price'] = (old_entry * old_amount + current_price * amount) / new_total
            position['amount'] = new_total
            position['amount_usdt'] = current_margin + add_usdt

            # 加仓后基于新均价重算SL/TP（Freqtrade stoploss_on_exchange_update模式）
            new_entry = position['entry_price']
            old_sl = position.get('stop_loss')
            if old_sl and old_entry > 0:
                # 保持原SL距离比例
                sl_dist_pct = abs(old_sl - old_entry) / old_entry
                if side == 'long':
                    position['stop_loss'] = new_entry * (1 - sl_dist_pct)
                else:
                    position['stop_loss'] = new_entry * (1 + sl_dist_pct)

            old_tp = position.get('take_profit')
            if old_tp and old_entry > 0:
                tp_dist_pct = abs(old_tp - old_entry) / old_entry
                if side == 'long':
                    position['take_profit'] = new_entry * (1 + tp_dist_pct)
                else:
                    position['take_profit'] = new_entry * (1 - tp_dist_pct)

            # 加仓后更新交易所SL条件单（旧单数量/价格不匹配）
            if position.get('sl_order_id'):
                try:
                    self.exchange.cancel_order(position['sl_order_id'], symbol)
                    self.logger.info(f"加仓后取消旧SL单: {position['sl_order_id']}")
                except Exception:
                    pass
                position['sl_order_id'] = None

            new_sl = position.get('stop_loss')
            if new_sl:
                sl_order_id = self.place_stop_loss_order(
                    symbol, side, new_sl, new_total
                )
                position['sl_order_id'] = sl_order_id

            self._save_positions()

            self.logger.info(
                f"加仓成功: {side} {symbol} +{amount:.4f} @ {current_price:.4f}, "
                f"均价={position['entry_price']:.4f}, 总量={new_total:.4f}, "
                f"新SL={position.get('stop_loss')}, 新TP={position.get('take_profit')}"
            )
            return {
                'symbol': symbol,
                'side': side,
                'add_amount': amount,
                'add_amount_usdt': add_usdt,
                'fill_price': current_price,
                'new_entry_price': position['entry_price'],
                'new_total_amount': new_total,
                'new_stop_loss': position.get('stop_loss'),
                'new_take_profit': position.get('take_profit'),
                'order': order,
            }

        except Exception as e:
            self.logger.error(f"加仓失败: {e}")
            return None
