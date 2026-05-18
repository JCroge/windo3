"""Telegram告警Agent - 实时推送交易通知、风控告警、每日摘要 + 远程命令控制"""

import asyncio
import json
import os
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
        self._update_offset = 0
        self._last_poll_time = 0
        self._poll_interval = 5
        self._start_time = time.time()
        self._last_balance = 0.0
        self._active_symbols = []

    async def setup(self):
        if not self._enabled:
            self.logger.warning("Telegram通知未启用（缺少BOT_TOKEN或CHAT_ID）")
            return
        self._reset_daily_summary()
        await self._flush_old_updates()
        ok = await self._send_message("🟢 交易系统启动")
        if ok:
            self.logger.info("Telegram通知Agent就绪（含远程命令）")
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
        if not self._enabled:
            await asyncio.sleep(30)
            return

        await self._poll_commands()
        self._check_daily_reset()
        await asyncio.sleep(self._poll_interval)

    async def _handle_execution(self, msg: dict):
        payload = msg['payload']
        status = payload.get('status')
        symbol = msg.get('symbol') or payload.get('symbol', '?')
        action = payload.get('action', '')
        result = payload.get('result', {})

        if status == 'executed' and action in ('open_long', 'open_short'):
            # sync发现的持仓不推送（不是交易决策，避免刷屏）
            if payload.get('source') == 'sync':
                return
            side = '🟢 做多' if action == 'open_long' else '🔴 做空'
            leverage = result.get('leverage', '?')
            amount = result.get('amount_usdt', '?')
            if payload.get('is_add'):
                text = (
                    f"➕ 加仓 {symbol}\n"
                    f"方向: {side} | 加仓: {result.get('add_amount_usdt', amount)} USDT\n"
                    f"新均价: {result.get('new_entry_price', '?')}"
                )
            else:
                text = (
                    f"{side} {symbol}\n"
                    f"杠杆: {leverage}x | 仓位: {amount} USDT\n"
                    f"置信度: {payload.get('confidence', '?')}%"
                )
            await self._send_message(text)

        elif status == 'risk_reduced':
            reduce_pct = payload.get('reduce_pct', 0.5)
            text = f"✂️ 减仓 {symbol} {int(reduce_pct*100)}%"
            await self._send_message(text)

        elif status in ('executed', 'force_closed', 'closed_externally') and (action == 'close' or status in ('force_closed', 'closed_externally')):
            # 去重：同一symbol 60s内不重复推送平仓
            if not hasattr(self, '_close_notify_cache'):
                self._close_notify_cache = {}
            now = time.time()
            if symbol in self._close_notify_cache and now - self._close_notify_cache[symbol] < 60:
                return
            self._close_notify_cache[symbol] = now

            pnl = result.get('pnl', 0)
            emoji = '💰' if pnl > 0 else '💸'
            reason = payload.get('reason', '交易所SL/TP触发' if status == 'closed_externally' else '主动平仓')
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

    # ==================== 远程命令系统 ====================

    async def _flush_old_updates(self):
        """启动时跳过所有旧消息，防止重新处理历史命令（如旧的/stop /restart）"""
        try:
            url = f"https://api.telegram.org/bot{self._bot_token}/getUpdates"
            params = {"offset": self._update_offset, "timeout": 0, "limit": 100}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        updates = data.get('result', [])
                        if updates:
                            self._update_offset = updates[-1]['update_id'] + 1
                            self.logger.info(f"[Telegram] 启动时跳过{len(updates)}条旧消息")
        except Exception:
            pass

    async def _poll_commands(self):
        url = f"https://api.telegram.org/bot{self._bot_token}/getUpdates"
        params = {"offset": self._update_offset, "timeout": 0, "limit": 10}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for update in data.get('result', []):
                            self._update_offset = update['update_id'] + 1
                            await self._handle_command(update)
        except Exception:
            pass

    async def _handle_command(self, update: dict):
        msg = update.get('message', {})
        chat_id = msg.get('chat', {}).get('id')
        text = (msg.get('text') or '').strip()

        if str(chat_id) != str(self._chat_id):
            return

        cmd = text.split()[0] if text else ''
        handlers = {
            '/status': self._cmd_status,
            '/positions': self._cmd_positions,
            '/stop': self._cmd_stop,
            '/restart': self._cmd_restart,
            '/halt': self._cmd_halt,
            '/resume': self._cmd_resume,
            '/log': self._cmd_log,
        }

        handler = handlers.get(cmd)
        if handler:
            self.logger.info(f"[Telegram] 收到命令: {cmd}")
            await handler()
        elif text.startswith('/'):
            self.logger.info(f"[Telegram] 未知命令: {text}")

    async def _cmd_status(self):
        uptime = time.time() - self._start_time
        hours = uptime / 3600

        positions = {}
        try:
            with open('data/positions.json', 'r') as f:
                positions = json.load(f)
        except Exception:
            pass

        halted = False
        try:
            with open('data/trade_history.json', 'r') as f:
                history = json.load(f)
                halted = history.get('trading_halted', False)
        except Exception:
            pass

        text = f"📊 系统状态\n"
        text += f"运行: {hours:.1f}h\n"
        text += f"持仓: {len(positions)}个\n"
        text += f"熔断: {'是' if halted else '否'}\n"
        text += f"今日交易: {self._daily_summary['trades']}笔\n"
        text += f"今日PnL: {self._daily_summary['pnl']:+.2f} USDT"
        await self._send_message(text)

    async def _cmd_positions(self):
        positions = {}
        try:
            with open('data/positions.json', 'r') as f:
                positions = json.load(f)
        except Exception:
            pass

        if not positions:
            await self._send_message("📭 当前无持仓")
            return

        text = "📈 当前持仓:\n"
        for sym, pos in positions.items():
            side = pos.get('side', '?')
            lev = pos.get('leverage', '?')
            entry = pos.get('entry_price', 0)
            text += f"\n<b>{sym}</b>\n"
            text += f"  {side} {lev}x @ {entry}\n"
            if pos.get('stop_loss'):
                text += f"  SL: {pos['stop_loss']}"
            if pos.get('take_profit'):
                text += f" | TP: {pos['take_profit']}"
            text += "\n"
        await self._send_message(text)

    async def _cmd_stop(self):
        await self._send_message("⏹ 正在优雅退出...")
        self.logger.info("[Telegram] /stop命令执行")
        await asyncio.sleep(1)
        await self.publish("system_command", {"command": "shutdown"})

    async def _cmd_restart(self):
        await self._send_message("🔄 正在重启...")
        os.makedirs('data', exist_ok=True)
        with open('data/.restart_flag', 'w') as f:
            f.write(str(time.time()))
        self.logger.info("[Telegram] /restart命令执行，已写入restart_flag")
        await asyncio.sleep(1)
        await self.publish("system_command", {"command": "shutdown"})

    async def _cmd_halt(self):
        await self.publish("system_command", {"command": "halt"})
        await self._send_message("🛑 已手动熔断，停止新交易")

    async def _cmd_resume(self):
        await self.publish("system_command", {"command": "resume"})
        await self._send_message("✅ 已解除熔断，恢复交易")

    async def _cmd_log(self):
        import subprocess
        try:
            result = subprocess.run(
                ['grep', '-E', '决策|执行|平仓|熔断|硬性规则|持仓分析|开仓',
                 'logs/system.log'],
                capture_output=True, text=True, timeout=5
            )
            lines = result.stdout.strip().split('\n')[-10:]
            if lines and lines[0]:
                text = "📋 最近日志:\n\n"
                for line in lines:
                    text += line[-80:] + "\n"
            else:
                text = "📋 暂无关键日志"
        except Exception:
            text = "📋 日志读取失败"
        await self._send_message(text)

    # ==================== 消息发送 ====================

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
