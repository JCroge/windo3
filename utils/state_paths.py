"""状态文件命名空间解析器 (FR-008 / AC-P1-007~010)

设计目标：
- 让 `USE_TESTNET=true` 不再默认读写 live 状态文件，避免污染 live ledger
- 支持显式 `STATE_NAMESPACE=live|testnet|paper`
- live 默认（既不设 STATE_NAMESPACE 也不开 testnet）路径完全兼容历史：`data/positions.json` 等
- 不做任何自动迁移；testnet/paper 首次写入时按需创建

使用方式：
    from utils.state_paths import get_state_paths
    paths = get_state_paths()
    paths.positions  # 'data/positions.json' (live) / 'data/testnet_positions.json' / 'data/paper_positions.json'
    paths.halt_state, paths.risk_state, paths.riskguard_state, ...
    paths.live_order_events, paths.live_position_lifecycle  # ledger jsonl + lifecycle json
    paths.namespace  # 'live' | 'testnet' | 'paper'

注意：
- 命名空间解析读取环境变量，不读 yaml；run_agents.py 启动时已 load_dotenv()
- live 路径保持「裸文件名」以维持历史 layout；testnet/paper 加前缀
- 为避免循环依赖，本模块不依赖 utils.config_loader
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


_VALID_NAMESPACES = ('live', 'testnet', 'paper')


def _resolve_namespace(explicit: Optional[str] = None) -> str:
    """命名空间优先级：显式参数 > STATE_NAMESPACE 环境变量 > USE_TESTNET 推断 > live

    显式取值非法 / 未知时回退 live 并 print warning（不抛，避免启动崩溃）。
    """
    raw = explicit if explicit is not None else os.getenv('STATE_NAMESPACE', '')
    raw = (raw or '').strip().lower()
    if raw in _VALID_NAMESPACES:
        return raw
    if raw and raw not in _VALID_NAMESPACES:
        # 异常值 → live 兜底
        return 'live'

    # 未显式设置：USE_TESTNET=true 推断 testnet
    use_testnet = os.getenv('USE_TESTNET', '').strip().lower() in ('true', '1', 'yes', 'on')
    return 'testnet' if use_testnet else 'live'


def _prefix(namespace: str) -> str:
    """live 不加前缀（历史兼容）；其他在 data/ 下加 namespace_ 前缀。"""
    return '' if namespace == 'live' else f'{namespace}_'


@dataclass(frozen=True)
class StatePaths:
    """状态文件路径集合（不可变）。

    所有路径均为相对项目根的相对路径，仍指向 `data/` 子目录；
    namespace 仅决定 basename 前缀，不改变父目录。
    """
    namespace: str
    positions: str
    risk_state: str
    riskguard_state: str
    halt_state: str
    live_order_events: str
    live_position_lifecycle: str

    @classmethod
    def for_namespace(cls, namespace: Optional[str] = None) -> 'StatePaths':
        ns = _resolve_namespace(namespace)
        p = _prefix(ns)
        return cls(
            namespace=ns,
            positions=f'data/{p}positions.json',
            risk_state=f'data/{p}risk_state.json',
            riskguard_state=f'data/{p}riskguard_state.json',
            halt_state=f'data/{p}halt_state.json',
            live_order_events=f'data/{p}live_order_events.jsonl',
            live_position_lifecycle=f'data/{p}live_position_lifecycle.json',
        )

    def as_banner_lines(self) -> list:
        """供启动 banner 使用的展示行。"""
        bot_id = (os.getenv("BOT_INSTANCE_ID") or "").strip()
        lines = [
            f'  状态命名空间:          {self.namespace.upper()}',
            f'    positions          → {self.positions}',
            f'    risk_state         → {self.risk_state}',
            f'    riskguard_state    → {self.riskguard_state}',
            f'    halt_state         → {self.halt_state}',
            f'    live_order_events  → {self.live_order_events}',
            f'    live_position_life → {self.live_position_lifecycle}',
            f'    BOT_INSTANCE_ID    → {bot_id or "<empty>"}',
        ]
        if self.namespace == "live" and not bot_id:
            lines.append(
                "    WARNING: BOT_INSTANCE_ID not configured; "
                "cross-bot SL ownership cannot be proven by clOrdId."
            )
        return lines


_singleton: Optional[StatePaths] = None


def get_state_paths(namespace: Optional[str] = None, *, refresh: bool = False) -> StatePaths:
    """获取当前进程命名空间下的状态路径单例。

    Args:
        namespace: 显式指定（测试用）。None 则按环境变量推断。
        refresh: True 时强制重新解析（测试 reset 用）。
    """
    global _singleton
    if refresh or _singleton is None or (namespace is not None and namespace != _singleton.namespace):
        _singleton = StatePaths.for_namespace(namespace)
    return _singleton


def reset_state_paths() -> None:
    """测试辅助：清除单例缓存。"""
    global _singleton
    _singleton = None
