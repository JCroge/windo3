"""统一全局熔断状态管理

所有 Agent 通过此模块读写熔断状态，确保一致性。
状态持久化到 data/halt_state.json（live 默认）；testnet / paper 由 STATE_NAMESPACE 切到独立文件。
重启后自动恢复。
"""

import json
import os
import time
from typing import Optional

from utils.state_paths import get_state_paths


# 模块级覆盖入口：测试可通过 monkeypatch 设置 HALT_STATE_FILE 来重定向状态文件路径
HALT_STATE_FILE: Optional[str] = None


def _halt_state_file() -> str:
    return HALT_STATE_FILE or get_state_paths().halt_state


class HaltState:
    """全局熔断状态结构体"""

    def __init__(self):
        self.halted: bool = False
        self.reason: str = ""
        self.triggered_at: float = 0.0
        self.triggered_by: str = ""
        self.resume_at: float = 0.0
        self.resume_by: str = ""
        self.reconciliation_pending: bool = False
        self.reconciliation_result: Optional[str] = None
        self._load()

    @property
    def can_open_new(self) -> bool:
        """是否允许新开仓（熔断或对账未完成时禁止）"""
        return not self.halted and not self.reconciliation_pending

    def halt(self, reason: str, triggered_by: str) -> dict:
        """触发熔断"""
        self.halted = True
        self.reason = reason
        self.triggered_at = time.time()
        self.triggered_by = triggered_by
        self.resume_at = 0.0
        self.resume_by = ""
        self.reconciliation_pending = False
        self.reconciliation_result = None
        self._save()
        return self.to_dict()

    def request_resume(self, resume_by: str) -> dict:
        """请求解除熔断 — 进入 reconciliation_pending 状态"""
        if not self.halted:
            return self.to_dict()
        self.reconciliation_pending = True
        self.reconciliation_result = None
        self._save()
        return self.to_dict()

    def confirm_resume(self, resume_by: str, reconcile_ok: bool) -> dict:
        """对账完成后确认解除（或维持熔断）"""
        if reconcile_ok:
            self.halted = False
            self.resume_at = time.time()
            self.resume_by = resume_by
            self.reconciliation_pending = False
            self.reconciliation_result = "matched"
        else:
            self.reconciliation_pending = False
            self.reconciliation_result = "mismatch"
            # 保持 halted=True
        self._save()
        return self.to_dict()

    def force_resume(self, resume_by: str) -> dict:
        """强制解除（跳过对账，仅用于紧急情况）"""
        self.halted = False
        self.resume_at = time.time()
        self.resume_by = resume_by
        self.reconciliation_pending = False
        self.reconciliation_result = "force_skipped"
        self._save()
        return self.to_dict()

    def to_dict(self) -> dict:
        return {
            "halted": self.halted,
            "reason": self.reason,
            "triggered_at": self.triggered_at,
            "triggered_by": self.triggered_by,
            "resume_at": self.resume_at,
            "resume_by": self.resume_by,
            "reconciliation_pending": self.reconciliation_pending,
            "reconciliation_result": self.reconciliation_result,
            "can_open_new": self.can_open_new,
        }

    def _save(self):
        path = _halt_state_file()
        try:
            os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
            from utils.atomic_io import atomic_write_json
            atomic_write_json(path, self.to_dict())
        except Exception as e:
            # P2-21: 原子写失败不退化为非原子裸写（可能写半截最关键的 halt 文件）。
            # 只记录错误；文件损坏/缺失时 _load 仍 fail-closed（halt=True）兜底。
            try:
                import logging
                logging.getLogger('halt_state').error(
                    f"halt_state 原子写失败，未持久化: {e}"
                )
            except Exception:
                pass

    def _load(self):
        path = _halt_state_file()
        if not os.path.exists(path):
            return
        try:
            with open(path, 'r') as f:
                state = json.load(f)
            self.halted = state.get("halted", False)
            self.reason = state.get("reason", "")
            self.triggered_at = state.get("triggered_at", 0.0)
            self.triggered_by = state.get("triggered_by", "")
            self.resume_at = state.get("resume_at", 0.0)
            self.resume_by = state.get("resume_by", "")
            self.reconciliation_pending = state.get("reconciliation_pending", False)
            self.reconciliation_result = state.get("reconciliation_result")
        except (json.JSONDecodeError, ValueError, TypeError, KeyError) as e:
            self.halted = True
            self.reason = f"state_load_failed: {type(e).__name__}"
            self.triggered_at = time.time()
            self.triggered_by = "system_startup"
            self.reconciliation_pending = True
        except Exception:
            self.halted = True
            self.reason = "state_load_failed: unknown"
            self.triggered_at = time.time()
            self.triggered_by = "system_startup"
            self.reconciliation_pending = True


# 全局单例
_instance: Optional[HaltState] = None


def get_halt_state() -> HaltState:
    global _instance
    if _instance is None:
        _instance = HaltState()
    return _instance
