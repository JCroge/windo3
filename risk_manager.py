#!/usr/bin/env python3
"""风控管理器 - 严格控制交易风险"""

import json
import os
from typing import Dict, Optional
from datetime import datetime, timedelta


class RiskManager:
    """风控管理器"""

    def __init__(self,
                 max_trade_amount: float = 500.0,
                 max_drawdown_pct: float = 20.0,
                 max_daily_loss: float = 300.0,
                 stop_loss_pct: float = 2.0,
                 take_profit_pct: float = 5.0,
                 state_file: str = 'data/risk_state.json'):
        """
        Args:
            max_trade_amount: 单次最大交易额（USDT）
            max_drawdown_pct: 最大回撤百分比
            max_daily_loss: 每日最大亏损（USDT）
            stop_loss_pct: 止损百分比
            take_profit_pct: 止盈百分比
            state_file: 状态持久化文件路径
        """
        self.max_trade_amount = max_trade_amount
        self.max_drawdown_pct = max_drawdown_pct
        self.max_daily_loss = max_daily_loss
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.state_file = state_file

        # 交易记录
        self.daily_pnl = 0.0
        self.peak_balance = 0.0
        self.current_balance = 0.0
        self.last_reset_date = datetime.now().date()

        # 加载持久化状态
        self._load_state()

    def check_can_trade(self, balance: float) -> tuple[bool, str]:
        """检查是否可以交易"""
        self._update_daily_reset()
        self.current_balance = balance

        # 更新峰值余额
        if balance > self.peak_balance:
            self.peak_balance = balance
            self._save_state()

        # 检查每日亏损（只限制亏损，不限制盈利）
        if self.daily_pnl <= -self.max_daily_loss:
            return False, f"已达每日最大亏损限制 {self.max_daily_loss} USDT"

        # 检查回撤
        if self.peak_balance > 0:
            drawdown_pct = (self.peak_balance - balance) / self.peak_balance * 100
            if drawdown_pct >= self.max_drawdown_pct:
                return False, f"已达最大回撤限制 {self.max_drawdown_pct}%"

        # 检查余额是否足够
        if balance < self.max_trade_amount:
            return False, f"余额不足，需要至少 {self.max_trade_amount} USDT"

        return True, "风控检查通过"

    def calculate_position_size(self, balance: float) -> float:
        """计算仓位大小（USDT）"""
        # 使用固定金额，不超过最大交易额
        return min(self.max_trade_amount, balance * 0.1)  # 最多使用10%余额

    def calculate_stop_loss(self, entry_price: float, side: str) -> float:
        """计算止损价格"""
        if side == 'long':
            return entry_price * (1 - self.stop_loss_pct / 100)
        else:  # short
            return entry_price * (1 + self.stop_loss_pct / 100)

    def calculate_take_profit(self, entry_price: float, side: str) -> float:
        """计算止盈价格"""
        if side == 'long':
            return entry_price * (1 + self.take_profit_pct / 100)
        else:  # short
            return entry_price * (1 - self.take_profit_pct / 100)

    def record_trade(self, pnl: float):
        """记录交易盈亏"""
        self._update_daily_reset()
        self.daily_pnl += pnl
        self._save_state()  # 持久化，防止崩溃后绕过当日熔断

    def _update_daily_reset(self):
        """每日重置"""
        today = datetime.now().date()
        if today > self.last_reset_date:
            self.daily_pnl = 0.0
            self.last_reset_date = today
            self._save_state()

    def _load_state(self):
        """加载持久化状态（peak_balance + daily_pnl + last_reset_date）"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.peak_balance = state.get('peak_balance', 0.0)
                    # 加载 daily_pnl，但只有日期匹配才用（跨天则重置）
                    saved_date_str = state.get('last_reset_date')
                    today = datetime.now().date()
                    if saved_date_str:
                        try:
                            saved_date = datetime.strptime(saved_date_str, '%Y-%m-%d').date()
                            if saved_date == today:
                                self.daily_pnl = state.get('daily_pnl', 0.0)
                                self.last_reset_date = saved_date
                            else:
                                # 跨天，daily_pnl 自动归零
                                self.daily_pnl = 0.0
                                self.last_reset_date = today
                        except ValueError:
                            pass
            except Exception:
                pass  # 文件损坏时使用默认值

    def _save_state(self):
        """保存持久化状态（peak_balance + daily_pnl + last_reset_date），原子写入"""
        try:
            from utils.atomic_io import atomic_write_json
            atomic_write_json(self.state_file, {
                'peak_balance': self.peak_balance,
                'daily_pnl': self.daily_pnl,
                'last_reset_date': self.last_reset_date.strftime('%Y-%m-%d'),
            })
        except Exception:
            pass  # 保存失败不影响交易

    def get_status(self) -> Dict:
        """获取风控状态"""
        drawdown_pct = 0.0
        if self.peak_balance > 0:
            drawdown_pct = (self.peak_balance - self.current_balance) / self.peak_balance * 100

        return {
            'daily_pnl': self.daily_pnl,
            'daily_loss_limit': self.max_daily_loss,
            'daily_loss_used_pct': abs(self.daily_pnl) / self.max_daily_loss * 100,
            'current_drawdown_pct': drawdown_pct,
            'max_drawdown_pct': self.max_drawdown_pct,
            'peak_balance': self.peak_balance,
            'current_balance': self.current_balance
        }
