"""FR-3D: 新闻 ticker mention 边界匹配.

统一新闻 / 行情侧 symbol 提及检测,替代历史 `if symbol in text` 的 substring 匹配,
避免短 ticker(OP/STX/INJ/...)被普通英文片段(options/stack/injection/...)误报。

详见 docs/audit_remediation_third_pass_20260528_prd.md FR-3D
与 docs/audit_remediation_third_pass_20260528_acceptance.md §7.1。

匹配规则与置信度:
  - cashtag    `$OP`           confidence=1.0
  - paren      `Stacks (STX)`  confidence=0.95
  - pair       `INJ/USDT`、`INJ-USDT`、`INJ-PERP` 等  confidence=0.95
  - keyword    `OP token`、`STX coin`、`INJ network` 等显式 token 关键词  confidence=0.85
  - word       裸 token 边界匹配(且不在常见英文短词黑名单内)  confidence=0.6

API:
  extract_symbol_mentions(headlines, symbols, *, now_ts=None, freshness_window=None)
    -> { symbol: {count, confidence, match_rules, headlines, headlines_meta} }

每条 headline 内若多次匹配,按置信度最高的 match_rule 计入 mention,count 累加。
"""

from __future__ import annotations

import re
import time
from typing import Iterable, List, Optional


# ── 内部常量 ─────────────────────────────────────────────────────────────

# 极少量"裸 word 高歧义"短 ticker:即便边界正则放行,也只在文本同时含 token
# 关键词或 cashtag/pair 时才允许。这里只放真正高歧义的:
# - "TON"   常作 "tons of" / "tonight"
# - "ARB"   常作 "arbitrage" / "arb" 缩写
# - "NEAR"  常作 "near future"
# 注意:正则边界(?<![A-Z0-9])SYM(?![A-Z0-9]) 已经能拒掉 OP/OPTIONS、INJ/INJECTION、
# STX/STACK 等子串误报,大多数短 ticker 不需要进入此名单。
_HIGH_AMBIGUITY_BARE_WORD = {"TON", "ARB", "NEAR", "DOT", "FIL", "OP", "FET"}

# 关键词后缀:出现这些词时认为是显式 token 引用
_TOKEN_KEYWORDS = (
    "TOKEN", "COIN", "PROTOCOL", "NETWORK", "CHAIN", "ECOSYSTEM",
    "BLOCKCHAIN", "PRICE", "RALLY", "RALLIES", "DUMP", "PUMP",
    "FUTURES", "PERPETUAL", "PERP", "STAKING", "AIRDROP",
)

# pair quote 列表
_PAIR_QUOTES = ("USDT", "USD", "USDC", "BTC", "ETH", "PERP")

# 边界(非字母数字)
_BOUNDARY = r"(?<![A-Z0-9])"
_BOUNDARY_RIGHT = r"(?![A-Z0-9])"


def _build_patterns(symbol: str):
    s = re.escape(symbol)
    pair_alt = "|".join(_PAIR_QUOTES)
    return {
        "cashtag": re.compile(rf"\${s}{_BOUNDARY_RIGHT}"),
        "paren": re.compile(rf"\({s}\)"),
        "pair": re.compile(rf"{_BOUNDARY}{s}[/\-]({pair_alt}){_BOUNDARY_RIGHT}"),
        "keyword": re.compile(
            rf"{_BOUNDARY}{s}{_BOUNDARY_RIGHT}\s+("
            + "|".join(_TOKEN_KEYWORDS) + ")"
        ),
        "word": re.compile(rf"{_BOUNDARY}{s}{_BOUNDARY_RIGHT}"),
    }


_RULE_CONFIDENCE = {
    "cashtag": 1.0,
    "paren": 0.95,
    "pair": 0.95,
    "keyword": 0.85,
    "word": 0.6,
}


def _allow_word_rule(symbol: str) -> bool:
    """word(裸边界)规则的最终放行判定。

    高歧义短 ticker(TON/ARB/NEAR 等)即便边界匹配也不放行 word 规则,只能通过
    cashtag/paren/pair/keyword 命中,避免 "tons of"、"arb itself"、"near future"
    被误报为 token 提及。
    """
    return symbol.upper() not in _HIGH_AMBIGUITY_BARE_WORD


def match_symbol_in_text(symbol: str, text: str) -> Optional[dict]:
    """对单条文本判定 symbol 是否被提及。

    返回 None 或 {match_rule, confidence}。多规则命中时按置信度最高的返回。
    """
    if not symbol or not text:
        return None
    s = symbol.upper()
    t = text.upper()
    patterns = _build_patterns(s)

    best = None
    for rule in ("cashtag", "paren", "pair", "keyword", "word"):
        if rule == "word" and not _allow_word_rule(s):
            continue
        if patterns[rule].search(t):
            conf = _RULE_CONFIDENCE[rule]
            if best is None or conf > best["confidence"]:
                best = {"match_rule": rule, "confidence": conf}
            if conf >= 1.0:
                break
    return best


def extract_symbol_mentions(
    headlines: Iterable[dict],
    symbols: Iterable[str],
    *,
    now_ts: Optional[float] = None,
) -> dict:
    """从 headlines 列表中按 symbols 抽取 mention 统计。

    headlines: list of {title, summary, source, published_ts, ...}
    symbols: 已知 symbol 列表(BASE,例如 'BTC' / 'OP' / 'STX')

    返回:
      {
        symbol: {
          'count': int,
          'confidence': float,         # 该 symbol 所有命中的最高置信度
          'match_rules': [rule, ...],  # 去重列表
          'headlines': [title, ...],   # 兼容旧接口,最多 3 条
          'headlines_meta': [
              {'title','source','published_ts','freshness_sec',
               'confidence','match_rule'}, ...  # 最多 5 条
          ],
        }
      }
    """
    if now_ts is None:
        now_ts = time.time()
    sym_list = [s.upper() for s in symbols]
    mentions: dict = {}

    for article in headlines:
        text = f"{article.get('title','')} {article.get('summary','')}"
        for sym in sym_list:
            ev = match_symbol_in_text(sym, text)
            if not ev:
                continue
            entry = mentions.setdefault(sym, {
                "count": 0,
                "confidence": 0.0,
                "match_rules": [],
                "headlines": [],
                "headlines_meta": [],
            })
            entry["count"] += 1
            entry["confidence"] = max(entry["confidence"], ev["confidence"])
            if ev["match_rule"] not in entry["match_rules"]:
                entry["match_rules"].append(ev["match_rule"])
            title = article.get("title", "")
            if title and len(entry["headlines"]) < 3:
                entry["headlines"].append(title)
            if len(entry["headlines_meta"]) < 5:
                published_ts = float(article.get("published_ts") or 0)
                freshness = (
                    int(now_ts - published_ts) if published_ts > 0 else None
                )
                entry["headlines_meta"].append({
                    "title": title,
                    "source": article.get("source", ""),
                    "published_ts": published_ts,
                    "freshness_sec": freshness,
                    "confidence": ev["confidence"],
                    "match_rule": ev["match_rule"],
                })

    # 按 count 降序
    return dict(sorted(mentions.items(),
                       key=lambda x: (x[1]["count"], x[1]["confidence"]),
                       reverse=True))


def filter_relevant_headlines(
    headlines: List[dict],
    base: str,
    *,
    now_ts: Optional[float] = None,
) -> List[dict]:
    """为给定 base 过滤相关 headline,返回带 freshness/confidence/match_rule 的副本。

    供 MultiDataCollector 在 news_snapshot.symbol_news 里直接挂。
    """
    if now_ts is None:
        now_ts = time.time()
    base_u = base.upper()
    out: List[dict] = []
    for h in headlines:
        text = f"{h.get('title','')} {h.get('summary','')}"
        ev = match_symbol_in_text(base_u, text)
        if not ev:
            continue
        published_ts = float(h.get("published_ts") or 0)
        freshness = int(now_ts - published_ts) if published_ts > 0 else None
        item = dict(h)
        item.update({
            "match_rule": ev["match_rule"],
            "confidence": ev["confidence"],
            "freshness_sec": freshness,
        })
        out.append(item)
    return out
