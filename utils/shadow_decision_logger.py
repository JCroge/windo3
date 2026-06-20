"""前向影子决策记录器（observability-only write-only）。

对 live 决策 bundle 旁路跑 both-levers（lever1+lever2）on 影子决策，write-only 记录
real vs shadow 供 lever1 增量对比。复用 `replay_decision` 隔离机器（mock 外部 await、
缓存 llm、捕获 publish 绝不进真实 bus、MultiJudge.__new__ 不碰 live 实例）。

红线：observability-only write-only —— 严禁交易决策/风控路径 import/读取本产物或日志。
fail-safe：影子任何异常绝不抛、绝不影响 live 决策。
"""
import json

from utils.decision_replay import replay_decision

# 影子 = both levers on（live 现 lever2-only，故 shadow − real = lever1 纯增量）
SHADOW_CONFIG = {"path_evidence_aligned_enabled": True, "ladder_rr_enabled": True}


def _gate_of(decision):
    action = (decision or {}).get("action")
    if action in ("open_long", "open_short"):
        return "accept"
    blocked = (((decision or {}).get("attribution") or {}).get("blocked_by")
               or (decision or {}).get("reject_reason"))
    return str(blocked).split(":")[0] if blocked else "hold_other"


def _is_accept(action):
    return action in ("open_long", "open_short")


def compute_baseline_mismatch(baseline_action, real_action):
    """baseline 复现自检：replay(lever2-only) 的 accept/reject 必须复现 live record。

    不一致 → True（复盘失真，该条排除出 lever1 增量统计）。只比二元 accept/reject。
    """
    return _is_accept(baseline_action) != _is_accept(real_action)


def compute_flip_kind(baseline_action, shadow_action):
    """baseline(lever2-only) vs shadow(both-levers) 的开仓翻转类别。"""
    baseline_open = _is_accept(baseline_action)
    shadow_open = _is_accept(shadow_action)
    if baseline_open == shadow_open:
        return "same"
    return "shadow_opens" if shadow_open else "shadow_holds"


def build_shadow_record(*, ts, symbol, real, shadow, tech_context):
    return {
        "timestamp": ts,
        "symbol": symbol,
        "real_action": real.get("action"),
        "real_gate": real.get("gate"),
        "shadow_action": shadow.get("action"),
        "shadow_gate": shadow.get("gate"),
        "shadow_plan": shadow.get("plan"),
        "flip_kind": compute_flip_kind(real.get("action"), shadow.get("action")),
        "tech_context": tech_context,
    }


async def log_shadow_decision(bundle, real_decision, log_path, *, enabled=True, logger=None):
    """旁路跑 both-levers 影子决策并 write-only 追加 jsonl。fail-safe：异常绝不抛。

    返回写入的 record（成功）或 None（关闭/不可回放/异常跳过）。
    """
    if not enabled:
        return None
    try:
        if not (bundle or {}).get("replayable"):
            return None
        shadow = await replay_decision(bundle, SHADOW_CONFIG)
        real_summ = {"action": (real_decision or {}).get("action", "hold"),
                     "gate": _gate_of(real_decision)}
        shadow_summ = {"action": (shadow or {}).get("action", "hold"),
                       "gate": _gate_of(shadow), "plan": (shadow or {}).get("plan")}
        rec = build_shadow_record(
            ts=bundle.get("timestamp", 0), symbol=bundle.get("symbol"),
            real=real_summ, shadow=shadow_summ,
            tech_context=bundle.get("tech_analysis"))
        with open(log_path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return rec
    except Exception as e:                # fail-safe：影子绝不破 live
        if logger:
            logger.warning(f"[shadow] log_shadow_decision skipped: {e}")
        return None
