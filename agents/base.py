"""Agent 基类 - 所有 Agent 的抽象基础"""

import asyncio
import time
from abc import ABC, abstractmethod
from agents.message_bus import MessageBus
from agents.llm_client import LLMClient
from utils.logger import setup_logger


class BaseAgent(ABC):
    """所有 Agent 的基类"""

    name: str = "unnamed"
    subscriptions: list = []

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.logger = setup_logger(f'agent_{self.name}')
        self.bus = MessageBus.get_instance()
        self.bus.register(self.name, self.subscriptions)
        self.llm = None
        self._running = False
        self._should_stop = False
        self._start_time = 0

    def init_llm(self):
        if self.llm is None:
            self.llm = LLMClient()

    @abstractmethod
    async def on_message(self, msg: dict):
        pass

    @abstractmethod
    async def setup(self):
        pass

    async def run(self):
        self._running = True
        self._start_time = time.time()
        self.logger.info(f"Agent [{self.name}] 启动")

        await self.setup()

        while self._running and not self._should_stop:
            try:
                msg = await self.bus.receive(self.name, timeout=0.5)
                if msg:
                    await self.on_message(msg)
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                import traceback
                self.logger.error(f"运行错误: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(1)

        self.logger.info(f"Agent [{self.name}] 停止")

    async def tick(self):
        pass

    async def publish(self, msg_type: str, payload: dict, to: str = "broadcast", symbol: str = None):
        await self.bus.publish(self.name, msg_type, payload, to, symbol=symbol)

    async def ask_claude(self, system_prompt: str, user_message: str, **kwargs) -> str:
        if self.llm is None:
            self.init_llm()
        return await self.llm.chat(system_prompt, user_message, **kwargs)

    async def ask_claude_json(self, system_prompt: str, user_message: str, **kwargs) -> dict:
        if self.llm is None:
            self.init_llm()
        return await self.llm.chat_json(system_prompt, user_message, **kwargs)

    def stop(self):
        self._running = False

    @property
    def uptime(self) -> float:
        return time.time() - self._start_time if self._start_time else 0
