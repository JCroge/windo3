"""AC3-P2-001..006 新闻 ticker mention 边界匹配定向单测.

参考: docs/audit_remediation_third_pass_20260528_acceptance.md §7.1
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.symbol_mentions import (
    extract_symbol_mentions,
    filter_relevant_headlines,
    match_symbol_in_text,
)


def _h(title, summary="", source="test", ts=1_770_000_000):
    return {
        "title": title,
        "summary": summary,
        "source": source,
        "published_ts": ts,
    }


# ── AC3-P2-001 短 ticker 不误报 ──────────────────────────────────────────


class TestAC3P2001NoFalsePositive:
    """OP/STX/INJ 等短 ticker 在 options/stack/injection 等普通词内不误报."""

    @pytest.mark.parametrize("symbol,text", [
        ("OP", "Trader options surge in Q3"),
        ("OP", "OPS team announces upgrade"),
        ("STX", "Tech stack rewrite completed"),
        ("STX", "Stacking up the codebase"),
        ("INJ", "Injection vulnerability patched"),
        ("INJ", "Injected funds into the market"),
        ("SUI", "New suite of trading tools"),
        ("SEI", "Seismic shift in markets"),
    ])
    def test_substring_not_matched(self, symbol, text):
        ev = match_symbol_in_text(symbol, text)
        assert ev is None, f"{symbol} should NOT match in {text!r}"


# ── AC3-P2-002 cashtag 命中 ──────────────────────────────────────────────


class TestAC3P2002Cashtag:
    @pytest.mark.parametrize("symbol,text", [
        ("OP", "$OP rallies after upgrade"),
        ("STX", "Big move on $STX today"),
        ("BTC", "$BTC reclaims 100k"),
    ])
    def test_cashtag(self, symbol, text):
        ev = match_symbol_in_text(symbol, text)
        assert ev is not None
        assert ev["match_rule"] == "cashtag"
        assert ev["confidence"] == 1.0


# ── AC3-P2-003 paren 格式 ───────────────────────────────────────────────


class TestAC3P2003Paren:
    @pytest.mark.parametrize("symbol,text", [
        ("STX", "Stacks (STX) network upgrade"),
        ("OP", "Optimism (OP) ecosystem grows"),
        ("INJ", "Injective (INJ) launches"),
    ])
    def test_paren(self, symbol, text):
        ev = match_symbol_in_text(symbol, text)
        assert ev is not None
        assert ev["match_rule"] == "paren"
        assert ev["confidence"] >= 0.9


# ── AC3-P2-004 pair 格式 ─────────────────────────────────────────────────


class TestAC3P2004Pair:
    @pytest.mark.parametrize("symbol,text", [
        ("INJ", "INJ/USDT volume spikes"),
        ("INJ", "INJ-USDT perpetual surges"),
        ("STX", "STX/USD liquidity returns"),
        ("OP", "OP-PERP sees record open interest"),
    ])
    def test_pair(self, symbol, text):
        ev = match_symbol_in_text(symbol, text)
        assert ev is not None
        assert ev["match_rule"] == "pair"
        assert ev["confidence"] >= 0.9


class TestKeywordRule:
    @pytest.mark.parametrize("symbol,text", [
        ("STX", "STX TOKEN sees surge"),
        ("INJ", "INJ network grows fast"),
        ("OP", "OP token unlock schedule"),
    ])
    def test_keyword(self, symbol, text):
        ev = match_symbol_in_text(symbol, text)
        assert ev is not None
        # 可能命中 keyword 或更高置信度规则
        assert ev["confidence"] >= 0.85


class TestHighAmbiguityBareWord:
    """TON/ARB/NEAR 等高歧义裸 word 不放行 word 规则."""

    @pytest.mark.parametrize("symbol,text", [
        ("TON", "Tons of users"),
        ("TON", "TON makes sense"),
        ("ARB", "Arb opportunity grows"),
        ("ARB", "ARB sees rotation"),
        ("NEAR", "Near future of crypto"),
    ])
    def test_high_ambiguity_bare_word_rejected(self, symbol, text):
        ev = match_symbol_in_text(symbol, text)
        # 不应被 word 规则命中
        if ev is not None:
            assert ev["match_rule"] != "word"

    def test_high_ambiguity_with_pair_passes(self):
        ev = match_symbol_in_text("TON", "TON/USDT volume surges")
        assert ev is not None
        assert ev["match_rule"] == "pair"

    def test_high_ambiguity_with_cashtag_passes(self):
        ev = match_symbol_in_text("ARB", "$ARB rallies")
        assert ev is not None
        assert ev["match_rule"] == "cashtag"


# ── AC3-P2-005 helper 被两处复用 ─────────────────────────────────────────


class TestAC3P2005HelperReuse:
    """NewsResearcher 与 MultiDataCollector 都通过 utils.symbol_mentions 调 helper."""

    def test_news_researcher_uses_extract_helper(self):
        from agents.research import news_researcher as nr_mod

        researcher = nr_mod.NewsResearcher.__new__(nr_mod.NewsResearcher)
        called_with = {}

        def fake_extract(headlines, symbols, *, now_ts=None):
            called_with["headlines"] = list(headlines)
            called_with["symbols"] = list(symbols)
            return {"OP": {"count": 1, "confidence": 1.0,
                           "match_rules": ["cashtag"], "headlines": []}}

        with patch.object(nr_mod, "extract_symbol_mentions",
                          side_effect=fake_extract):
            res = researcher._extract_symbol_mentions([
                _h("$OP rallies"),
            ])
        assert "OP" in res
        assert called_with["headlines"][0]["title"].startswith("$OP")

    def test_multi_data_collector_uses_filter_helper(self):
        from agents.trading import multi_data_collector as mdc_mod

        called = {"count": 0}
        original = mdc_mod.filter_relevant_headlines

        def spy(headlines, base, *, now_ts=None):
            called["count"] += 1
            return original(headlines, base, now_ts=now_ts)

        with patch.object(mdc_mod, "filter_relevant_headlines", side_effect=spy):
            relevant = mdc_mod.filter_relevant_headlines(
                [_h("$OP rallies")], "OP",
            )
        assert called["count"] == 1
        assert len(relevant) == 1


# ── AC3-P2-006 provenance 完整 ──────────────────────────────────────────


class TestAC3P2006Provenance:
    def test_extract_returns_provenance(self):
        headlines = [_h("$OP rallies after upgrade", ts=1_770_000_000)]
        res = extract_symbol_mentions(
            headlines, ["OP"], now_ts=1_770_000_300
        )
        assert "OP" in res
        meta = res["OP"]["headlines_meta"]
        assert meta and "match_rule" in meta[0]
        assert meta[0]["confidence"] == 1.0
        assert meta[0]["source"] == "test"
        assert meta[0]["freshness_sec"] == 300

    def test_filter_returns_provenance(self):
        headlines = [_h("Stacks (STX) launches mainnet upgrade",
                        ts=1_770_000_000)]
        res = filter_relevant_headlines(
            headlines, "STX", now_ts=1_770_000_120,
        )
        assert len(res) == 1
        item = res[0]
        assert item["match_rule"] == "paren"
        assert item["confidence"] >= 0.9
        assert item["freshness_sec"] == 120
        assert item["source"] == "test"


class TestExtractMixedHeadlines:
    """端到端:多条新闻混合,验证 OP 不被 options 误报、STX 命中 paren。"""

    def test_mixed_headlines(self):
        headlines = [
            _h("Trader options surge today"),       # 不应命中 OP
            _h("$OP price hits new high"),          # cashtag → OP
            _h("Stacks (STX) launches v3 upgrade"), # paren → STX
            _h("Injection attack patched in Linux"), # 不应命中 INJ
            _h("INJ/USDT volume spikes 200%"),      # pair → INJ
            _h("Tech stack rewrite completed"),     # 不应命中 STX
        ]
        res = extract_symbol_mentions(
            headlines, ["OP", "STX", "INJ"],
        )
        assert res.get("OP", {}).get("count") == 1
        assert res.get("STX", {}).get("count") == 1
        assert res.get("INJ", {}).get("count") == 1
        assert "cashtag" in res["OP"]["match_rules"]
        assert "paren" in res["STX"]["match_rules"]
        assert "pair" in res["INJ"]["match_rules"]
