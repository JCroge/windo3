"""Data-source provenance: source / freshness_sec / confidence for market dimensions.

Single source of truth for confidence scoring. Mirrors utils/symbol_mentions.py.
Observability metadata only — never gates trading decisions.
"""
from typing import Optional

CROSS_EXCHANGE_FACTOR = 0.7


def _venue_of(source: str) -> str:
    """Map a source feed id to its venue (e.g. binance_fapi -> binance)."""
    if not source:
        return "unknown"
    return source.split("_", 1)[0]


def derive_confidence(source: str, freshness_sec: float, *,
                      native_venue: str = "okx",
                      period_sec: Optional[float] = None,
                      degraded: bool = False) -> float:
    """0.0-1.0. Linear freshness decay to 0 at 2x the sampling period, times a
    cross-exchange penalty; floored to 0 when degraded."""
    if degraded:
        return 0.0
    if period_sec and period_sec > 0:
        freshness_factor = 1.0 - (freshness_sec / (period_sec * 2.0))
    else:
        freshness_factor = 1.0
    freshness_factor = max(0.0, min(1.0, freshness_factor))
    source_factor = 1.0 if _venue_of(source) == native_venue else CROSS_EXCHANGE_FACTOR
    return round(freshness_factor * source_factor, 4)


def provenance_entry(source: str, item_ts_ms: Optional[float], now: float, *,
                     period_sec: Optional[float] = None,
                     native_venue: str = "okx",
                     fetch_ts: Optional[float] = None,
                     degraded: bool = False) -> dict:
    """Build {source, freshness_sec, confidence}. freshness from the datum
    timestamp when available, else time-since-fetch, else 0."""
    if item_ts_ms is not None:
        freshness_sec = max(0.0, now - (float(item_ts_ms) / 1000.0))
    elif fetch_ts is not None:
        freshness_sec = max(0.0, now - float(fetch_ts))
    else:
        freshness_sec = 0.0
    return {
        "source": source,
        "freshness_sec": round(freshness_sec, 1),
        "confidence": derive_confidence(source, freshness_sec,
                                        native_venue=native_venue,
                                        period_sec=period_sec, degraded=degraded),
    }
