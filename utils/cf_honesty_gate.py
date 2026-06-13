"""诚实性 gate：胜率 Wilson 区间 + 净 PnL bootstrap 区间 + 三档样本量。
所有方向/PnL 结论的单一收口；薄样本拒答，防过拟合噪声。
observability-only。"""
import math
import random

_Z = 1.96  # 95%


def wilson_interval(wins: int, n: int, z: float = _Z):
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def bootstrap_mean_ci(samples, iters: int = 2000, seed: int = 1234, z: float = _Z):
    if not samples:
        return (0.0, 0.0)
    rng = random.Random(seed)  # 固定种子 → 确定性，可测
    n = len(samples)
    means = []
    for _ in range(iters):
        s = sum(samples[rng.randrange(n)] for _ in range(n)) / n
        means.append(s)
    means.sort()
    lo = means[int(0.025 * iters)]
    hi = means[int(0.975 * iters) - 1]
    return (lo, hi)


def summarize_bucket(*, wins: int, losses: int, net_usdt_samples,
                     min_sample: int = 30, lowconf_sample: int = 100):
    n = wins + losses
    wr_ci = wilson_interval(wins, n)
    pnl_ci = bootstrap_mean_ci(list(net_usdt_samples))
    out = {
        "n": n,
        "win_rate": (wins / n) if n else 0.0,
        "win_rate_ci": wr_ci,
        "net_pnl_mean": (sum(net_usdt_samples) / len(net_usdt_samples)) if net_usdt_samples else 0.0,
        "net_pnl_ci": pnl_ci,
    }
    if n < min_sample:
        out["verdict"] = "INSUFFICIENT_SAMPLE"
        out["direction"] = None
    elif n < lowconf_sample:
        out["verdict"] = "low_confidence"
    else:
        actionable = pnl_ci[0] > 0 or pnl_ci[1] < 0  # CI 不跨 0
        out["verdict"] = "actionable" if actionable else "inconclusive"
    return out
