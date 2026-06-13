"""决策磁带：Judge 决策点全量输入+输出 bundle 的 append-only 落盘。
observability-only write-only —— 严禁任何交易决策路径读取本模块产物。"""
import json
import os
import time
import logging

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "decision_replay_record.v1"


def _jsonable(v):
    """递归把 set→sorted list，保证 JSON 可序列化。"""
    if isinstance(v, set):
        return sorted(_jsonable(x) for x in v)
    if isinstance(v, dict):
        return {k: _jsonable(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def build_bundle(*, symbol, decision, request_id, tech_analysis, price_at_decision,
                 regime_state, llm_output, llm_audit_ref, trade_decision_output,
                 state_snapshot=None):
    """构建一条 decision_replay_record。llm_output 内联（self-contained），
    llm_audit_ref 仅 best-effort 指针。state_snapshot 含决策前跨决策可变状态，
    存在时 replayable=True。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "timestamp": time.time(),
        "symbol": symbol,
        "decision": decision,
        "tech_analysis": tech_analysis,
        "price_at_decision": price_at_decision,
        "regime_state": regime_state,
        "llm_output_inline": llm_output,
        "llm_audit_ref": llm_audit_ref,
        "trade_decision_output": trade_decision_output,
        "state_snapshot_before_decision": _jsonable(state_snapshot) if state_snapshot is not None else None,
        "replayable": state_snapshot is not None,
    }


class DecisionTape:
    def __init__(self, path, enabled=True, retention_days=90, prune_every=500):
        self.path = path
        self.enabled = enabled
        self.retention_days = retention_days
        # Throttle: pruning rewrites the whole file (O(n)); doing it every write is
        # O(n^2) and runs in the async decision loop. Prune at most once per
        # `prune_every` writes so the hot path stays cheap.
        self.prune_every = max(1, int(prune_every))
        self._writes_since_prune = 0
        self.drop_count = 0

    def record_decision(self, bundle):
        if not self.enabled:
            return
        try:
            self._writes_since_prune += 1
            if self._writes_since_prune >= self.prune_every:
                self._writes_since_prune = 0
                self._maybe_prune()
            self._append_raw(bundle)
        except Exception as e:
            self.drop_count += 1
            logger.warning(f"[DecisionTape] drop (#{self.drop_count}): {e}")

    def _append_raw(self, bundle):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps(bundle, ensure_ascii=False) + "\n")

    def _maybe_prune(self):
        if not os.path.exists(self.path):
            return
        cutoff = time.time() - self.retention_days * 86400
        rows = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("timestamp", 0) >= cutoff:
                    rows.append(line)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            for line in rows:
                f.write(line + "\n")
        os.replace(tmp, self.path)
