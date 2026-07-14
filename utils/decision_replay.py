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
from utils.config_loader import DEFAULTS as _PROD_DEFAULTS


def _restore_regime(snap, config=None):
    """还原一个【真实】RegimeManager（不重写其逻辑），从快照灌入内部状态。

    L2 核心：回放复用真实代码，不要第二份实现。决策路径调用的
    `snapshot()` / `is_short_allowed()` / `is_probe_short_eligible()` 都只读
    `self._effective_regime` 等内部字段（+ 入参），所以还原这些字段后真实方法即可忠实运行。
    构造后覆盖 7 个内部字段，使其与 `_load_state()` 读到的磁盘状态无关。
    """
    from utils.market_regime import RegimeManager
    snap = dict(snap or {})
    rm = RegimeManager(config=config or {}, logger=None)
    rm._effective_regime = snap.get("effective_regime", rm._effective_regime)
    rm._raw_regime = snap.get("raw_regime", rm._effective_regime)
    rm._confidence = snap.get("confidence", rm._confidence)
    rm._candidate_regime = snap.get("candidate_regime")
    rm._candidate_count = snap.get("candidate_count", 0)
    rm._last_changed_at = snap.get("last_changed_at", 0.0)
    rm._basis = snap.get("basis", {})
    return rm


def restore_state(judge, snap, symbol=None):
    """从决策前快照还原 MultiJudge 实例状态（决策路径读取的字段子集）。"""
    judge._open_positions = set(snap.get("_open_positions", []))
    judge._pending_open_symbols = set(snap.get("_pending_open_symbols", []))
    # _pending_open_ts 是 state（非 config）：还原而非清空，使 _sweep_stale_pending 忠实
    judge._pending_open_ts = dict(snap.get("_pending_open_ts", {}))
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
    judge._regime_manager = _restore_regime(snap.get("_regime_manager"),
                                            config=getattr(judge, "config", {}))


def production_base_config():
    """live 生产决策 config 基线(config_loader 生产默认)。

    回放/CF-sim baseline 须以此为基线而非空 config —— 空 config 会让
    _install_config_flags 把 Phase-2 等 flag 默认到与生产相反的值，致
    confidence/gate 路径系统性发散(baseline_fidelity 虚低)。observability-only:
    只读 config_loader 静态默认，不读任何 live 运行态。
    """
    return dict(_PROD_DEFAULTS)


# 键 → "该键加入 DEFAULTS 之前的纪元默认"。缺该键的旧记录回放用此值，而非当前
# production 默认（其默认可能已翻转，致系统性发散）。
# forward-only 契约：新增"DEFAULTS 默认值发生翻转"的键时，在此登记其翻转前的纪元默认。
# 注：值与当前 DEFAULTS 相同的条目是防御性 no-op（当前默认恰等于纪元默认），
#     保留它们仅为让守卫测试把这些 snapshot-缺键显式归类（见 _GATE_IRRELEVANT 对照）。
_EPOCH_FALLBACK = {
    "ladder_rr_enabled": False,          # 真翻转：trend-entry-levers-default-on 把 DEFAULTS 翻成 True，纪元前=关
    "ev_winrate_gate_enabled": True,     # 防御性 no-op：当前 DEFAULTS 仍=True（仅 config.yaml live 值改过）
    "ev_neutral_p_win": 0.55,            # 防御性 no-op：当前 DEFAULTS 仍=0.55
    "long_live_regime_aware_range_enabled": False,  # 真翻转：regime-aware-long-entry-guard 新增，纪元前无体制感知=固定0.82，replay 用 off 还原旧判定
    "long_live_max_range_pos_choppy": 0.55,         # 防御性 no-op：仅 enabled=True 生效，旧纪元 enabled=False 不影响判定
    "long_live_daily_gain_range_pos_choppy": 0.50,  # 防御性 no-op：同上
    "llm_rsi_reversal_veto_enabled": False,          # 真翻转：restore-llm-rsi-veto-power 新增，纪元前无反转否决=off，replay 用 off 还原旧判定
    "reversal_veto_min_llm_confidence": 0,           # 防御性 no-op：子门，veto off 时无效，0=不启用
    "pseudo_resonance_downweight_enabled": False,    # 真翻转：pseudo-resonance-downweight 新增，纪元前无伪共振降权=off
    "ma_bloc_cap": 50,                               # 防御性 no-op：仅 downweight enabled=True 生效，旧纪元 enabled=False 不影响判定
    "regime_flat_gate_enabled": False,               # 真翻转：choppy-flat-gate 新增，纪元前无 flat-gate=off，replay 忠实还原旧判定（同 ladder_rr_enabled 模式）
    "main_quality_gate_enabled": False,              # 真翻转：tactical track 引入后才有 main quality gate；旧纪元无该 gate
    "main_quality_min_provenance": 0.20,             # 防御性 no-op：仅 gate enabled=True 生效，旧纪元 gate=False
    "main_quality_block_llm_reversal": True,         # 防御性 no-op：仅 gate enabled=True 生效
    "main_quality_allow_mixed_override": False,      # 防御性 no-op：仅 gate enabled=True 生效
    "main_quality_require_volume_or_oi": True,       # 防御性 no-op：仅 gate enabled=True 生效
    "tactical_track_enabled": False,                 # 防御性 no-op：旧纪元无 tactical track
    "tactical_shadow_only": True,                    # 防御性 no-op：仅 tactical track enabled=True 生效
    "tactical_max_leverage": 5,                      # 防御性 no-op：仅 tactical track enabled=True 生效
    "tactical_default_position_pct": 0.70,           # 防御性 no-op：仅 tactical track enabled=True 生效
    "tactical_very_near_position_pct": 1.00,         # 防御性 no-op：仅 tactical track enabled=True 生效
    "tactical_stop_cap_r_main": 0.60,                # 防御性 no-op：仅 tactical track enabled=True 生效
    "tactical_very_near_stop_r_main": 0.40,          # 防御性 no-op：仅 tactical track enabled=True 生效
    "tactical_tp1_r": 0.60,                          # 真翻转：旧 tactical 纪元 tp1=0.60，后续 live 默认提到 1.00
    "tactical_cost_coverage_min": 4.0,               # 防御性 no-op：仅 tactical track enabled=True 生效
    "tactical_min_rr_for_track": 0.75,               # 防御性 no-op：仅 tactical track enabled=True 生效
    "tactical_min_ev_for_track": -0.04,              # 防御性 no-op：仅 tactical track enabled=True 生效
    "tactical_max_hold_minutes": 90,                 # 防御性 no-op：仅 tactical track enabled=True 生效
    "tactical_min_progress_r": 0.15,                 # 防御性 no-op：executor tactical exit 参数，不改 Judge gate
    "tactical_weakened_no_progress_min_minutes": 30, # 防御性 no-op：executor tactical exit 参数，不改 Judge gate
    "tactical_weakened_no_progress_max_minutes": 45, # 防御性 no-op：executor tactical exit 参数，不改 Judge gate
    "tactical_daily_loss_limit_usdt": -10.0,         # 防御性 no-op：仅 tactical slot circuit 生效
}

# 晚加但不影响 Judge gate 决策的键。守卫测试（CF-T2）消费此集合：snapshot-缺键
# 必须落在 _EPOCH_FALLBACK 或本集合之一，否则守卫失败，防新翻转键静默漂移。
_GATE_IRRELEVANT = {
    "rotation_close_held_enabled",       # 轮换平仓开关，不进 Judge 决策
    "position_resync_confirm_ticks",     # executor 仓位同步补录双确认 tick 数，不进 Judge 决策
    "tactical_loss_streak_pause_count",  # PortfolioRiskGuard 后验 circuit 参数，不进 Judge replay gate
    "tactical_loss_streak_pause_minutes",# PortfolioRiskGuard 后验 circuit 参数，不进 Judge replay gate
    "tactical_quality_window_trades",    # PortfolioRiskGuard 后验质量窗口，不进 Judge replay gate
    "tactical_success_window_trades",    # 上 live 样本门槛/观测指标，不进 Judge replay gate
    "tactical_success_min_win_rate",     # 上 live 样本门槛/观测指标，不进 Judge replay gate
    "tactical_success_min_profit_factor",# 上 live 样本门槛/观测指标，不进 Judge replay gate
}


def _resolve_effective_config(record, perturbation):
    """四层合并：production_base < 纪元兜底 < config_snapshot(录值优先) < 扰动override(顶层)。"""
    return {
        **production_base_config(),
        **_EPOCH_FALLBACK,
        **(record.get("config_snapshot") or {}),
        **(perturbation or {}),
    }


async def replay_decision(record, config=None):
    """还原状态 + stub 3 个外部 await + 跑真实 _make_decision，捕获发布的决策。

    返回捕获到的 `trade_decision` payload（dict）；若 record 不可回放则返回 None。
    """
    from agents.trading.judge import MultiJudge

    if not record.get("replayable") or not record.get("state_snapshot_before_decision"):
        return None
    symbol = record["symbol"]
    snap = record["state_snapshot_before_decision"]

    # 有效 config = 四层合并：production_base < 纪元兜底 < config_snapshot(录值优先) < 扰动override。
    effective = _resolve_effective_config(record, config)
    judge = MultiJudge.__new__(MultiJudge)
    judge.config = effective
    judge.logger = mock.MagicMock()
    _install_config_flags(judge, effective)
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
    # ranked accept 经延迟 task 发布；回放时不挂真实 timer，改在决策后同步驱动 flush。
    judge._schedule_rank_flush = lambda: None

    ts = record["timestamp"]
    with mock.patch("time.time", return_value=ts):
        await judge._make_decision(symbol, record["tech_analysis"])
        # 若 _make_decision 把 accept 候选入队等延迟 flush（ranking_enabled），
        # 同步驱动真实 _flush_ranked_candidates 复现开仓发布。
        if not captured and getattr(judge, "_candidate_ranker", None) is not None:
            await judge._flush_ranked_candidates()

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
    judge._ev_winrate_gate_enabled = g("ev_winrate_gate_enabled", True)
    judge._ev_neutral_p_win = g("ev_neutral_p_win", 0.55)
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

    # ── Tactical exit track ──
    judge._tactical_track_enabled = g("tactical_track_enabled", False)
    judge._tactical_shadow_only = g("tactical_shadow_only", True)
    judge._main_quality_gate_enabled = g("main_quality_gate_enabled", True)
    judge._main_quality_min_provenance = g("main_quality_min_provenance", 0.20)
    judge._main_quality_block_llm_reversal = g("main_quality_block_llm_reversal", True)
    judge._main_quality_allow_mixed_override = g("main_quality_allow_mixed_override", False)
    judge._main_quality_require_volume_or_oi = g("main_quality_require_volume_or_oi", True)
    judge._tactical_max_leverage = g("tactical_max_leverage", 5)
    judge._tactical_default_position_pct = g("tactical_default_position_pct", 0.70)
    judge._tactical_very_near_position_pct = g("tactical_very_near_position_pct", 1.00)
    judge._tactical_stop_cap_r_main = g("tactical_stop_cap_r_main", 0.60)
    judge._tactical_very_near_stop_r_main = g("tactical_very_near_stop_r_main", 0.40)
    judge._tactical_tp1_r = g("tactical_tp1_r", 0.60)
    judge._tactical_cost_coverage_min = g("tactical_cost_coverage_min", 4.0)
    judge._tactical_max_hold_minutes = g("tactical_max_hold_minutes", 90)

    # ── trend-entry-rr-fidelity 两杠杆 (默认值须与 judge.__init__ 一致) ──
    # trend-entry-levers-default-on: lever2(ladder) 默认开、lever1(path_evidence) 默认关。
    judge._path_evidence_aligned_enabled = g("path_evidence_aligned_enabled", False)
    judge._path_evidence_min_pre12h_return = g("path_evidence_min_pre12h_return", 0.03)
    judge._path_evidence_max_range_pos = g("path_evidence_max_range_pos", 0.92)
    judge._path_evidence_min_strength = g("path_evidence_min_strength", 60)
    judge._ladder_rr_enabled = g("ladder_rr_enabled", True)
    judge._regime_flat_gate_enabled = g("regime_flat_gate_enabled", True)

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

    # ── Ranking（真实 CandidateRanker，不重写）：accept 路径读 _compute_rank_score
    #    并经 add_candidate + 延迟 flush 发布，回放须用真实 ranker 才能复现开仓决策 ──
    from utils.candidate_ranker import CandidateRanker
    ranking_enabled = g("ranking_enabled", True)
    low_rr_extra = g("low_rr_extra_slot", 1)
    judge._candidate_ranker = CandidateRanker(
        max_slots=judge._max_concurrent_positions,
        enabled=ranking_enabled,
        low_rr_extra_slot=low_rr_extra if g("low_rr_slot_enabled", True) else 0,
        logger=judge.logger,
    )
    judge._rank_flush_delay = g("rank_flush_delay", 5.0)
    judge._rank_flush_task = None

    # ── 缓存 / 杂项 plain init state ──
    judge._symbol_tech_cache = {}
    judge._news_snapshot = {}
    judge._state_dirty = False
    judge.exchange = None
    # 决策磁带：回放期间禁写（observability-only），避免污染真实 tape
    judge._decision_tape = None
    # 反事实账本：reject 路径（含 EV/RR/guard 等）都会调 _record_rejected_plan，
    # 回放须用【禁用】ledger 让其早返回，不写盘也不污染真实反事实流水。
    from utils.counterfactual_ledger import CounterfactualLedger
    judge._counterfactual_ledger = CounterfactualLedger(enabled=False, logger=judge.logger)


_DISCRETE = ("action", "confidence", "dispatch_path")
_DISCRETE_ATTR = ("entry_type", "slot_type", "is_probe", "is_low_rr",
                  "short_gate_decision", "short_gate_reason", "rr_policy", "rr_floor_used",
                  "entry_position_status", "entry_position_block_reason", "blocked_by")
_CONTINUOUS = ("size_usdt", "entry_ref", "stop_loss", "leverage")
_INFORMATIONAL = ("reasoning", "key_factors", "risk_warnings")
_TOL = 0.005


def _rel_close(a, b, tol=_TOL):
    if a is None or b is None:
        return a == b
    if a == 0:
        return abs(b) <= tol
    return abs(a - b) / abs(a) <= tol


def compare_decision(recorded, replayed):
    """三层比对：离散字节级 fail / 连续 <0.5% fail / reasoning 仅信息。
    返回 {"match": bool, "diffs": [{"field","recorded","replayed"[,"informational"]}]}"""
    diffs = []
    match = True
    for f in _DISCRETE:
        if recorded.get(f) != replayed.get(f):
            diffs.append({"field": f, "recorded": recorded.get(f), "replayed": replayed.get(f)})
            match = False
    ra, pa = recorded.get("attribution") or {}, replayed.get("attribution") or {}
    for f in _DISCRETE_ATTR:
        if ra.get(f) != pa.get(f):
            diffs.append({"field": f"attribution.{f}", "recorded": ra.get(f), "replayed": pa.get(f)})
            match = False
    rp, pp = recorded.get("plan") or {}, replayed.get("plan") or {}
    for f in _CONTINUOUS:
        if not _rel_close(rp.get(f), pp.get(f)):
            diffs.append({"field": f"plan.{f}", "recorded": rp.get(f), "replayed": pp.get(f)})
            match = False
    rtp, ptp = rp.get("take_profit") or [], pp.get("take_profit") or []
    if len(rtp) != len(ptp) or any(not _rel_close(x, y) for x, y in zip(rtp, ptp)):
        diffs.append({"field": "plan.take_profit", "recorded": rtp, "replayed": ptp})
        match = False
    for f in _INFORMATIONAL:
        if recorded.get(f) != replayed.get(f):
            diffs.append({"field": f, "recorded": recorded.get(f),
                          "replayed": replayed.get(f), "informational": True})
    return {"match": match, "diffs": diffs}
