"""Critical event journal — append-only JSONL for trade lifecycle replay

关键交易消息落盘，进程崩溃后能重放最近交易生命周期。
写失败只告警，不阻塞交易。
"""

import json
import os
import time
import hashlib
from typing import Optional


JOURNAL_DIR = "data/journal"
CRITICAL_TOPICS = frozenset({
    'trade_decision', 'tactical_candidate.v2', 'execution_result',
    'daily_hard_stop_triggered', 'system_command', 'risk_alert',
})


class EventJournal:
    """Append-only JSONL event journal"""

    def __init__(self, journal_dir: str = JOURNAL_DIR):
        self._dir = journal_dir
        self._fd: Optional[object] = None
        self._current_date: str = ""
        os.makedirs(self._dir, exist_ok=True)

    def should_record(self, topic: str) -> bool:
        return topic in CRITICAL_TOPICS

    def append(self, topic: str, message: dict, msg_id: str = ""):
        try:
            payload_str = json.dumps(message, ensure_ascii=False, default=str)
            entry = {
                "topic": topic,
                "msg_id": msg_id,
                "timestamp": time.time(),
                "payload_hash": hashlib.md5(payload_str.encode()).hexdigest()[:12],
                "payload": message,
            }
            self._write_line(json.dumps(entry, ensure_ascii=False, default=str))
        except Exception:
            pass

    def replay(self, filter_key: str = None, filter_value: str = None,
               since: float = 0, limit: int = 1000) -> list:
        """重放事件，支持按 request_id/symbol/topic 过滤"""
        results = []
        for filepath in sorted(self._list_files()):
            try:
                with open(filepath, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        entry = json.loads(line)
                        if entry.get('timestamp', 0) < since:
                            continue
                        if filter_key and filter_value:
                            payload = entry.get('payload', {})
                            if filter_key == 'topic':
                                if entry.get('topic') != filter_value:
                                    continue
                            elif payload.get(filter_key) != filter_value:
                                continue
                        results.append(entry)
                        if len(results) >= limit:
                            return results
            except Exception:
                continue
        return results

    def replay_messages(self, topic: str, *, namespace: str, now: float,
                        max_age_seconds: float, limit: int = 1000) -> list:
        """Return replay envelopes that remain valid in the target namespace."""
        expected_namespace = str(namespace or "").strip().lower()
        if not expected_namespace:
            return []

        replayed = []
        for entry in self.replay(filter_key="topic", filter_value=topic, limit=limit):
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            payload_namespace = str(payload.get("namespace") or "").strip().lower()
            if payload_namespace != expected_namespace:
                continue
            try:
                age = float(now) - float(payload.get("created_at"))
            except (TypeError, ValueError):
                continue
            if age < 0 or age > float(max_age_seconds):
                continue
            replayed.append({
                "msg_id": entry.get("msg_id", ""),
                "type": entry.get("topic", topic),
                "timestamp": entry.get("timestamp", 0),
                "payload": payload,
            })
        return replayed

    def _write_line(self, line: str):
        today = time.strftime("%Y%m%d", time.gmtime())
        if today != self._current_date or self._fd is None:
            if self._fd:
                self._fd.close()
            filepath = os.path.join(self._dir, f"events_{today}.jsonl")
            self._fd = open(filepath, 'a')
            self._current_date = today
        self._fd.write(line + "\n")
        self._fd.flush()
        # P2-20: 落盘到磁盘（非仅 OS page cache），断电不丢最近关键事件。
        # journal 只记低频 critical topic，fsync 成本可接受。
        try:
            os.fsync(self._fd.fileno())
        except OSError:
            pass

    def _list_files(self) -> list:
        try:
            return [
                os.path.join(self._dir, f)
                for f in sorted(os.listdir(self._dir))
                if f.startswith("events_") and f.endswith(".jsonl")
            ]
        except Exception:
            return []

    def close(self):
        if self._fd:
            self._fd.close()
            self._fd = None


_instance: Optional[EventJournal] = None


def get_event_journal() -> EventJournal:
    global _instance
    if _instance is None:
        _instance = EventJournal()
    return _instance
