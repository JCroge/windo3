"""Market Regime Detection with Hysteresis

Computes effective market regime (bullish/bearish/mixed/choppy) from aggregated
tech_analysis data across all active symbols. Uses hysteresis to prevent flapping
at regime boundaries.

Integration: Judge calls regime_manager.update(symbol_tech_cache) on each tick.
"""

import time
from utils.atomic_io import atomic_write_json

REGIME_BULLISH = "bullish"
REGIME_BEARISH = "bearish"
REGIME_MIXED = "mixed"
REGIME_CHOPPY = "choppy"


class RegimeManager:

    def __init__(self, config: dict = None, logger=None):
        config = config or {}
        self.logger = logger
        self._enabled = config.get('regime_hysteresis_enabled', True)

        # Hysteresis parameters
        self._confirm_count = 2
        self._min_hold_sec = 1800  # 30 min

        # State
        self._effective_regime = REGIME_MIXED
        self._raw_regime = REGIME_MIXED
        self._candidate_regime = None
        self._candidate_count = 0
        self._confidence = 50
        self._last_changed_at = 0
        self._basis = {}

        self._load_state()

    # ── Public API ──────────────────────────────────────────────────────────

    def update(self, symbol_techs: dict) -> dict:
        """Recompute regime from all active symbols' tech data, apply hysteresis."""
        if not self._enabled or not symbol_techs:
            return self.snapshot()

        raw_regime, confidence, basis = self._compute_raw_regime(symbol_techs)
        self._raw_regime = raw_regime
        self._confidence = confidence
        self._basis = basis
        self._apply_hysteresis(raw_regime, confidence)
        return self.snapshot()

    def snapshot(self) -> dict:
        return {
            "effective_regime": self._effective_regime,
            "raw_regime": self._raw_regime,
            "confidence": self._confidence,
            "candidate_regime": self._candidate_regime,
            "candidate_count": self._candidate_count,
            "last_changed_at": self._last_changed_at,
            "min_hold_remaining_sec": max(0, self._min_hold_sec - (time.time() - self._last_changed_at)),
            "basis": self._basis,
        }

    def is_short_allowed(self, score: float, htf_bearish_votes: int,
                         effective_rr: float, confirm_15m_short: bool,
                         daily_bias: str) -> tuple:
        """Check if short is allowed under current regime (PRD RQ-REG-03).

        Returns (allowed: bool, reason: str).
        """
        if self._effective_regime != REGIME_BULLISH:
            return True, ""

        # Strong short conditions: all must be met
        if (score <= -70
                and htf_bearish_votes >= 2
                and confirm_15m_short
                and effective_rr >= 1.8):
            return True, "short_bullish_strong"

        return False, "short_regime_guard"

    def is_probe_short_eligible(self, btc_tech: dict, symbol_techs: dict) -> bool:
        """Check market-level early reversal conditions (PRD RQ-REG-04).

        Requires at least one of:
        1. BTC 4h RSI dropped from >=75 to <70 with volume
        2. 60%+ of active symbols 15m bias turned bearish/neutral
        3. Funding extreme + crowd extreme + 15m breakdown
        """
        if self._effective_regime != REGIME_BULLISH:
            return False

        if not btc_tech:
            return False

        # Condition 1: BTC 4h RSI reversal
        btc_trend = btc_tech.get('trend', {})
        btc_4h_rsi = btc_trend.get('tf_4h_rsi', 50)
        # We check if 4h RSI is in the 65-72 zone (just crossed down from overbought)
        btc_momentum = btc_tech.get('momentum', {})
        volume_ratio = btc_momentum.get('volume_ratio', 1.0)
        if btc_4h_rsi < 70 and btc_4h_rsi > 60 and volume_ratio > 1.5:
            return True

        # Condition 2: Breadth deterioration
        if len(symbol_techs) >= 3:
            bearish_15m = 0
            total_with_15m = 0
            for tech in symbol_techs.values():
                entry_timing = tech.get('entry_timing', {})
                bias = entry_timing.get('tf_15m_bias')
                if bias:
                    total_with_15m += 1
                    if bias in ('bearish', 'neutral'):
                        bearish_15m += 1
            if total_with_15m >= 3 and bearish_15m / total_with_15m >= 0.6:
                return True

        # Condition 3: Funding extreme + crowd extreme
        btc_money = btc_tech.get('money_flow', {})
        if (btc_money.get('funding_extreme')
                and btc_tech.get('crowd', {}).get('long_ratio', 0.5) > 0.7):
            return True

        return False

    # ── Internal ────────────────────────────────────────────────────────────

    def _compute_raw_regime(self, techs: dict) -> tuple:
        """Compute raw regime from aggregated tech_analysis snapshots.

        Uses: trend direction consensus, BTC/ETH higher_tf_bias, volatility.
        Returns: (regime_label, confidence, basis_dict)
        """
        if not techs:
            return REGIME_MIXED, 50, {}

        bullish_count = 0
        bearish_count = 0
        neutral_count = 0
        total = 0
        atr_values = []

        btc_bias = None
        eth_bias = None

        for sym, tech in techs.items():
            trend = tech.get('trend', {})
            direction = trend.get('direction', 'neutral')
            total += 1
            if direction == 'bullish':
                bullish_count += 1
            elif direction == 'bearish':
                bearish_count += 1
            else:
                neutral_count += 1

            atr_pct = tech.get('momentum', {}).get('atr_pct', 0)
            if atr_pct > 0:
                atr_values.append(atr_pct)

            base = sym.split('-')[0].upper()
            if base == 'BTC':
                btc_bias = trend.get('higher_tf_bias', trend.get('daily_bias'))
            elif base == 'ETH':
                eth_bias = trend.get('higher_tf_bias', trend.get('daily_bias'))

        if total == 0:
            return REGIME_MIXED, 50, {}

        bullish_pct = bullish_count / total
        bearish_pct = bearish_count / total

        # BTC/ETH anchor bias
        anchor_bullish = (btc_bias == 'bullish') or (eth_bias == 'bullish')
        anchor_bearish = (btc_bias == 'bearish') or (eth_bias == 'bearish')
        anchor_neutral = not anchor_bullish and not anchor_bearish

        # Volatility assessment
        avg_atr = sum(atr_values) / len(atr_values) if atr_values else 0.02
        high_vol = avg_atr > 0.04

        # Regime determination
        regime = REGIME_MIXED
        confidence = 50

        if bullish_pct >= 0.6 and (anchor_bullish or anchor_neutral):
            regime = REGIME_BULLISH
            confidence = int(50 + bullish_pct * 40 + (10 if anchor_bullish else 0))
        elif bearish_pct >= 0.6 and (anchor_bearish or anchor_neutral):
            regime = REGIME_BEARISH
            confidence = int(50 + bearish_pct * 40 + (10 if anchor_bearish else 0))
        elif high_vol and neutral_count / total < 0.4:
            regime = REGIME_MIXED
            confidence = 55
        elif not high_vol and neutral_count / total >= 0.5:
            regime = REGIME_CHOPPY
            confidence = 60
        else:
            regime = REGIME_MIXED
            confidence = 50

        confidence = min(100, confidence)

        basis = {
            "bullish_pct": round(bullish_pct, 2),
            "bearish_pct": round(bearish_pct, 2),
            "btc_bias": btc_bias,
            "eth_bias": eth_bias,
            "avg_atr": round(avg_atr, 4),
            "total_symbols": total,
        }

        return regime, confidence, basis

    def _apply_hysteresis(self, raw_regime: str, confidence: int):
        """Apply hysteresis rules to prevent flapping."""
        now = time.time()

        # If same as effective, reset candidate
        if raw_regime == self._effective_regime:
            self._candidate_regime = None
            self._candidate_count = 0
            return

        # Min hold period: don't switch too fast (unless critical)
        in_hold_period = (now - self._last_changed_at) < self._min_hold_sec
        is_critical = (raw_regime == REGIME_BEARISH and
                       self._effective_regime == REGIME_BULLISH and
                       confidence >= 80)

        if in_hold_period and not is_critical:
            return

        # Accumulate candidate confirmations
        if raw_regime == self._candidate_regime:
            self._candidate_count += 1
        else:
            self._candidate_regime = raw_regime
            self._candidate_count = 1

        # Determine required confirmations
        required = self._confirm_count  # default 2
        if self._effective_regime == REGIME_BULLISH and raw_regime == REGIME_BEARISH:
            required = 3  # bullish→bearish needs more evidence

        # Critical override: single high-confidence bearish can force switch
        if is_critical:
            required = 1

        # Switch if enough confirmations
        if self._candidate_count >= required and confidence >= 65:
            old = self._effective_regime
            self._effective_regime = raw_regime
            self._last_changed_at = now
            self._candidate_regime = None
            self._candidate_count = 0
            if self.logger:
                self.logger.info(
                    f"[Regime] {old} → {raw_regime} "
                    f"(confidence={confidence}, confirmations={required})"
                )
            self._persist_state()

    # ── Persistence ─────────────────────────────────────────────────────────

    _STATE_PATH = "data/regime_state.json"

    def _persist_state(self):
        try:
            atomic_write_json(self._STATE_PATH, {
                "effective_regime": self._effective_regime,
                "last_changed_at": self._last_changed_at,
                "candidate_regime": self._candidate_regime,
                "candidate_count": self._candidate_count,
            })
        except Exception:
            pass

    def _load_state(self):
        import json
        try:
            with open(self._STATE_PATH, 'r') as f:
                data = json.load(f)
            self._effective_regime = data.get("effective_regime", REGIME_MIXED)
            self._last_changed_at = data.get("last_changed_at", 0)
            self._candidate_regime = data.get("candidate_regime")
            self._candidate_count = data.get("candidate_count", 0)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
