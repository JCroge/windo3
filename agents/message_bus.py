"""消息总线 - Agent 间通信基础设施（支持 symbol-scoped 路由）"""

import asyncio
import uuid
import time
from typing import Optional
from utils.logger import setup_logger


class MessageBus:
    """基于 asyncio Queue 的进程内消息总线，支持 topic:symbol 路由"""

    _instance = None
    _queues: dict = {}
    _subscriptions: dict = {}

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        cls._instance = None
        cls._queues = {}
        cls._subscriptions = {}

    def __init__(self):
        self.logger = setup_logger('message_bus')

    def register(self, agent_name: str, topics: list):
        """注册Agent及其订阅的topic列表。

        支持格式：
        - "market_data"          精确匹配
        - "market_data:SOL-USDT" 精确匹配（含symbol）
        - "market_data:*"        通配符（匹配所有symbol的market_data）
        """
        if agent_name not in self._queues:
            self._queues[agent_name] = None  # 延迟创建Queue
        for topic in topics:
            if topic not in self._subscriptions:
                self._subscriptions[topic] = []
            if agent_name not in self._subscriptions[topic]:
                self._subscriptions[topic].append(agent_name)

    def _ensure_queue(self, agent_name: str):
        """确保Queue在当前event loop中创建"""
        if agent_name in self._queues and self._queues[agent_name] is None:
            self._queues[agent_name] = asyncio.Queue()

    async def publish(self, from_agent: str, msg_type: str, payload: dict,
                      to: str = "broadcast", symbol: str = None):
        """发布消息。

        Args:
            from_agent: 发送者名称
            msg_type: 消息类型（如 "market_data", "tech_analysis"）
            payload: 消息内容
            to: "broadcast" 或 目标agent名称
            symbol: 可选，标的符号。设置后消息会路由到订阅了
                    "msg_type:symbol" 或 "msg_type:*" 的agent
        """
        msg = {
            "msg_id": str(uuid.uuid4()),
            "from": from_agent,
            "to": to,
            "type": msg_type,
            "symbol": symbol,
            "timestamp": time.time(),
            "payload": payload
        }

        if to == "broadcast":
            subscribers = self._find_subscribers(msg_type, symbol, from_agent)
            for agent_name in subscribers:
                self._ensure_queue(agent_name)
                await self._queues[agent_name].put(msg)
        else:
            if to in self._queues:
                self._ensure_queue(to)
                await self._queues[to].put(msg)

    def _find_subscribers(self, msg_type: str, symbol: Optional[str],
                          exclude: str) -> set:
        """查找所有匹配的订阅者。

        匹配规则（按优先级）：
        1. 精确匹配 "msg_type:symbol"（如果有symbol）
        2. 通配符匹配 "msg_type:*"（如果有symbol）
        3. 无scope匹配 "msg_type"（向后兼容）
        """
        matched = set()

        if symbol:
            scoped_topic = f"{msg_type}:{symbol}"
            if scoped_topic in self._subscriptions:
                matched.update(self._subscriptions[scoped_topic])

            wildcard_topic = f"{msg_type}:*"
            if wildcard_topic in self._subscriptions:
                matched.update(self._subscriptions[wildcard_topic])

        if msg_type in self._subscriptions:
            matched.update(self._subscriptions[msg_type])

        matched.discard(exclude)
        return matched

    async def receive(self, agent_name: str, timeout: float = 1.0) -> Optional[dict]:
        if agent_name not in self._queues:
            return None
        self._ensure_queue(agent_name)
        try:
            return await asyncio.wait_for(
                self._queues[agent_name].get(), timeout=timeout
            )
        except asyncio.TimeoutError:
            return None

    def close(self):
        pass
