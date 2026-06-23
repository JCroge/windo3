"""手写标准蜡烛形态库(固定阈值,禁调)。observability-only 研究用。

阈值预登记锁死于 Design Doc 附录 A,严禁按结果调参。
detect_patterns(bars) -> list[(name:str, direction:int)],bars 升序
dict 列表 {"open","high","low","close"},判定序列末端命中的形态。
direction ∈ {+1 看涨, -1 看跌, 0 中性}。

注:同形不同位置(Hammer/Hanging Man、Inverted Hammer/Shooting Star)
由下游回测分桶(趋势 + range_pos)叠加上下文区分;识别器只返回形态
固有方向。歧义优先实现 Hammer / Shooting Star / Inverted Hammer 三个
明确形,Hanging Man 与 Hammer 同形不重复返回。
"""

EPS = 1e-9


def _f(b):
    o, h, l, c = b["open"], b["high"], b["low"], b["close"]
    rng = max(h - l, EPS)
    body = abs(c - o)
    up = h - max(o, c)
    lo = min(o, c) - l
    return o, h, l, c, rng, body, up, lo, (c > o)


def _single(b):
    o, h, l, c, rng, body, up, lo, bull = _f(b)
    out = []
    if body <= 0.1 * rng:
        out.append(("Doji", 0))
    if body <= 0.3 * rng and up >= body and lo >= body and body > 0.1 * rng:
        out.append(("Spinning Top", 0))
    if bull and body >= 0.9 * rng:
        out.append(("Bullish Marubozu", +1))
    if (not bull) and body >= 0.9 * rng:
        out.append(("Bearish Marubozu", -1))
    if lo >= 2 * body and up <= 0.3 * body and body > 0:
        out.append(("Hammer", +1))
    # 倒锤(跌势上下文由下游区分),固有方向 +1
    if up >= 2 * body and lo <= 0.3 * body and body > 0:
        out.append(("Inverted Hammer", +1))
    # 流星 = 同倒锤形,升势/高位上下文由下游区分,固有方向 -1
    if up >= 2 * body and lo <= 0.3 * body and body > 0:
        out.append(("Shooting Star", -1))
    if body <= 0.1 * rng and lo >= 2 * rng and up <= 0.1 * rng:
        out.append(("Dragonfly Doji", +1))
    if body <= 0.1 * rng and up >= 2 * rng and lo <= 0.1 * rng:
        out.append(("Gravestone Doji", -1))
    return out


def _double(prev, cur):
    po, ph, pl, pc, prng, pbody, pup, plo, pbull = _f(prev)
    co, ch, cl, cc, crng, cbody, cup, clo, cbull = _f(cur)
    out = []

    # Engulfing
    if (not pbull) and cbull and co <= pc and cc >= po and cbody > pbody:
        out.append(("Bullish Engulfing", +1))
    if pbull and (not cbull) and co >= pc and cc <= po and cbody > pbody:
        out.append(("Bearish Engulfing", -1))

    # Harami(今实体 ⊂ 昨实体 且 昨大实体 ≥1.5·今实体)
    p_hi, p_lo = max(po, pc), min(po, pc)
    c_hi, c_lo = max(co, cc), min(co, cc)
    contained = c_hi <= p_hi and c_lo >= p_lo
    if (not pbull) and cbull and contained and pbody >= 1.5 * cbody:
        out.append(("Bullish Harami", +1))
    if pbull and (not cbull) and contained and pbody >= 1.5 * cbody:
        out.append(("Bearish Harami", -1))

    # Piercing / Dark Cloud(穿入昨实体中点)
    p_mid = (po + pc) / 2.0
    if (not pbull) and cbull and co < pl and cc > p_mid:
        out.append(("Piercing Line", +1))
    if pbull and (not cbull) and co > ph and cc < p_mid:
        out.append(("Dark Cloud Cover", -1))

    # Tweezer(两根 low/high 近似相等 ±0.1%)
    if abs(cl - pl) <= 0.001 * max(abs(pl), EPS):
        out.append(("Tweezer Bottom", +1))
    if abs(ch - ph) <= 0.001 * max(abs(ph), EPS):
        out.append(("Tweezer Top", -1))

    # Kicker(跳空反扑)
    if (not pbull) and cbull and co > po:
        out.append(("Bullish Kicker", +1))
    if pbull and (not cbull) and co < po:
        out.append(("Bearish Kicker", -1))

    return out


def _triple(b1, b2, b3):
    o1, h1, l1, c1, rng1, body1, up1, lo1, bull1 = _f(b1)
    o2, h2, l2, c2, rng2, body2, up2, lo2, bull2 = _f(b2)
    o3, h3, l3, c3, rng3, body3, up3, lo3, bull3 = _f(b3)
    out = []

    mid1 = (o1 + c1) / 2.0
    # Morning / Evening Star:1 大实体 + 2 小实体 + 3 大实体收回 1 实体过半
    big1 = body1 >= 0.5 * rng1
    small2 = body2 <= 0.5 * body1 if body1 > 0 else False
    big3 = body3 >= 0.5 * rng3
    if (not bull1) and big1 and small2 and bull3 and big3 and c3 > mid1:
        out.append(("Morning Star", +1))
    if bull1 and big1 and small2 and (not bull3) and big3 and c3 < mid1:
        out.append(("Evening Star", -1))

    # Three Inside Up/Down = Harami(b1,b2) + 第3根突破第1根实体
    inside = _double(b1, b2)
    inside_names = {n for (n, _d) in inside}
    if "Bullish Harami" in inside_names and c3 > max(o1, c1):
        out.append(("Three Inside Up", +1))
    if "Bearish Harami" in inside_names and c3 < min(o1, c1):
        out.append(("Three Inside Down", -1))

    # Three Outside Up/Down = Engulfing(b1,b2) + 第3根继续
    eng_names = {n for (n, _d) in inside}
    if "Bullish Engulfing" in eng_names and c3 > c2:
        out.append(("Three Outside Up", +1))
    if "Bearish Engulfing" in eng_names and c3 < c2:
        out.append(("Three Outside Down", -1))

    # Abandoned Baby:1 实体 + 2 十字 向跳空孤立 + 3 反向跳空
    doji2 = body2 <= 0.1 * rng2
    if (not bull1) and doji2 and h2 < l1 and bull3 and l3 > h2:
        out.append(("Bullish Abandoned Baby", +1))
    if bull1 and doji2 and l2 > h1 and (not bull3) and h3 < l2:
        out.append(("Bearish Abandoned Baby", -1))

    # Three White Soldiers / Black Crows:连续同色,逐根推进,body≥0.6·rng,小影
    def _strong(body, rng, up, lo, bull):
        if bull:
            return body >= 0.6 * rng and up <= 0.3 * body
        return body >= 0.6 * rng and lo <= 0.3 * body
    if (bull1 and bull2 and bull3
            and c1 < c2 < c3 and o1 < o2 < o3
            and _strong(body1, rng1, up1, lo1, True)
            and _strong(body2, rng2, up2, lo2, True)
            and _strong(body3, rng3, up3, lo3, True)):
        out.append(("Three White Soldiers", +1))
    if ((not bull1) and (not bull2) and (not bull3)
            and c1 > c2 > c3 and o1 > o2 > o3
            and _strong(body1, rng1, up1, lo1, False)
            and _strong(body2, rng2, up2, lo2, False)
            and _strong(body3, rng3, up3, lo3, False)):
        out.append(("Three Black Crows", -1))

    return out


def _five(bars5):
    b1, b2, b3, b4, b5 = bars5
    o1, h1, l1, c1, rng1, body1, _u1, _lo1, bull1 = _f(b1)
    o5, h5, l5, c5, rng5, body5, _u5, _lo5, bull5 = _f(b5)
    out = []

    mids = [bars5[1], bars5[2], bars5[3]]
    big1 = body1 >= 0.5 * rng1
    big5 = body5 >= 0.5 * rng5

    # Rising Three Methods:1 大阳 + 3 小阴(未破 1 根 low)+ 5 大阳收新高
    mids_bear_inbound = all(
        (m["close"] < m["open"]) and m["low"] >= l1 and m["high"] <= h1
        for m in mids
    )
    if bull1 and big1 and mids_bear_inbound and bull5 and big5 and c5 > h1:
        out.append(("Rising Three Methods", +1))

    # Falling Three Methods 镜像
    mids_bull_inbound = all(
        (m["close"] > m["open"]) and m["high"] <= h1 and m["low"] >= l1
        for m in mids
    )
    if (not bull1) and big1 and mids_bull_inbound and (not bull5) and big5 and c5 < l1:
        out.append(("Falling Three Methods", -1))

    return out


def detect_patterns(bars):
    out = []
    if len(bars) >= 1:
        out += _single(bars[-1])
    if len(bars) >= 2:
        out += _double(bars[-2], bars[-1])
    if len(bars) >= 3:
        out += _triple(bars[-3], bars[-2], bars[-1])
    if len(bars) >= 5:
        out += _five(bars[-5:])
    return out
