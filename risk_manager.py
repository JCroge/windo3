#!/usr/bin/env python3
"""风控管理器 - 严格控制交易风险"""

import json
import os
import time
import logging
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
                 state_file: Optional[str] = None,
                 effective_balance_cap: Optional[float] = None,
                 baseline_mode: str = "session_start"):
        self.max_trade_amount = max_trade_amount
        self.max_drawdown_pct = max_drawdown_pct
        self.max_daily_loss = max_daily_loss
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        if state_file is None:
            # FR-008: 未显式指定时按 STATE_NAMESPACE 派生
            from utils.state_paths import get_state_paths
            state_file = get_state_paths().risk_state
        self.state_file = state_file
        self.effective_balance_cap = effective_balance_cap
        self.baseline_mode = baseline_mode
        self.logger = logging.getLogger("RiskManager")

        # 交易记录
        self.daily_pnl = 0.0
        self.peak_balance = 0.0
        self.current_balance = 0.0
        self.last_reset_date = datetime.now().date()

        # Session-based drawdown tracking
        self.session_baseline_equity = 0.0
        self.session_peak_equity = 0.0
        self._session_initialized = False

        # 加载持久化状态
        self._load_state()

    def initialize_session(self, real_total_balance: float,
                           effective_balance_cap: Optional[float] = None) -> float:
        """启动时初始化本轮风控基准。返回 risk_equity。"""
        self._update_daily_reset()
        if effective_balance_cap is not None:
            self.effective_balance_cap = effective_balance_cap

        risk_equity = self._calc_risk_equity(real_total_balance)

        if self.baseline_mode == "session_start":
            self.session_baseline_equity = risk_equity
            self.session_peak_equity = risk_equity
        else:
            # persisted_peak: 兼容旧行为，用持久化的 peak
            if self.peak_balance > 0:
                self.session_peak_equity = min(self.peak_balance, risk_equity) if self.effective_balance_cap else self.peak_balance
                self.session_baseline_equity = risk_equity
            else:
                self.session_baseline_equity = risk_equity
                self.session_peak_equity = risk_equity

        self._session_initialized = True
        self.current_balance = real_total_balance
        self._save_state()

        self.logger.info(
            f"[RiskBaseline] real_total={real_total_balance:.2f} "
            f"cap={self.effective_balance_cap or 'None'} "
            f"risk_equity={risk_equity:.2f} "
            f"mode={self.baseline_mode} "
            f"peak={self.session_peak_equity:.2f}"
        )
        return risk_equity

    def _calc_risk_equity(self, real_total_balance: float) -> float:
        """计算风险权益：min(real_total, cap)"""
        if self.effective_balance_cap and self.effective_balance_cap > 0:
            return min(real_total_balance, self.effective_balance_cap)
        return real_total_balance

    def check_can_open(self, real_total_balance: float,
                       effective_balance_cap: Optional[float] = None) -> tuple:
        """检查是否可以新增风险（open/add）。使用 session 基准。"""
        self._update_daily_reset()
        self.current_balance = real_total_balance

        cap = effective_balance_cap if effective_balance_cap is not None else self.effective_balance_cap
        risk_equity = min(real_total_balance, cap) if cap and cap > 0 else real_total_balance

        # 更新 session peak（棘轮）
        if self._session_initialized and risk_equity > self.session_peak_equity:
            self.session_peak_equity = risk_equity
            self._save_state()

        # 同时维护旧 peak_balance（兼容）
        if real_total_balance > self.peak_balance:
            self.peak_balance = real_total_balance

        # 检查每日亏损
        if self.daily_pnl <= -self.max_daily_loss:
            return False, f"已达每日最大亏损限制 {self.max_daily_loss} USDT"

        # 检查回撤（基于 session peak）
        if self._session_initialized and self.session_peak_equity > 0:
            drawdown_pct = (self.session_peak_equity - risk_equity) / self.session_peak_equity * 100
            if drawdown_pct >= self.max_drawdown_pct:
                return False, (
                    f"已达最大回撤限制: drawdown={drawdown_pct:.1f}% "
                    f"risk_equity={risk_equity:.1f} "
                    f"peak={self.session_peak_equity:.1f} "
                    f"mode={self.baseline_mode}"
                )
        elif self.peak_balance > 0:
            # fallback: session 未初始化时用旧逻辑
            drawdown_pct = (self.peak_balance - real_total_balance) / self.peak_balance * 100
            if drawdown_pct >= self.max_drawdown_pct:
                return False, f"已达最大回撤限制 {self.max_drawdown_pct}%"

        # 检查余额是否足够
        if risk_equity < self.max_trade_amount:
            return False, f"余额不足，需要至少 {self.max_trade_amount} USDT"

        return True, "风控检查通过"

    def check_can_trade(self, balance: float) -> tuple:
        """向后兼容接口，委托到 check_can_open"""
        return self.check_can_open(balance, self.effective_balance_cap)

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

    def sync_from_ledger(self, ledger) -> None:
        """启动时从 LiveLedger 同步当日真实 PnL（修正重启后的 daily_pnl 偏差）"""
        try:
            ledger_daily = ledger.daily_realized_pnl()
            if abs(ledger_daily - self.daily_pnl) > 0.01:
                old = self.daily_pnl
                self.daily_pnl = ledger_daily
                self._save_state()
                if hasattr(self, 'logger'):
                    self.logger.info(f"[RiskManager] 从Ledger同步daily_pnl: {old:.4f} → {ledger_daily:.4f}")
        except Exception:
            pass

    def _update_daily_reset(self):
        """每日重置"""
        today = datetime.now().date()
        if today > self.last_reset_date:
            self.daily_pnl = 0.0
            self.last_reset_date = today
            self._save_state()

    def _load_state(self):
        """加载持久化状态，兼容 v1 和 v2 schema"""
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)

            # v2 schema
            if state.get('schema_version') == 'risk_state.v2':
                self.session_baseline_equity = state.get('session_baseline_equity', 0.0)
                self.session_peak_equity = state.get('session_peak_equity', 0.0)
                self.peak_balance = state.get('legacy_peak_balance', 0.0)
                self._session_initialized = self.session_peak_equity > 0
                saved_mode = state.get('baseline_mode', 'session_start')
                if saved_mode == 'persisted_peak':
                    self.baseline_mode = saved_mode
            else:
                # v1 schema: 迁移旧 peak_balance
                self.peak_balance = state.get('peak_balance', 0.0)

            # daily_pnl 跨天重置逻辑（v1/v2 通用）
            saved_date_str = state.get('last_reset_date')
            today = datetime.now().date()
            if saved_date_str:
                try:
                    saved_date = datetime.strptime(saved_date_str, '%Y-%m-%d').date()
                    if saved_date == today:
                        self.daily_pnl = state.get('daily_pnl', 0.0)
                        self.last_reset_date = saved_date
                    else:
                        self.daily_pnl = 0.0
                        self.last_reset_date = today
                except ValueError:
                    pass
        except Exception:
            pass

    def _save_state(self):
        """保存 v2 schema 状态，原子写入"""
        try:
            from utils.atomic_io import atomic_write_json
            atomic_write_json(self.state_file, {
                'schema_version': 'risk_state.v2',
                'baseline_mode': self.baseline_mode,
                'session_started_at': getattr(self, '_session_started_at', time.time()),
                'real_total_at_start': self.current_balance,
                'effective_balance_cap': self.effective_balance_cap,
                'session_baseline_equity': self.session_baseline_equity,
                'session_peak_equity': self.session_peak_equity,
                'current_risk_equity': self._calc_risk_equity(self.current_balance) if self.current_balance > 0 else 0.0,
                'daily_pnl': self.daily_pnl,
                'last_reset_date': self.last_reset_date.strftime('%Y-%m-%d'),
                'legacy_peak_balance': self.peak_balance,
            })
        except Exception:
            pass

    def get_status(self) -> Dict:
        """获取风控状态"""
        drawdown_pct = 0.0
        if self._session_initialized and self.session_peak_equity > 0:
            risk_eq = self._calc_risk_equity(self.current_balance) if self.current_balance > 0 else 0.0
            drawdown_pct = (self.session_peak_equity - risk_eq) / self.session_peak_equity * 100
        elif self.peak_balance > 0:
            drawdown_pct = (self.peak_balance - self.current_balance) / self.peak_balance * 100

        return {
            'daily_pnl': self.daily_pnl,
            'daily_loss_limit': self.max_daily_loss,
            'daily_loss_used_pct': abs(self.daily_pnl) / self.max_daily_loss * 100 if self.max_daily_loss > 0 else 0,
            'current_drawdown_pct': drawdown_pct,
            'max_drawdown_pct': self.max_drawdown_pct,
            'peak_balance': self.peak_balance,
            'session_peak_equity': self.session_peak_equity,
            'session_baseline_equity': self.session_baseline_equity,
            'baseline_mode': self.baseline_mode,
            'current_balance': self.current_balance,
        }
