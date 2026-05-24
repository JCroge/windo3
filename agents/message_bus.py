"""消息总线 - Agent 间通信基础设施（支持 symbol-scoped 路由 + 优先级 + 背压 + 死信）

P1-K 升级要点：
1. **优先级**：critical (risk_alert/halt) > high (trade_decision) > normal (tech/position) > low (market_data/news)
2. **背压**：队列长度阈值（500/1000）超出时丢弃 LOW 优先级消息
3. **死信**：派发失败的消息写入全局 dead_letter（仅保留最近 200 条供运维查阅）
4. **Metrics**：每个 agent queue size、dropped count、dlq count
5. **向后兼容**：publish/register/receive API 不变，priority 自动判断
"""

import asyncio
import uuid
import time
from typing import Optional, Any
from dataclasses import dataclass, field
from collections import deque
from utils.logger import setup_logger
from utils.event_journal import get_event_journal


# 优先级（数字越小越高）
PRIORITY_CRITICAL = 0
PRIORITY_HIGH = 1
PRIORITY_NORMAL = 2
PRIORITY_LOW = 3

# msg_type → priority 映射（未在映射中的消息默认 NORMAL）
_PRIORITY_MAP = {
    # CRITICAL: 风控/熔断/异常
    'daily_hard_stop_triggered': PRIORITY_CRITICAL,
    'risk_alert': PRIORITY_CRITICAL,
    'emergency_close': PRIORITY_CRITICAL,
    # HIGH: 交易决策与执行
    'system_command': PRIORITY_HIGH,
    'trade_decision': PRIORITY_HIGH,
    'execution_result': PRIORITY_HIGH,
    'position_verdict': PRIORITY_HIGH,
    # NORMAL: 分析与审查
    'tech_analysis': PRIORITY_NORMAL,
    'position_review': PRIORITY_NORMAL,
    'strategy_review': PRIORITY_NORMAL,
    'censor_review': PRIORITY_NORMAL,
    'synthesizer_result': PRIORITY_NORMAL,
    'symbol_update': PRIORITY_NORMAL,
    # LOW: 高频数据采集
    'market_data': PRIORITY_LOW,
    'news_snapshot': PRIORITY_LOW,
    'price_tick': PRIORITY_LOW,
}

# 背压阈值
_QSIZE_SOFT_LIMIT = 500   # 超过此值开始丢 LOW
_QSIZE_HARD_LIMIT = 1000  # 超过此值丢 LOW 和 NORMAL
_DLQ_MAX = 200            # 死信队列最大保留条数

_IMPORTANT_TOPICS = frozenset({
    'trade_decision', 'execution_result', 'risk_alert',
    'daily_hard_stop_triggered', 'data_alert', 'system_command',
})


@dataclass(order=True)
class _Envelope:
    """带优先级的消息信封（sort key 是 (priority, seq)）。"""
    priority: int
    seq: int
    msg: Any = field(compare=False)


def get_priority(msg_type: str) -> int:
    """根据 msg_type 推断优先级，未知则 NORMAL。"""
    return _PRIORITY_MAP.get(msg_type, PRIORITY_NORMAL)


class MessageBus:
    """基于 asyncio Queue 的进程内消息总线。"""

    _instance = None
    _queues: dict = {}
    _subscriptions: dict = {}
    _metrics: dict = {}  # agent_name -> {dropped, dlq, total_in, total_out}
    _dead_letter = deque(maxlen=_DLQ_MAX)
    _seq_counter = 0

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
        cls._metrics = {}
        cls._dead_letter = deque(maxlen=_DLQ_MAX)
        cls._seq_counter = 0

    def __init__(self):
        self.logger = setup_logger('message_bus')

    def register(self, agent_name: str, topics: list):
        """注册Agent及其订阅的topic列表。"""
        if agent_name not in self._queues:
            self._queues[agent_name] = None
            self._metrics[agent_name] = {
                'dropped': 0, 'dlq': 0, 'total_in': 0, 'total_out': 0
            }
        for topic in topics:
            if topic not in self._subscriptions:
                self._subscriptions[topic] = []
            if agent_name not in self._subscriptions[topic]:
                self._subscriptions[topic].append(agent_name)

    def _ensure_queue(self, agent_name: str):
        if agent_name in self._queues and self._queues[agent_name] is None:
            self._queues[agent_name] = asyncio.PriorityQueue()
        if agent_name not in self._metrics:
            self._metrics[agent_name] = {
                'dropped': 0, 'dlq': 0, 'total_in': 0, 'total_out': 0
            }

    def _next_seq(self) -> int:
        cls = type(self)
        cls._seq_counter += 1
        return cls._seq_counter

    async def publish(self, from_agent: str, msg_type: str, payload: dict,
                      to: str = "broadcast", symbol: str = None,
                      priority: int = None):
        """发布消息（priority 可显式覆盖；不传则按 msg_type 推断）。"""
        if priority is None:
            priority = get_priority(msg_type)

        msg = {
            "msg_id": str(uuid.uuid4()),
            "from": from_agent,
            "to": to,
            "type": msg_type,
            "symbol": symbol,
            "timestamp": time.time(),
            "priority": priority,
            "payload": payload
        }

        journal = get_event_journal()
        if journal.should_record(msg_type):
            journal.append(msg_type, payload, msg_id=msg["msg_id"])

        if to == "broadcast":
            subscribers = self._find_subscribers(msg_type, symbol, from_agent)
            if not subscribers and msg_type in _IMPORTANT_TOPICS:
                self.logger.warning(f"无订阅者: topic={msg_type} from={from_agent}")
                self._record_dlq(msg, reason="no_subscriber")
            for agent_name in subscribers:
                await self._enqueue(agent_name, msg, priority)
        else:
            if to in self._queues:
                await self._enqueue(to, msg, priority)
            else:
                self._record_dlq(msg, reason="unknown_target")

    async def _enqueue(self, agent_name: str, msg: dict, priority: int):
        """带背压控制的入队。"""
        self._ensure_queue(agent_name)
        q = self._queues[agent_name]
        qsize = q.qsize()

        # 背压：根据 qsize 和 priority 决定是否丢弃
        if qsize >= _QSIZE_HARD_LIMIT and priority >= PRIORITY_NORMAL:
            self._metrics[agent_name]['dropped'] += 1
            self.logger.warning(
                f"背压HARD丢弃: {agent_name} q={qsize} type={msg['type']} prio={priority}"
            )
            return
        if qsize >= _QSIZE_SOFT_LIMIT and priority >= PRIORITY_LOW:
            self._metrics[agent_name]['dropped'] += 1
            return

        try:
            env = _Envelope(priority=priority, seq=self._next_seq(), msg=msg)
            await q.put(env)
            self._metrics[agent_name]['total_in'] += 1
        except Exception as e:
            self._record_dlq(msg, reason=f"enqueue_error:{e}")
            self._metrics[agent_name]['dlq'] += 1

    def _record_dlq(self, msg: dict, reason: str):
        """写入死信队列（仅日志/运维用）。"""
        self._dead_letter.append({
            'ts': time.time(),
            'msg_id': msg.get('msg_id'),
            'from': msg.get('from'),
            'to': msg.get('to'),
            'type': msg.get('type'),
            'reason': reason,
        })

    def _find_subscribers(self, msg_type: str, symbol: Optional[str],
                          exclude: str) -> set:
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
        """按优先级取下一条消息（同优先级 FIFO）。"""
        if agent_name not in self._queues:
            return None
        self._ensure_queue(agent_name)
        try:
            env = await asyncio.wait_for(
                self._queues[agent_name].get(), timeout=timeout
            )
            self._metrics[agent_name]['total_out'] += 1
            return env.msg
        except asyncio.TimeoutError:
            return None

    def get_metrics(self) -> dict:
        """返回总线运行指标快照（供 /status 命令展示）。"""
        snapshot = {}
        for agent, m in self._metrics.items():
            q = self._queues.get(agent)
            snapshot[agent] = {
                **m,
                'pending': q.qsize() if q is not None else 0,
            }
        snapshot['_dlq_size'] = len(self._dead_letter)
        return snapshot

    def get_dead_letters(self, limit: int = 50) -> list:
        """返回最近的死信消息（最多 limit 条）。"""
        return list(self._dead_letter)[-limit:]

    def close(self):
        pass
