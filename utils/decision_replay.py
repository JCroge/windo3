"""确定性决策回放 harness：用真实 MultiJudge 代码重放历史决策。

observability-only —— 严禁交易决策路径 import/调用本模块。

设计：
- 用 `MultiJudge.__new__` 绕过 `__init__`（避免真实 LLM/exchange 构造）。
- `restore_state` 从快照还原决策真正读取的实例状态。
- `replay_decision` 还原状态 + stub 3 个外部 await
  (`_update_balance` / `_ask_llm` / `publish`) + mock `time.time`，
  然后跑真实的 `_make_decision`，捕获发布的 `trade_decision` payload。

与决策/风控路径完全隔离：本模块只在事后回放/观测时被调用。
"""
from unittest import mock

from utils.archetype_cooldown import ArchetypeCooldown


class _RegimeStub:
    """RegimeManager 的只读替身，从快照还原。

    决策路径读取 `.snapshot()`、`._effective_regime`、`._raw_regime`、
    `._confidence`，并调用 `is_short_allowed` / `is_probe_short_eligible`。
    回放时不重算 regime，只回放当时的 effective_regime 快照。
    """

    def __init__(self, snap):
        self._snap = dict(snap or {})
        self._effective_regime = self._snap.get("effective_regime")
        self._raw_regime = self._snap.get("raw_regime", self._effective_regime)
        self._confidence = self._snap.get("confidence", 0)
        self._candidate_regime = self._snap.get("candidate_regime")
        self._candidate_count = self._snap.get("candidate_count", 0)
        self._last_changed_at = self._snap.get("last_changed_at", 0.0)
        self._min_hold_sec = self._snap.get("min_hold_sec", 0)
        self._basis = self._snap.get("basis", {})

    def snapshot(self):
        snap = dict(self._snap)
        snap.setdefault("effective_regime", self._effective_regime)
        snap.setdefault("raw_regime", self._raw_regime)
        snap.setdefault("confidence", self._confidence)
        return snap

    def is_short_allowed(self, score, htf_bearish_votes, effective_rr,
                         confirm_15m_short, daily_bias):
        # 回放当时 regime：非 bullish 一律放行，bullish 走与生产同构的强空判定。
        if self._effective_regime != "bullish":
            return True, ""
        if (score <= -70 and htf_bearish_votes >= 2
                and confirm_15m_short and effective_rr >= 1.8):
            if daily_bias == "bullish":
                return True, "degrade_to_probe"
            return True, "short_bullish_strong"
        return False, "short_regime_guard"

    def is_probe_short_eligible(self, *a, **k):
        return False


def restore_state(judge, snap, symbol=None):
    """从决策前快照还原 MultiJudge 实例状态（决策路径读取的字段子集）。"""
    judge._open_positions = set(snap.get("_open_positions", []))
    judge._pending_open_symbols = set(snap.get("_pending_open_symbols", []))
    judge._position_slots = dict(snap.get("_position_slots", {}))
    judge._pending_open_slots = dict(snap.get("_pending_open_slots", {}))

    ac_snap = snap.get("_archetype_cooldown") or {"_history": {}, "_cooldown_until": {}}
    ac = ArchetypeCooldown(enabled=True, logger=None)
    ac._history = dict(ac_snap.get("_history", {}))
    ac._cooldown_until = dict(ac_snap.get("_cooldown_until", {}))
    judge._archetype_cooldown = ac

    judge._recent_wins = snap.get("_recent_wins", 0)
    judge._total_completed_trades = snap.get("_total_completed_trades", 0)
    judge._recent_win_rate = snap.get("_recent_win_rate")
    judge._probe_short_active = snap.get("_probe_short_active")
    judge._probe_short_sl_count = snap.get("_probe_short_sl_count", 0)
    judge._probe_short_cooldown_until = snap.get("_probe_short_cooldown_until", 0.0)

    sym_state = snap.get("_symbol_state") or {}
    judge._symbol_state = {symbol: dict(sym_state)} if (symbol and sym_state) else {}
    judge._available_balance = snap.get("_available_balance", 0.0)
    judge._regime_manager = _RegimeStub(snap.get("_regime_manager"))


async def replay_decision(record, config=None):
    """还原状态 + stub 3 个外部 await + 跑真实 _make_decision，捕获发布的决策。

    返回捕获到的 `trade_decision` payload（dict）；若 record 不可回放则返回 None。
    """
    from agents.trading.judge import MultiJudge

    if not record.get("replayable") or not record.get("state_snapshot_before_decision"):
        return None
    symbol = record["symbol"]
    snap = record["state_snapshot_before_decision"]

    judge = MultiJudge.__new__(MultiJudge)
    judge.config = config or {}
    judge.logger = mock.MagicMock()
    _install_config_flags(judge, config or {})
    restore_state(judge, snap, symbol=symbol)

    captured = []

    async def _capture_publish(msg_type, payload, to="broadcast", symbol=None):
        if msg_type == "trade_decision":
            captured.append(payload)

    async def _noop_balance():
        return None

    async def _inject_llm(sym, tech, score):
        return record.get("llm_output_inline") or {
            "action": "hold", "confidence": 0, "reasoning": "",
            "key_factors": [], "risk_warnings": [],
        }

    judge.publish = _capture_publish
    judge._update_balance = _noop_balance
    judge._ask_llm = _inject_llm

    ts = record["timestamp"]
    with mock.patch("time.time", return_value=ts):
        await judge._make_decision(symbol, record["tech_analysis"])

    return captured[0] if captured else None


def _install_config_flags(judge, config):
    """白名单还原 _make_decision 读取的配置开关/阈值与若干 plain init state。

    缺啥补啥：跑回放时 _make_decision 抛 AttributeError 就在这里补上对应字段，
    默认值与 MultiJudge.__init__ 保持一致。严禁改 judge.py 决策逻辑。
    """
    g = config.get

    # ── 决策冷却 / 仓位上限 ──
    judge._decision_cooldown = g("decision_cooldown", 55)
    judge._force_close_cooldown = g("force_close_cooldown", 300)
    judge._max_trade_amount = g("max_trade_amount", 10)
    judge._max_concurrent_positions = g("max_concurrent_positions", 3)
    judge._pending_ttl = g("pending_ttl", 120)
    judge._pending_open_ts = {}

    # ── 余额 / 逻辑账户 ──
    judge._effective_balance_cap = g("effective_balance_cap", None)
    judge._balance_adapter = None

    # ── EV 门 ──
    judge._min_trades_for_ev_gate = g("min_trades_for_ev_gate", 10)
    judge._fallback_win_rate = g("fallback_win_rate", 0.52)
    judge._ev_min_threshold = g("ev_min_threshold", 0.05)
    judge._ev_prior_wins = g("ev_prior_wins", 2)
    judge._ev_prior_total = g("ev_prior_total", 5)
    judge._ev_strong_signal_threshold = g("ev_strong_signal_threshold", 70)
    judge._recent_profit_factor = None
    judge._ev_bucket_min_trades = g("ev_bucket_min_trades", 10)
    judge._ev_bucket_sparse_allow_uplift = g("ev_bucket_sparse_allow_uplift", False)

    # ── 信号门槛 ──
    judge._min_confidence = g("min_confidence", 60)
    judge._min_deferred_signal_score = g("min_deferred_signal_score", 45)
    judge._min_liquidity_score_for_weak_signal = g("min_liquidity_score_for_weak_signal", 1)

    # ── 15m 入场时机 ──
    judge._15m_enabled = g("entry_timing_15m_enabled", True)
    judge._15m_required = g("entry_timing_15m_required", True)
    judge._15m_neutral_allows_strong = g("entry_timing_15m_neutral_allows_strong_signal", True)
    judge._15m_strong_score_threshold = g("entry_timing_15m_strong_score_threshold", 70)
    judge._15m_defer_on_block = g("entry_timing_15m_defer_on_block", True)
    judge._15m_timeout_hours = g("entry_timing_15m_timeout_hours", 4)

    # ── R:R floor ──
    judge._short_regime_guard_enabled = g("short_regime_guard_enabled", True)
    judge._probe_short_enabled = g("probe_short_enabled", True)
    judge._low_rr_slot_enabled = g("low_rr_slot_enabled", True)
    judge._rr_floor_default = g("rr_floor_default", 1.50)
    judge._rr_floor_long_bullish = g("rr_floor_long_bullish", 1.30)
    judge._rr_floor_long_aligned_choppy = g("rr_floor_long_aligned_choppy", 1.30)
    judge._rr_floor_short_bullish = g("rr_floor_short_bullish", 1.80)
    judge._probe_rr_floor = g("probe_rr_floor", 1.30)
    judge._low_rr_long_aligned_enabled = g("low_rr_long_aligned_enabled", True)
    judge._low_rr_max_leverage = g("low_rr_max_leverage", 5)
    judge._low_rr_max_position_pct = g("low_rr_max_position_pct", 0.5)

    # ── probe_short ──
    judge._probe_short_max_position_pct = g("probe_short_max_position_pct", 0.3)
    judge._probe_short_max_leverage = g("probe_short_max_leverage", 3)
    judge._probe_short_max_concurrent = g("probe_short_max_concurrent", 1)
    judge._probe_short_cooldown_hours = g("probe_short_cooldown_hours", 24)

    # ── short side guard live thresholds ──
    judge._short_live_min_score = g("short_live_min_score", 55)
    judge._short_live_min_rsi = g("short_live_min_rsi", 40)
    judge._short_live_min_range_pos = g("short_live_min_range_pos", 0.45)
    judge._short_live_require_daily_bearish = g("short_live_require_daily_bearish", True)
    judge._short_live_min_htf_votes = g("short_live_min_htf_votes", 2)
    judge._short_live_max_pre_move = g("short_live_max_pre_move", -0.01)

    # ── long entry position guard ──
    judge._long_live_position_guard_enabled = g("long_live_position_guard_enabled", True)
    judge._long_live_max_range_pos = g("long_live_max_range_pos", 0.82)
    judge._long_live_max_pre_move = g("long_live_max_pre_move", 0.05)
    judge._long_live_max_daily_gain = g("long_live_max_daily_gain", 0.10)
    judge._long_live_daily_gain_range_pos = g("long_live_daily_gain_range_pos", 0.75)
    judge._long_live_pullback_min_pct = g("long_live_pullback_min_pct", 0.025)
    judge._long_live_pullback_timeout_hours = g("long_live_pullback_timeout_hours", 4)
    judge._long_live_overheat_disable_chase = g("long_live_overheat_disable_chase", True)

    # ── Phase 2 feature flags ──
    judge._confidence_split_enabled = g("phase2_signal_confidence_split_enabled", False)
    judge._momentum_probe_long_enabled = g("phase2_momentum_probe_long_enabled", False)
    judge._trend_saturation_enabled = g("phase2_trend_saturation_enabled", False)
    judge._bucketed_ev_enabled = g("phase2_bucketed_ev_enabled", False)
    judge._request_id_enabled = True
    judge._probe_long_max_concurrent = 1
    judge._probe_long_max_position_pct = 0.3
    judge._probe_long_max_leverage = 3
    judge._probe_long_rsi_min = 70
    judge._probe_long_rsi_max = 85
    judge._bucketed_metrics = {}

    # ── LLM degraded 计数 ──
    judge._llm_consecutive_failures = 0
    judge._llm_degraded_alerted = False

    # ── pnl_resolved 去重 ──
    judge._processed_resolution_ids = set()
    judge._processed_resolution_max = 1024

    # ── 缓存 / 杂项 plain init state ──
    judge._symbol_tech_cache = {}
    judge._news_snapshot = {}
    judge._state_dirty = False
    judge.exchange = None
    # 决策磁带：回放期间禁写（observability-only），避免污染真实 tape
    judge._decision_tape = None
