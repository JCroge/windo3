"""原子文件写入工具 — 防止进程崩溃/SIGKILL导致状态文件损坏

策略：写入临时文件 → fsync → os.replace（POSIX 原子操作）
- 读到的要么是旧版完整数据，要么是新版完整数据，不会出现半写状态
- 适用于 positions.json / trade_history.json / risk_state.json / riskguard_state.json
"""

import json
import os
from typing import Any


def atomic_write_json(path: str, data: Any, indent: int = 2) -> None:
    """原子写 JSON 文件

    Args:
        path: 目标文件路径
        data: 可序列化对象
        indent: JSON 缩进（默认 2）

    Raises:
        OSError / TypeError: 序列化或写入失败时抛出，上层应捕获并记录
    """
    dir_name = os.path.dirname(path) or '.'
    os.makedirs(dir_name, exist_ok=True)

    tmp_path = f"{path}.tmp"
    with open(tmp_path, 'w') as f:
        json.dump(data, f, indent=indent)
        f.flush()
        os.fsync(f.fileno())  # 确保数据落盘
    os.replace(tmp_path, path)  # POSIX 原子重命名
