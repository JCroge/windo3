"""前向影子决策记录器（observability-only write-only）。

对 live 决策 bundle 旁路跑 both-levers（lever1+lever2）on 影子决策，write-only 记录
real vs shadow 供 lever1 增量对比。复用 `replay_decision` 隔离机器（mock 外部 await、
缓存 llm、捕获 publish 绝不进真实 bus、MultiJudge.__new__ 不碰 live 实例）。

红线：observability-only write-only —— 严禁交易决策/风控路径 import/读取本产物或日志。
fail-safe：影子任何异常绝不抛、绝不影响 live 决策。
"""
import json

from utils.decision_replay import replay_decision

# baseline = lever2-only（= live 现生效配置：l2 on / l1 off）
BASELINE_CONFIG = {"path_evidence_aligned_enabled": False, "ladder_rr_enabled": True}
# shadow = both levers on
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


def build_shadow_record(*, ts, symbol, real, baseline, shadow, tech_context):
    baseline_action = baseline.get("action")
    return {
        "timestamp": ts,
        "symbol": symbol,
        "real_action": real.get("action"),       # live 决策, 仅供自检追溯
        "real_gate": real.get("gate"),
        "baseline_action": baseline_action,       # replay(lever2-only)
        "baseline_gate": baseline.get("gate"),
        "shadow_action": shadow.get("action"),
        "shadow_gate": shadow.get("gate"),
        "shadow_plan": shadow.get("plan"),
        "baseline_mismatch": compute_baseline_mismatch(baseline_action, real.get("action")),
        "flip_kind": compute_flip_kind(baseline_action, shadow.get("action")),
        "tech_context": tech_context,
    }


def _summ(decision):
    return {"action": (decision or {}).get("action", "hold"),
            "gate": _gate_of(decision), "plan": (decision or {}).get("plan")}


async def log_shadow_decision(bundle, real_decision, log_path, *, enabled=True, logger=None):
    """两臂复盘 + baseline 自检, write-only 追加 jsonl。fail-safe：异常绝不抛。

    baseline=replay(lever2-only), shadow=replay(both-levers)；lever1 增量=两臂之差。
    baseline 复盘背离 live record 的 accept/reject → baseline_mismatch=True（排除）。
    返回写入的 record(成功) 或 None(关闭/不可回放/baseline 不可判定/异常跳过)。
    """
    if not enabled:
        return None
    try:
        if not (bundle or {}).get("replayable"):
            return None
        baseline = await replay_decision(bundle, BASELINE_CONFIG)
        if baseline is None:          # baseline 无法复盘 → 自检不可判定 → 跳过不写
            return None
        shadow = await replay_decision(bundle, SHADOW_CONFIG)
        rec = build_shadow_record(
            ts=bundle.get("timestamp", 0), symbol=bundle.get("symbol"),
            real=_summ(real_decision), baseline=_summ(baseline), shadow=_summ(shadow),
            tech_context=bundle.get("tech_analysis"))
        with open(log_path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return rec
    except Exception as e:                # fail-safe：影子绝不破 live
        if logger:
            logger.warning(f"[shadow] log_shadow_decision skipped: {e}")
        return None
