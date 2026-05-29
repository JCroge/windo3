"""Telegram告警Agent - 实时推送交易通知、风控告警、每日摘要 + 远程命令控制"""

import asyncio
import json
import os
import time
import datetime
import aiohttp
from agents.base import BaseAgent
from utils.halt_state import get_halt_state
from utils.state_paths import get_state_paths


def _positions_path() -> str:
    return get_state_paths().positions


def _riskguard_path() -> str:
    return get_state_paths().riskguard_state


class TelegramNotifier(BaseAgent):
    name = "telegram_notifier"
    subscriptions = [
        "execution_result",
        "daily_hard_stop_triggered",
        "risk_alert",
        "strategy_review",
        "telegram_alert",
        "data_alert",
        "pnl_resolved",
        "pnl_mismatch",
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
        self._halt_state = get_halt_state()
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
        elif msg['type'] == 'telegram_alert':
            level = msg['payload'].get('level', 'info')
            text = msg['payload'].get('message', '')
            prefix = '⚠️' if level == 'warning' else 'ℹ️'
            await self._send_message(f"{prefix} {text}")
        elif msg['type'] == 'data_alert':
            await self._handle_data_alert(msg)
        elif msg['type'] == 'pnl_resolved':
            await self._handle_pnl_resolved(msg)
        elif msg['type'] == 'pnl_mismatch':
            await self._handle_pnl_mismatch(msg)

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
            if payload.get('protection_failed'):
                pus = payload.get('protective_update_state', 'unknown')
                text = (
                    f"⚠️ 减仓 {symbol} {int(reduce_pct*100)}% 已成交\n"
                    f"但保护单异常: {pus}\n"
                    f"protection_state=unknown,需人工核查"
                )
            else:
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

            pnl_is_final = bool(
                result.get('pnl_is_final',
                           payload.get('pnl_is_final', True))
            )
            pnl_raw = result.get('realized_pnl_net_usdt')
            if pnl_raw is None:
                pnl_raw = result.get('pnl')
            reason = payload.get('reason', '交易所SL/TP触发' if status == 'closed_externally' else '主动平仓')

            if pnl_is_final and pnl_raw is not None:
                pnl = float(pnl_raw)
                emoji = '💰' if pnl > 0 else '💸'
                text = f"{emoji} 平仓 {symbol}\nPnL: {pnl:+.2f} USDT | 原因: {reason}"
                await self._send_message(text)
                self._update_daily_summary(pnl)
            else:
                # pending: 走 estimated_pnl,等 pnl_resolved 升级后再计 daily summary
                est = result.get('estimated_pnl')
                est_str = f"{float(est):+.2f}" if est is not None else "N/A"
                text = (
                    f"⏳ 平仓 {symbol}\n"
                    f"PnL 待交易所账单确认 | 原因: {reason}\n"
                    f"估算: {est_str} USDT"
                )
                await self._send_message(text)

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

        critical_types = ('flash_move', 'max_drawdown', 'emergency_close', 'llm_degraded',
                          'protection_failed')
        if alert_type not in critical_types:
            return

        type_names = {
            'flash_move': '⚡ 闪崩',
            'max_drawdown': '📉 最大回撤',
            'emergency_close': '🆘 紧急平仓',
            'llm_degraded': '🤖 LLM降级',
            'protection_failed': '⚠️ 保护单异常',
        }
        name = type_names.get(alert_type, alert_type)
        text = f"{name} {symbol}"

        if alert_type == 'flash_move':
            text += f"\n变动: {payload.get('magnitude_pct', 0):.1f}%"
        elif alert_type == 'max_drawdown':
            text += f"\n回撤: {payload.get('drawdown_pct', 0):.1f}%"
        elif alert_type == 'llm_degraded':
            text += f"\n{payload.get('message', '')}"
        elif alert_type == 'protection_failed':
            pus = payload.get('protective_update_state', 'unknown')
            request_id = payload.get('request_id', '')
            text += f"\nprotective_update_state: {pus}"
            if request_id:
                text += f"\nrequest_id: {request_id}"

        await self._send_message(text)

    async def _handle_data_alert(self, msg: dict):
        payload = msg['payload']
        symbol = payload.get('symbol', '?')
        consecutive_failures = payload.get('consecutive_failures', 0)
        error = payload.get('error', '')
        if consecutive_failures >= 3:
            await self._send_message(
                f"⚠️ 数据采集告警: {symbol} 连续{consecutive_failures}次失败\n{str(error)[:100]}"
            )

    async def _handle_pnl_resolved(self, msg: dict):
        """PRD §6.2 dual-payload: pending → final 升级,补 daily summary"""
        payload = msg['payload']
        symbol = payload.get('symbol', '?')
        final_pnl = payload.get('realized_pnl_net_usdt')
        if final_pnl is None:
            return
        try:
            final_pnl = float(final_pnl)
        except (TypeError, ValueError):
            return
        emoji = '💰' if final_pnl > 0 else '💸'
        est = payload.get('estimated_pnl')
        if est is not None:
            try:
                est_str = f"{float(est):+.2f}"
            except (TypeError, ValueError):
                est_str = "N/A"
        else:
            est_str = "N/A"
        confidence = payload.get('match_confidence')
        conf_str = f" | 置信:{float(confidence):.2f}" if confidence is not None else ""
        text = (
            f"{emoji} PnL 校正 {symbol}\n"
            f"估算 {est_str} → 终值 {final_pnl:+.2f} USDT{conf_str}"
        )
        await self._send_message(text)
        self._update_daily_summary(final_pnl)

    async def _handle_pnl_mismatch(self, msg: dict):
        """PRD §6.2 dual-payload: fills/bills 偏差 → 人工复核告警,不进 daily"""
        payload = msg['payload']
        symbol = payload.get('symbol', '?')
        local = payload.get('estimated_pnl')
        exch = payload.get('exchange_pnl_usdt')
        warnings = payload.get('warnings', []) or []
        local_str = f"{float(local):+.2f}" if local is not None else "N/A"
        exch_str = f"{float(exch):+.2f}" if exch is not None else "N/A"
        text = (
            f"⚠️ PnL 对账偏差 {symbol}\n"
            f"本地估算: {local_str} | 交易所: {exch_str}\n"
            f"需人工复核"
        )
        if warnings:
            text += f"\n标记: {','.join(warnings[:3])}"
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
            '/force_resume': self._cmd_force_resume,
            '/reconcile': self._cmd_reconcile,
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
            with open(_positions_path(), 'r') as f:
                positions = json.load(f)
        except Exception:
            pass

        halted = False
        halt_reason = ""
        reconciliation = ""
        try:
            from utils.halt_state import get_halt_state
            hs = get_halt_state()
            halted = hs.halted
            halt_reason = hs.reason or ""
            if hs.reconciliation_pending:
                reconciliation = "对账中..."
            elif hs.reconciliation_result:
                reconciliation = f"对账: {hs.reconciliation_result}"
        except Exception:
            try:
                with open(_riskguard_path(), 'r') as f:
                    halted = json.load(f).get('trading_halted', False)
            except Exception:
                pass

        text = f"📊 系统状态\n"
        text += f"运行: {hours:.1f}h\n"
        text += f"持仓: {len(positions)}个\n"
        if halted:
            text += f"熔断: 是 ({halt_reason})\n"
        else:
            text += f"熔断: 否\n"
        if reconciliation:
            text += f"{reconciliation}\n"
        text += f"今日交易: {self._daily_summary['trades']}笔\n"
        text += f"今日PnL: {self._daily_summary['pnl']:+.2f} USDT"
        await self._send_message(text)

    async def _cmd_positions(self):
        positions = {}
        try:
            with open(_positions_path(), 'r') as f:
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
        self._halt_state.halt(reason="manual_telegram", triggered_by="telegram")
        await self.publish("system_command", {"command": "halt"})
        await self._send_message("🛑 已手动熔断，停止新交易")

    async def _cmd_resume(self):
        if not self._halt_state.halted:
            await self._send_message("ℹ️ 当前未处于熔断状态")
            return

        await self._send_message("🔄 正在执行对账...")
        self._halt_state.request_resume(resume_by="telegram")

        reconcile_ok = await self._run_reconciliation()

        if reconcile_ok:
            await self.publish("system_command", {
                "command": "resume", "source": "telegram",
                "reconciliation_result": {"status": "matched"},
            })
            await self._send_message("✅ 对账通过，已发送恢复请求")
        else:
            await self._send_message(
                "❌ 对账不通过，维持熔断\n"
                "使用 /force_resume 强制解除（跳过对账）"
            )

    async def _cmd_force_resume(self):
        if not self._halt_state.halted:
            await self._send_message("ℹ️ 当前未处于熔断状态")
            return
        await self.publish("system_command", {"command": "force_resume", "source": "telegram"})
        await self._send_message("⚠️ 已发送强制解除请求（跳过对账）")

    async def _cmd_reconcile(self):
        await self._send_message("🔍 正在执行四方对账...")
        reconcile_ok = await self._run_reconciliation()
        if reconcile_ok:
            await self._send_message("✅ 四方持仓一致")
        else:
            pass  # _run_reconciliation already sends detail message

    async def _run_reconciliation(self) -> bool:
        try:
            from utils.position_reconciler import PositionReconciler
            from utils.exchange_factory import create_exchange

            exchange = None
            try:
                exchange = create_exchange(self.config, require_private=True, purpose="telegram_reconcile")
            except Exception:
                pass

            executor_positions = {}
            try:
                with open(_positions_path(), 'r') as f:
                    executor_positions = json.load(f)
            except Exception:
                pass

            riskguard_positions = {}
            try:
                with open(_riskguard_path(), 'r') as f:
                    rg_state = json.load(f)
                    riskguard_positions = rg_state.get('positions', {})
            except Exception:
                pass

            paper_positions = {}
            try:
                with open('data/paper_positions.json', 'r') as f:
                    paper_positions = json.load(f)
            except Exception:
                pass

            class _FakeExecutor:
                def __init__(self, pos):
                    self._pos = pos
                def get_all_positions(self):
                    return self._pos

            reconciler = PositionReconciler(
                executor=_FakeExecutor(executor_positions),
                exchange=exchange,
                logger=self.logger,
            )
            result = reconciler.reconcile(
                riskguard_positions=riskguard_positions,
                paper_positions=paper_positions,
            )

            if result['status'] == 'matched' and result.get('exchange_query_ok', False):
                return True
            else:
                issues = result.get('issues', [])
                if not result.get('exchange_query_ok', True):
                    text = "❌ 对账失败：交易所持仓查询不可用，无法确认安全恢复\n"
                else:
                    text = f"⚠️ 对账发现 {len(issues)} 个问题:\n"
                for issue in issues[:5]:
                    text += f"• {issue['symbol']}: {issue['detail']}\n"
                if len(issues) > 5:
                    text += f"...还有 {len(issues)-5} 个"
                await self._send_message(text)
                return False
        except Exception as e:
            self.logger.error(f"[Telegram] 对账执行失败: {e}")
            await self._send_message(f"⚠️ 对账执行失败: {e}")
            return False

    async def _cmd_log(self):
        import subprocess
        import glob
        from datetime import datetime
        try:
            # logger 写 logs/{name}_YYYYMMDD.log，扫今日所有 agent 日志
            today = datetime.now().strftime("%Y%m%d")
            log_files = glob.glob(f'logs/*_{today}.log')
            if not log_files:
                # 兜底：取最近修改的日志文件
                all_logs = sorted(glob.glob('logs/*.log'), key=lambda p: os.path.getmtime(p), reverse=True)
                log_files = all_logs[:5]
            if not log_files:
                await self._send_message("📋 暂无日志文件")
                return
            result = subprocess.run(
                ['grep', '-h', '-E', '决策|执行|平仓|熔断|硬性规则|持仓分析|开仓', *log_files],
                capture_output=True, text=True, timeout=5
            )
            lines = result.stdout.strip().split('\n')[-10:]
            if lines and lines[0]:
                text = "📋 最近日志:\n\n"
                for line in lines:
                    text += line[-80:] + "\n"
            else:
                text = "📋 暂无关键日志"
        except Exception as e:
            text = f"📋 日志读取失败: {e}"
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
