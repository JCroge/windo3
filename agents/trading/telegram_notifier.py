"""Telegram告警Agent - 实时推送交易通知、风控告警、每日摘要"""

import asyncio
import time
import datetime
import aiohttp
from agents.base import BaseAgent


class TelegramNotifier(BaseAgent):
    name = "telegram_notifier"
    subscriptions = [
        "execution_result",
        "daily_hard_stop_triggered",
        "risk_alert",
        "strategy_review",
    ]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self._bot_token = config.get('telegram_bot_token', '') if config else ''
        self._chat_id = config.get('telegram_chat_id', '') if config else ''
        self._enabled = bool(self._bot_token and self._chat_id)
        self._daily_summary = {
            'trades': 0, 'pnl': 0.0, 'wins': 0, 'losses': 0,
            'alerts': 0, 'last_reset': None
        }
        self._msg_queue = asyncio.Queue()
        self._rate_limit_interval = 1.0
        self._last_send_time = 0

    async def setup(self):
        if not self._enabled:
            self.logger.warning("Telegram通知未启用（缺少BOT_TOKEN或CHAT_ID）")
            return
        self._reset_daily_summary()
        ok = await self._send_message("🟢 交易系统启动")
        if ok:
            self.logger.info("Telegram通知Agent就绪")
        else:
            self.logger.error("Telegram连接失败，通知功能降级")
            self._enabled = False

    async def on_message(self, msg: dict):
        if not self._enabled:
            return

        if msg['type'] == 'execution_result':
            await self._handle_execution(msg)
        elif msg['type'] == 'daily_hard_stop_triggered':
            await self._handle_hard_stop(msg)
        elif msg['type'] == 'risk_alert':
            await self._handle_risk_alert(msg)
        elif msg['type'] == 'strategy_review':
            await self._handle_strategy_review(msg)

    async def tick(self):
        await asyncio.sleep(30)
        if not self._enabled:
            return
        self._check_daily_reset()

    async def _handle_execution(self, msg: dict):
        payload = msg['payload']
        status = payload.get('status')
        symbol = msg.get('symbol') or payload.get('symbol', '?')
        action = payload.get('action', '')
        result = payload.get('result', {})

        if status == 'executed' and action in ('open_long', 'open_short'):
            side = '🟢 做多' if action == 'open_long' else '🔴 做空'
            leverage = result.get('leverage', '?')
            amount = result.get('amount_usdt', '?')
            text = (
                f"{side} {symbol}\n"
                f"杠杆: {leverage}x | 仓位: {amount} USDT\n"
                f"置信度: {payload.get('confidence', '?')}%"
            )
            await self._send_message(text)

        elif status in ('executed', 'force_closed') and (action == 'close' or status == 'force_closed'):
            pnl = result.get('pnl', 0)
            emoji = '💰' if pnl > 0 else '💸'
            reason = payload.get('reason', '主动平仓')
            text = f"{emoji} 平仓 {symbol}\nPnL: {pnl:+.2f} USDT | 原因: {reason}"
            await self._send_message(text)
            self._update_daily_summary(pnl)

    async def _handle_hard_stop(self, msg: dict):
        payload = msg['payload']
        reason = payload.get('reason', 'unknown')
        if reason == 'daily_loss_limit':
            daily_pnl = payload.get('daily_pnl', 0)
            text = f"🚨 熔断触发: 单日亏损 {daily_pnl:.2f} USDT"
        else:
            count = payload.get('count', 0)
            text = f"🚨 熔断触发: 连续{count}次亏损"
        text += "\n⛔ 系统已停止交易，需手动恢复"
        await self._send_message(text)

    async def _handle_risk_alert(self, msg: dict):
        payload = msg['payload']
        alert_type = payload.get('type', '')
        symbol = payload.get('symbol', '')
        self._daily_summary['alerts'] += 1

        critical_types = ('flash_move', 'max_drawdown', 'emergency_close')
        if alert_type not in critical_types:
            return

        type_names = {
            'flash_move': '⚡ 闪崩',
            'max_drawdown': '📉 最大回撤',
            'emergency_close': '🆘 紧急平仓',
        }
        name = type_names.get(alert_type, alert_type)
        text = f"{name} {symbol}"

        if alert_type == 'flash_move':
            text += f"\n变动: {payload.get('magnitude_pct', 0):.1f}%"
        elif alert_type == 'max_drawdown':
            text += f"\n回撤: {payload.get('drawdown_pct', 0):.1f}%"

        await self._send_message(text)

    async def _handle_strategy_review(self, msg: dict):
        payload = msg['payload']
        metrics = payload.get('recent_metrics', {})
        decay = payload.get('decay_signals')

        text = (
            f"📊 策略复盘\n"
            f"近{metrics.get('total_trades', 0)}笔: "
            f"胜率{metrics.get('win_rate', 0):.0%} | "
            f"盈亏比{metrics.get('profit_factor', 0):.2f}\n"
            f"总PnL: {metrics.get('total_pnl', 0):+.2f} USDT"
        )

        if decay:
            text += f"\n⚠️ 策略衰减: {len(decay)}个指标异常"

        await self._send_message(text)

    def _update_daily_summary(self, pnl: float):
        self._daily_summary['trades'] += 1
        self._daily_summary['pnl'] += pnl
        if pnl > 0:
            self._daily_summary['wins'] += 1
        elif pnl < 0:
            self._daily_summary['losses'] += 1

    def _check_daily_reset(self):
        now = datetime.datetime.utcnow()
        last_reset = self._daily_summary.get('last_reset')
        if last_reset and last_reset.date() < now.date():
            asyncio.create_task(self._send_daily_summary())
            self._reset_daily_summary()

    def _reset_daily_summary(self):
        self._daily_summary = {
            'trades': 0, 'pnl': 0.0, 'wins': 0, 'losses': 0,
            'alerts': 0, 'last_reset': datetime.datetime.utcnow()
        }

    async def _send_daily_summary(self):
        s = self._daily_summary
        if s['trades'] == 0 and s['alerts'] == 0:
            return
        win_rate = s['wins'] / s['trades'] * 100 if s['trades'] > 0 else 0
        emoji = '📈' if s['pnl'] >= 0 else '📉'
        text = (
            f"{emoji} 每日摘要\n"
            f"交易: {s['trades']}笔 | 胜率: {win_rate:.0f}%\n"
            f"盈亏: {s['pnl']:+.2f} USDT\n"
            f"风控告警: {s['alerts']}次"
        )
        await self._send_message(text)

    async def _send_message(self, text: str) -> bool:
        if not self._enabled:
            return False

        now = time.time()
        wait = self._rate_limit_interval - (now - self._last_send_time)
        if wait > 0:
            await asyncio.sleep(wait)

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload = {"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    self._last_send_time = time.time()
                    if resp.status == 200:
                        return True
                    else:
                        body = await resp.text()
                        self.logger.error(f"Telegram发送失败: {resp.status} {body[:100]}")
                        return False
        except Exception as e:
            self.logger.error(f"Telegram发送异常: {e}")
            return False
