"""P2-21: halt_state._save 删除非原子裸写兜底。

atomic_write_json 失败时 MUST 只记录错误，MUST NOT 退化为非原子 open(path,'w')+json.dump
（可能写半截最关键的 halt 文件）。损坏时 _load 仍 fail-closed（halt=True）兜底。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _make_halt_state():
    from utils.halt_state import HaltState
    hs = HaltState.__new__(HaltState)  # 跳过 __init__ 的 _load
    hs.halted = True
    hs.reason = "test"
    hs.triggered_at = 0.0
    hs.triggered_by = "test"
    hs.resume_at = 0.0
    hs.resume_by = ""
    hs.reconciliation_pending = False
    hs.reconciliation_result = None
    return hs


def test_save_no_naked_json_dump_on_atomic_failure(monkeypatch, tmp_path):
    import utils.atomic_io
    import utils.halt_state as hsmod

    # 重定向到 tmp，避免触碰真实 halt 文件
    monkeypatch.setattr(hsmod, 'HALT_STATE_FILE', str(tmp_path / 'halt.json'))

    def boom(*a, **k):
        raise IOError("disk full")
    monkeypatch.setattr(utils.atomic_io, 'atomic_write_json', boom)

    dump_calls = []
    monkeypatch.setattr(hsmod.json, 'dump', lambda *a, **k: dump_calls.append(1))

    hs = _make_halt_state()
    hs._save()  # 不应抛

    assert dump_calls == [], "atomic 写失败时不应退化为裸写 json.dump"
