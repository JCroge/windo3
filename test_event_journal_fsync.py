"""P2-20: event_journal 写关键事件后 MUST fsync，断电不丢最近关键事件
（与 atomic_io 已 fsync 标准对齐）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_append_fsyncs_after_write(monkeypatch, tmp_path):
    import utils.event_journal as ejmod
    from utils.event_journal import EventJournal, CRITICAL_TOPICS

    ej = EventJournal(journal_dir=str(tmp_path))
    topic = next(iter(CRITICAL_TOPICS))

    fsync_calls = []
    real_fsync = os.fsync
    monkeypatch.setattr(ejmod.os, 'fsync', lambda fd: (fsync_calls.append(fd), real_fsync(fd)))

    ej.append(topic, {'k': 'v'}, 'id1')

    assert fsync_calls, "append 关键事件后应调用 os.fsync"
