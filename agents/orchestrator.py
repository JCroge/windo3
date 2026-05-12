"""编排器 - 两层架构生命周期管理

Tier 1 (研判层): 每12小时运行一次，选出最有价值的2-3个标的
Tier 2 (交易层): 持续运行，对活跃标的并行分析+交易
"""

import asyncio
import signal
import os
from dotenv import load_dotenv
from utils.logger import setup_logger
from agents.message_bus import MessageBus

load_dotenv()


class Orchestrator:
    """两层Agent编排器"""

    def __init__(self, config: dict = None):
        self.logger = setup_logger('orchestrator')
        self.config = config or self._default_config()
        self._research_agents = []
        self._trading_agents = []
        self._tasks = []
        self._research_interval = self.config.get('research_interval', 12 * 3600)
        self._shutdown_event = None
        self.bus = None

    def _default_config(self) -> dict:
        return {
            "exchange": os.getenv("EXCHANGE", "okx"),
            "interval": "1h",
            "leverage": int(os.getenv("LEVERAGE", "3")),
            "max_trade_amount": float(os.getenv("MAX_TRADE_AMOUNT", "10")),
            "use_testnet": os.getenv("USE_TESTNET", "false").lower() == "true",
            "research_interval": int(os.getenv("RESEARCH_INTERVAL", str(4 * 3600))),
            "max_active_symbols": int(os.getenv("MAX_ACTIVE_SYMBOLS", "5")),
            "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
            "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        }

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
            ReviewerAgent(self.config),
            PortfolioRiskGuard(self.config),
            PositionAnalyst(self.config),
            BehavioralCritic(self.config),
            TelegramNotifier(self.config),
        ]

    def start(self):
        self._register_agents()
        all_agents = self._research_agents + self._trading_agents

        self.logger.info("=" * 60)
        self.logger.info("多Agent交易系统启动（两层架构）")
        self.logger.info(f"交易所: {self.config['exchange']} | "
                        f"杠杆: {self.config['leverage']}x | "
                        f"研判周期: {self._research_interval//3600}h")
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

        self._tasks = [asyncio.create_task(agent.run()) for agent in all_agents]
        research_task = asyncio.create_task(self._research_loop())
        self._tasks.append(research_task)

        self.logger.info("所有Agent已启动，进入运行状态...")

        try:
            await self._shutdown_event.wait()
        except KeyboardInterrupt:
            self.logger.info("收到KeyboardInterrupt...")
        finally:
            await self._graceful_shutdown(all_agents)

    async def _research_loop(self):
        """定期触发研判层运行"""
        bus = MessageBus.get_instance()

        await asyncio.sleep(5)
        self.logger.info("[编排] 首次研判触发...")
        await bus.publish("orchestrator", "research_trigger", {}, "broadcast")

        while True:
            await asyncio.sleep(self._research_interval)
            self.logger.info(f"[编排] 定时研判触发（每{self._research_interval//3600}h）")
            await bus.publish("orchestrator", "research_trigger", {}, "broadcast")

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
