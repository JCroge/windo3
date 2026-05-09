"""交易复盘与策略衰减检测 Agent"""

import asyncio
import json
import os
import time
import datetime
from agents.base import BaseAgent


class ReviewerAgent(BaseAgent):
    name = "reviewer"
    subscriptions = ["execution_result", "research_trigger", "risk_alert"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.trade_history = []
        self.history_file = 'data/trade_history.json'

        # 滚动窗口配置
        self.rolling_window_size = config.get('rolling_window_size', 20) if config else 20

        # 策略衰减阈值
        self.decay_threshold_win_rate = config.get('decay_threshold_win_rate', 0.50) if config else 0.50
        self.decay_threshold_profit_factor = config.get('decay_threshold_profit_factor', 1.5) if config else 1.5

        # Daily hard stop阈值
        self.daily_pnl_hard_stop = config.get('daily_pnl_hard_stop', -50.0) if config else -50.0
        self.consecutive_loss_limit = config.get('consecutive_loss_limit', 3) if config else 3

    async def setup(self):
        self._load_trade_history()
        self.logger.info(f"交易复盘Agent就绪 (历史{len(self.trade_history)}笔)")

    async def on_message(self, msg: dict):
        if msg['type'] == 'execution_result':
            await self._process_trade_result(msg)
            await self._check_daily_hard_stop()
        elif msg['type'] == 'research_trigger':
            await self._run_strategy_review()
        elif msg['type'] == 'risk_alert':
            # 组合回撤超限时，将浮亏计入当日PnL触发熔断
            if msg['payload'].get('type') == 'max_drawdown':
                unrealized = msg['payload'].get('total_pnl_usdt', 0)
                daily_pnl = self._calculate_daily_pnl() + unrealized
                if daily_pnl <= self.daily_pnl_hard_stop:
                    self.logger.critical(f"[熔断] 含浮亏当日亏损{daily_pnl:.2f} USDT 超过限制")
                    await self.publish("daily_hard_stop_triggered", {
                        "reason": "daily_loss_limit_with_unrealized",
                        "daily_pnl": daily_pnl,
                        "limit": self.daily_pnl_hard_stop,
                        "timestamp": time.time()
                    })

    async def tick(self):
        await asyncio.sleep(60)

    async def _process_trade_result(self, msg: dict):
        """处理交易结果，记录到历史"""
        payload = msg['payload']
        status = payload.get('status')

        # 只记录已完成的交易（executed或force_closed）
        if status not in ('executed', 'force_closed'):
            return

        action = payload.get('action', '')
        result = payload.get('result', {})

        # 只记录开仓，平仓时计算盈亏
        if action in ('open_long', 'open_short'):
            # 记录开仓信息，等待平仓时补充
            pass
        elif action == 'close' or status == 'force_closed':
            # 平仓：记录完整交易
            symbol = msg.get('symbol') or payload.get('symbol')
            pnl = result.get('pnl', 0)

            trade_record = {
                'timestamp': msg['timestamp'],
                'symbol': symbol,
                'status': status,
                'pnl': pnl,
                'confidence': payload.get('confidence', 0),
            }

            self.trade_history.append(trade_record)
            self._save_trade_history()

            self.logger.info(f"[复盘] 记录交易: {symbol} PnL={pnl:.2f} USDT")

    async def _check_daily_hard_stop(self):
        """检查daily hard stop触发条件"""
        daily_pnl = self._calculate_daily_pnl()
        consecutive_losses = self._track_consecutive_losses()

        # 条件1: 单日亏损超限
        if daily_pnl <= self.daily_pnl_hard_stop:
            self.logger.critical(
                f"[熔断] 单日亏损{daily_pnl:.2f} USDT 超过限制{self.daily_pnl_hard_stop} USDT"
            )
            await self.publish("daily_hard_stop_triggered", {
                "reason": "daily_loss_limit",
                "daily_pnl": daily_pnl,
                "limit": self.daily_pnl_hard_stop,
                "timestamp": time.time()
            })

        # 条件2: 连续亏损超限
        elif consecutive_losses >= self.consecutive_loss_limit:
            self.logger.critical(
                f"[熔断] 连续{consecutive_losses}次亏损 超过限制{self.consecutive_loss_limit}次"
            )
            await self.publish("daily_hard_stop_triggered", {
                "reason": "consecutive_losses",
                "count": consecutive_losses,
                "limit": self.consecutive_loss_limit,
                "timestamp": time.time()
            })

    def _calculate_daily_pnl(self) -> float:
        """计算当日累计盈亏"""
        if not self.trade_history:
            return 0.0

        today = datetime.datetime.utcnow().date()
        daily_trades = [
            t for t in self.trade_history
            if datetime.datetime.utcfromtimestamp(t['timestamp']).date() == today
        ]

        return sum(t['pnl'] for t in daily_trades)

    def _track_consecutive_losses(self) -> int:
        """追踪连续亏损次数"""
        if not self.trade_history:
            return 0

        consecutive_count = 0
        for trade in reversed(self.trade_history):
            if trade['pnl'] < 0:
                consecutive_count += 1
            else:
                break

        return consecutive_count

    async def _run_strategy_review(self):
        """策略复盘（每12h触发一次）"""
        if len(self.trade_history) < 5:
            self.logger.info("[复盘] 交易历史不足5笔，跳过复盘")
            return

        recent_metrics = self._calculate_rolling_metrics()
        decay_signals = self._detect_strategy_decay()
        consecutive_losses = self._track_consecutive_losses()
        daily_pnl = self._calculate_daily_pnl()

        review_report = {
            'timestamp': time.time(),
            'recent_metrics': recent_metrics,
            'decay_signals': decay_signals,
            'consecutive_losses': consecutive_losses,
            'daily_pnl': daily_pnl,
            'total_trades': len(self.trade_history),
        }

        # 生成建议
        if decay_signals:
            self.logger.warning(
                f"[复盘] 策略衰减检测: {len(decay_signals)}个指标异常"
            )
            for signal in decay_signals:
                self.logger.warning(
                    f"  - {signal['metric']}: {signal['recent']:.2f} "
                    f"(历史{signal['historical']:.2f}, 阈值{signal['threshold']:.2f})"
                )

        self.logger.info(
            f"[复盘] 近{self.rolling_window_size}笔: "
            f"胜率{recent_metrics['win_rate']:.1%}, "
            f"盈亏比{recent_metrics['profit_factor']:.2f}, "
            f"总盈亏{recent_metrics['total_pnl']:.2f} USDT"
        )

        await self.publish("strategy_review", review_report)

    def _calculate_rolling_metrics(self) -> dict:
        """计算滚动窗口指标"""
        window_size = min(self.rolling_window_size, len(self.trade_history))
        if window_size == 0:
            return {
                'win_rate': 0, 'profit_factor': 0, 'total_pnl': 0,
                'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0
            }

        recent_trades = self.trade_history[-window_size:]
        winning_trades = [t for t in recent_trades if t['pnl'] > 0]
        losing_trades = [t for t in recent_trades if t['pnl'] < 0]

        win_rate = len(winning_trades) / len(recent_trades) if recent_trades else 0

        gross_profit = sum(t['pnl'] for t in winning_trades) if winning_trades else 0
        gross_loss = abs(sum(t['pnl'] for t in losing_trades)) if losing_trades else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0)

        total_pnl = sum(t['pnl'] for t in recent_trades)

        return {
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'total_pnl': total_pnl,
            'total_trades': len(recent_trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'gross_profit': gross_profit,
            'gross_loss': gross_loss,
        }

    def _detect_strategy_decay(self):
        """检测策略衰减（对比近期 vs 历史基线）"""
        if len(self.trade_history) < self.rolling_window_size * 2:
            return None

        # 历史基线：前半部分交易
        mid_point = len(self.trade_history) // 2
        historical_trades = self.trade_history[:mid_point]
        historical_metrics = self._calculate_metrics_for_trades(historical_trades)

        # 近期表现
        recent_metrics = self._calculate_rolling_metrics()

        decay_signals = []

        # 胜率衰减
        if recent_metrics['win_rate'] < self.decay_threshold_win_rate:
            decay_signals.append({
                'metric': 'win_rate',
                'historical': historical_metrics['win_rate'],
                'recent': recent_metrics['win_rate'],
                'threshold': self.decay_threshold_win_rate,
                'severity': 'high' if recent_metrics['win_rate'] < 0.40 else 'medium'
            })

        # 盈亏比衰减
        if recent_metrics['profit_factor'] < self.decay_threshold_profit_factor:
            decay_signals.append({
                'metric': 'profit_factor',
                'historical': historical_metrics['profit_factor'],
                'recent': recent_metrics['profit_factor'],
                'threshold': self.decay_threshold_profit_factor,
                'severity': 'high' if recent_metrics['profit_factor'] < 1.0 else 'medium'
            })

        return decay_signals if decay_signals else None

    def _calculate_metrics_for_trades(self, trades: list) -> dict:
        """计算指定交易列表的指标"""
        if not trades:
            return {'win_rate': 0, 'profit_factor': 0}

        winning = [t for t in trades if t['pnl'] > 0]
        losing = [t for t in trades if t['pnl'] < 0]

        win_rate = len(winning) / len(trades)
        gross_profit = sum(t['pnl'] for t in winning) if winning else 0
        gross_loss = abs(sum(t['pnl'] for t in losing)) if losing else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0)

        return {'win_rate': win_rate, 'profit_factor': profit_factor}

    def _load_trade_history(self):
        """加载交易历史"""
        if not os.path.exists(self.history_file):
            return

        try:
            with open(self.history_file, 'r') as f:
                self.trade_history = json.load(f)
            self.logger.info(f"加载交易历史: {len(self.trade_history)}笔")
        except Exception as e:
            self.logger.error(f"加载交易历史失败: {e}")

    def _save_trade_history(self):
        """保存交易历史"""
        os.makedirs('data', exist_ok=True)
        try:
            with open(self.history_file, 'w') as f:
                json.dump(self.trade_history, f, indent=2)
        except Exception as e:
            self.logger.error(f"保存交易历史失败: {e}")
