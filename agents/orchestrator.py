"""编排器 - 两层架构生命周期管理

Tier 1 (研判层): 每12小时运行一次，选出最有价值的2-3个标的
Tier 2 (交易层): 持续运行，对活跃标的并行分析+交易
"""

import asyncio
import signal
import os
import time
import uuid
from utils.logger import setup_logger
from utils.config_loader import load_config, format_banner, ConfigError
from agents.message_bus import MessageBus


class Orchestrator:
    """两层Agent编排器"""

    def __init__(self, config: dict = None):
        self.logger = setup_logger('orchestrator')
        self.config = config or self._default_config()
        self._research_agents = []
        self._trading_agents = []
        self._tasks = []
        self._research_interval = self.config.get('research_interval', 12 * 3600)
        self._idle_trigger_seconds = self.config.get('idle_trigger_seconds', 2 * 3600)  # 连续2h全hold→提前触发
        self._idle_cooldown_seconds = self.config.get('idle_cooldown_seconds', 30 * 60)  # 提前触发后30min冷却
        self._last_active_time = time.time()  # 最近一次有 action != 'hold' 的时间
        self._last_research_time = 0.0
        self._shutdown_event = None
        self.bus = None
        self._research_watchdog_timeout = self.config.get('research_watchdog_timeout', 90)
        self._research_watchdog_max_retries = self.config.get('research_watchdog_max_retries', 1)
        self._research_watchdogs = {}
        self._research_cycle_completed = set()
        self._research_cycle_retries = {}
        self._latest_halts_snapshot: dict = {}    # F-TG-004
        self._health_write_interval: float = 30.0  # F-TG-004 秒
        self._prev_dlq_size: int = 0  # P2-16: DLQ 增长告警基准

    def _default_config(self) -> dict:
        try:
            return load_config()
        except ConfigError as e:
            self.logger.critical(f"[配置错误] {e}")
            raise

    def _register_agents(self):
        from agents.research.market_scanner import MarketScanner
        from agents.research.sentiment_researcher import SentimentResearcher
        from agents.research.news_researcher import NewsResearcher
        from agents.research.synthesizer import ResearchSynthesizer
        from agents.research.censor import Censor
        from agents.research.symbol_router import SymbolRouter
        from agents.trading.multi_data_collector import MultiDataCollector
        from agents.trading.tech_analyst import MultiTechAnalyst
        from agents.trading.judge import MultiJudge
        from agents.trading.executor import MultiExecutor
        from agents.trading.reviewer import ReviewerAgent
        from agents.trading.portfolio_risk_guard import PortfolioRiskGuard
        from agents.trading.telegram_notifier import TelegramNotifier
        from agents.trading.position_analyst import PositionAnalyst
        from agents.trading.behavioral_critic import BehavioralCritic
        from agents.trading.paper_executor import PaperExecutor

        MessageBus.reset()

        self._research_agents = [
            MarketScanner(self.config),
            SentimentResearcher(self.config),
            NewsResearcher(self.config),
            ResearchSynthesizer(self.config),
            Censor(self.config),
            SymbolRouter(self.config),
        ]

        self._trading_agents = [
            MultiDataCollector(self.config),
            MultiTechAnalyst(self.config),
            MultiJudge(self.config),
            MultiExecutor(self.config),
            PaperExecutor(self.config),
            ReviewerAgent(self.config),
            PortfolioRiskGuard(self.config),
            PositionAnalyst(self.config),
            BehavioralCritic(self.config),
            TelegramNotifier(self.config),
        ]

    def start(self):
        self._register_agents()
        all_agents = self._research_agents + self._trading_agents

        # 打印硬限制 banner（让用户在启动时一眼看到风险参数）
        for line in format_banner(self.config).split("\n"):
            self.logger.info(line)
        self.logger.info(f"研判层: {len(self._research_agents)} agents | "
                        f"交易层: {len(self._trading_agents)} agents")
        self.logger.info("=" * 60)

        try:
            asyncio.run(self._run(all_agents))
        except KeyboardInterrupt:
            self.logger.info("用户中断，退出...")

    async def _run(self, all_agents):
        self._shutdown_event = asyncio.Event()
        self.bus = MessageBus.get_instance()

        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGTERM, lambda: self._shutdown_event.set())
        loop.add_signal_handler(signal.SIGINT, lambda: self._shutdown_event.set())

        self.bus.register(
            "orchestrator",
            ["system_command", "trade_decision:*", "research_preliminary",
             "research_result", "halts_snapshot"]  # F-TG-004
        )

        self._tasks = [asyncio.create_task(agent.run()) for agent in all_agents]
        research_task = asyncio.create_task(self._research_loop())
        cmd_task = asyncio.create_task(self._command_listener())
        health_task = asyncio.create_task(self._health_loop())  # F-TG-004
        self._tasks.append(research_task)
        self._tasks.append(cmd_task)
        self._tasks.append(health_task)

        self.logger.info("所有Agent已启动，进入运行状态...")

        try:
            await self._shutdown_event.wait()
        except KeyboardInterrupt:
            self.logger.info("收到KeyboardInterrupt...")
        finally:
            await self._graceful_shutdown(all_agents)

    async def _publish_research_trigger(self, bus=None, reason: str = "scheduled", retry_of: str = None):
        """发布研判触发并登记 watchdog，防止上游无声失败让 cycle 卡死。"""
        bus = bus or self.bus or MessageBus.get_instance()
        cycle_id = str(uuid.uuid4())[:8]
        retry_count = self._research_cycle_retries.get(retry_of, 0) + 1 if retry_of else 0
        payload = {"cycle_id": cycle_id}
        if retry_of:
            payload["retry_of"] = retry_of
            payload["retry_count"] = retry_count

        await bus.publish("orchestrator", "research_trigger", payload, "broadcast")
        self._last_research_time = time.time()
        self._research_cycle_retries[cycle_id] = retry_count
        self._start_research_watchdog(cycle_id, retry_count, reason)
        return cycle_id

    def _start_research_watchdog(self, cycle_id: str, retry_count: int, reason: str):
        timeout = float(self._research_watchdog_timeout)
        if timeout <= 0:
            return

        existing = self._research_watchdogs.pop(cycle_id, None)
        if existing and not existing.done():
            existing.cancel()

        task = asyncio.create_task(self._watch_research_cycle(cycle_id, retry_count))
        self._research_watchdogs[cycle_id] = task
        self._tasks.append(task)
        task.add_done_callback(lambda t, cid=cycle_id: self._on_research_watchdog_done(cid, t))
        self.logger.debug(
            f"[编排] 研判watchdog已启动: cycle={cycle_id} timeout={timeout:g}s "
            f"retry={retry_count} reason={reason}"
        )

    def _on_research_watchdog_done(self, cycle_id: str, task: asyncio.Task):
        if self._research_watchdogs.get(cycle_id) is task:
            self._research_watchdogs.pop(cycle_id, None)
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc:
            self.logger.error(f"[编排] 研判watchdog异常 cycle={cycle_id}: {exc}")

    async def _watch_research_cycle(self, cycle_id: str, retry_count: int):
        timeout = float(self._research_watchdog_timeout)
        await asyncio.sleep(timeout)
        if cycle_id in self._research_cycle_completed:
            return
        if self._shutdown_event and self._shutdown_event.is_set():
            return

        self.logger.error(
            f"[编排] 研判cycle={cycle_id} {timeout:g}s内未产出"
            f"research_preliminary/research_result"
        )

        if retry_count < int(self._research_watchdog_max_retries):
            self.logger.warning(
                f"[编排] 强制重发研判: retry={retry_count + 1}/"
                f"{self._research_watchdog_max_retries} old_cycle={cycle_id}"
            )
            await self._publish_research_trigger(reason="watchdog_retry", retry_of=cycle_id)
            return

        bus = self.bus or MessageBus.get_instance()
        await bus.publish("orchestrator", "telegram_alert", {
            "level": "error",
            "message": f"研判cycle={cycle_id} 超时且重试耗尽，请检查 market_scanner/synthesizer",
            "cycle_id": cycle_id,
        }, "broadcast")

    def _mark_research_cycle_completed(self, cycle_id: str, msg_type: str):
        if not cycle_id:
            return
        if cycle_id not in self._research_cycle_completed:
            self.logger.info(f"[编排] 研判cycle={cycle_id} 已产出 {msg_type}")
        self._research_cycle_completed.add(cycle_id)
        task = self._research_watchdogs.pop(cycle_id, None)
        if task and not task.done():
            task.cancel()

    async def _research_loop(self):
        """定期触发研判层运行（含空闲提前触发：连续 idle_trigger_seconds 全 hold → 提前换标的）"""
        bus = MessageBus.get_instance()

        await asyncio.sleep(5)
        self.logger.info("[编排] 首次研判触发...")
        await self._publish_research_trigger(bus, reason="startup")

        check_interval = 300  # 每 5 min 检查一次状态
        while True:
            await asyncio.sleep(check_interval)
            now = time.time()
            elapsed_since_research = now - self._last_research_time
            elapsed_since_active = now - self._last_active_time

            # 触发条件1：定时周期到了
            if elapsed_since_research >= self._research_interval:
                self.logger.info(f"[编排] 定时研判触发（每{self._research_interval//3600}h）")
                await self._publish_research_trigger(bus, reason="scheduled")
                continue

            # 触发条件2：连续 idle 太久 + 距上次研判已超冷却（防止刚换完又被空闲触发）
            if (elapsed_since_active >= self._idle_trigger_seconds
                    and elapsed_since_research >= self._idle_cooldown_seconds):
                self.logger.info(
                    f"[编排] 空闲提前触发研判（连续{elapsed_since_active//60:.0f}min无开/平仓决策）"
                )
                await self._publish_research_trigger(bus, reason="idle")
                # 重置 idle 计时，避免立即重复触发
                self._last_active_time = now

    async def _command_listener(self):
        """监听system_command + 跟踪交易活跃度（trade_decision != hold）"""
        while not self._shutdown_event.is_set():
            msg = await self.bus.receive("orchestrator", timeout=2.0)
            if not msg:
                continue
            mtype = msg.get('type')
            # F-TG-004: halts_snapshot 缓存
            if mtype == 'halts_snapshot':
                self._on_halts_snapshot(msg)
                continue
            if mtype == 'system_command':
                cmd = msg.get('payload', {}).get('command', '')
                if cmd == 'shutdown':
                    self.logger.info("[编排] 收到远程shutdown命令")
                    self._shutdown_event.set()
            elif mtype == 'trade_decision':
                # 任何非 hold 决策（含 deferred entry/追价/正常入场）都视为活跃
                action = msg.get('payload', {}).get('action', 'hold')
                if action and action != 'hold':
                    self._last_active_time = time.time()
            elif mtype in ('research_preliminary', 'research_result'):
                cycle_id = msg.get('payload', {}).get('cycle_id')
                self._mark_research_cycle_completed(cycle_id, mtype)

    def _on_halts_snapshot(self, msg: dict):
        """F-TG-004: 缓存 halts_snapshot 事件,供 _write_agent_health 用。"""
        try:
            payload = msg.get('payload', {}) or {}
            halts = payload.get('halted_symbols', {})
            self._latest_halts_snapshot = dict(halts) if halts else {}
        except Exception as e:
            self.logger.warning(f"[Orchestrator] _on_halts_snapshot 异常: {e}")

    def _write_agent_health(self):
        """F-TG-004: 写 data/<ns_>agent_health.json。失败 logger.warning 不抛。"""
        try:
            from utils.state_paths import get_state_paths
            from utils.atomic_io import atomic_write_json
            from agents.message_bus import MessageBus

            tasks_alive = 0
            tasks_failed = 0
            for t in self._tasks:
                try:
                    if t.done():
                        cancelled = getattr(t, "cancelled", None)
                        if callable(cancelled) and cancelled() is True:
                            continue
                        try:
                            task_exc = t.exception()
                        except asyncio.CancelledError:
                            continue
                        if task_exc is not None:
                            tasks_failed += 1
                    else:
                        tasks_alive += 1
                except Exception:
                    pass  # 单个 task 异常不影响整体计数

            agents_registered = len(self._research_agents) + len(self._trading_agents)

            try:
                bus = MessageBus.get_instance()
                dlq_size = len(getattr(bus, '_dead_letter', []))
            except Exception:
                dlq_size = 0

            health = {
                'ts': time.time(),
                'agents_registered': agents_registered,
                'tasks_alive': tasks_alive,
                'tasks_failed': tasks_failed,
                'halted_symbols': dict(self._latest_halts_snapshot),
                'bus_dlq_size': dlq_size,
            }
            path = get_state_paths().agent_health
            atomic_write_json(path, health)
            return dlq_size
        except Exception as e:
            self.logger.warning(f"[AgentHealth] 写入失败: {e}")
            return None

    async def _maybe_alert_dlq_growth(self, dlq_size):
        """P2-16: DLQ 较上次增长时主动 telegram_alert（30s cadence 天然限流）。

        DLQ 增长意味着 enqueue 失败或重要 topic 无订阅者——关键 wiring 断裂，
        不得仅静默写入 agent_health.json。
        """
        if dlq_size is None:
            return
        if dlq_size > self._prev_dlq_size:
            delta = dlq_size - self._prev_dlq_size
            try:
                from agents.message_bus import MessageBus
                bus = self.bus or MessageBus.get_instance()
                await bus.publish("orchestrator", "telegram_alert", {
                    "level": "warning",
                    "type": "bus_dlq_growth",
                    "message": (
                        f"消息总线 DLQ 增长 +{delta}（当前 {dlq_size}）；"
                        f"可能有 enqueue 失败或重要 topic 无订阅者"
                    ),
                    "dlq_size": dlq_size,
                    "delta": delta,
                }, "broadcast")
            except Exception as e:
                self.logger.warning(f"[DLQ Alert] 发布失败: {e}")
        self._prev_dlq_size = dlq_size

    async def _health_loop(self):
        """F-TG-004: 每 N 秒写一次 agent_health.json；P2-16: 顺带 DLQ 增长告警。"""
        while not self._shutdown_event.is_set():
            dlq_size = self._write_agent_health()
            await self._maybe_alert_dlq_growth(dlq_size)
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self._health_write_interval,
                )
            except asyncio.TimeoutError:
                continue

    async def _graceful_shutdown(self, all_agents):
        """优雅停机流程"""
        self.logger.info("=" * 60)
        self.logger.info("开始优雅停机...")

        # 1. 通知所有Agent停止接收新消息
        self.logger.info("[1/4] 通知所有Agent停止...")
        for agent in all_agents:
            agent._should_stop = True

        # 2. 取消所有任务
        self.logger.info("[2/4] 取消所有任务...")
        for task in self._tasks:
            task.cancel()

        # 等待任务完成取消
        await asyncio.gather(*self._tasks, return_exceptions=True)

        # 3. 保存所有状态
        self.logger.info("[3/4] 保存状态...")
        await self._save_all_states()

        # 4. 关闭消息总线
        self.logger.info("[4/4] 关闭消息总线...")
        if self.bus:
            self.bus.close()

        self.logger.info("优雅停机完成")
        self.logger.info("=" * 60)

    async def _save_all_states(self):
        """保存所有Agent状态"""
        # RiskGuard状态
        for agent in self._trading_agents:
            if hasattr(agent, '_save_state'):
                try:
                    agent._save_state()
                    self.logger.info(f"  ✓ {agent.name} 状态已保存")
                except Exception as e:
                    self.logger.error(f"  ✗ {agent.name} 状态保存失败: {e}")

        # Reviewer交易历史
        reviewer = next((a for a in self._trading_agents if a.name == 'reviewer'), None)
        if reviewer and hasattr(reviewer, '_save_trade_history'):
            try:
                reviewer._save_trade_history()
                self.logger.info(f"  ✓ 交易历史已保存")
            except Exception as e:
                self.logger.error(f"  ✗ 交易历史保存失败: {e}")


def main():
    orchestrator = Orchestrator()
    orchestrator.start()


if __name__ == '__main__':
    main()
