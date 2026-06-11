"""Agent 基类 - 所有 Agent 的抽象基础"""

import asyncio
import time
from abc import ABC, abstractmethod
from agents.message_bus import MessageBus
from agents.llm_client import LLMClient, LLMUnavailableError
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
            try:
                self.llm = LLMClient()
            except Exception as e:
                self.logger.warning(f"LLM 客户端创建失败，将走规则降级: {e}")
                self.llm = None

    @property
    def llm_available(self) -> bool:
        """LLM 是否可用。Agent 的 _ask_llm/_llm_xxx 应先检查此属性。"""
        return self.llm is not None and getattr(self.llm, 'available', False)

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

        try:
            await self.setup()
        except Exception:
            import traceback
            self.logger.critical(
                f"Agent [{self.name}] setup 失败\n{traceback.format_exc()}"
            )
            raise

        msg_task = asyncio.create_task(self._message_loop())
        tick_task = asyncio.create_task(self._periodic_loop())

        try:
            await asyncio.gather(msg_task, tick_task)
        except asyncio.CancelledError:
            msg_task.cancel()
            tick_task.cancel()

        self.logger.info(f"Agent [{self.name}] 停止")

    async def _message_loop(self):
        """快速消费消息，不被 tick sleep 阻塞"""
        while self._running and not self._should_stop:
            try:
                msg = await self.bus.receive(self.name, timeout=0.5)
                if msg:
                    await self.on_message(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                import traceback
                self.logger.error(f"消息处理错误: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(1)

    async def _periodic_loop(self):
        """独立周期任务，不阻塞消息消费"""
        while self._running and not self._should_stop:
            try:
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                import traceback
                self.logger.error(f"tick错误: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(1)

    async def tick(self):
        await asyncio.sleep(1)

    async def publish(self, msg_type: str, payload: dict, to: str = "broadcast", symbol: str = None):
        await self.bus.publish(self.name, msg_type, payload, to, symbol=symbol)

    async def ask_claude(self, system_prompt: str, user_message: str, **kwargs) -> str:
        if self.llm is None:
            self.init_llm()
        if not self.llm_available:
            raise LLMUnavailableError("LLM 不可用")
        return await self.llm.chat(system_prompt, user_message, **kwargs)

    async def ask_claude_json(self, system_prompt: str, user_message: str, **kwargs) -> dict:
        if self.llm is None:
            self.init_llm()
        if not self.llm_available:
            raise LLMUnavailableError("LLM 不可用")
        # 自动注入 caller 用于审计追踪
        kwargs.setdefault('caller', self.name)
        return await self.llm.chat_json(system_prompt, user_message, **kwargs)

    def stop(self):
        self._running = False

    @property
    def uptime(self) -> float:
        return time.time() - self._start_time if self._start_time else 0
