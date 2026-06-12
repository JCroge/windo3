"""统一配置加载器 — 单一真相源

优先级：env > config.yaml > 内置默认值

设计原则：
- 所有 agent / executor / risk_manager 通过 load_config() 拿配置
- 字段名统一为 python snake_case
- 数值范围校验：硬限制不允许超过安全边界
- live 模式（USE_TESTNET=false）下，关键凭证缺失则拒绝启动
"""

import os
from typing import Optional
from dotenv import load_dotenv

try:
    import yaml
except ImportError:
    yaml = None


# 关键硬限制安全上限（防止误配置导致超额风险）
HARD_LIMITS = {
    "max_trade_amount": (0.1, 10000.0),          # 单笔最大保证金 USDT
    "max_drawdown_pct": (5.0, 50.0),             # 最大回撤百分比
    "daily_pnl_hard_stop": (-10000.0, -1.0),     # 每日硬熔断（必须为负）
    "consecutive_loss_limit": (1, 20),           # 连续亏损次数熔断
    "leverage": (1, 100),                        # 杠杆倍数
    "effective_balance_cap": (10.0, 1_000_000.0),  # 逻辑账户拆分上限 USDT
    "min_confidence": (1, 100),                    # 实盘开仓最低置信度
    "min_deferred_signal_score": (1, 100),          # 回调入场最低原始信号强度
    "min_liquidity_score_for_weak_signal": (0, 100), # 弱信号最低流动性评分
    "ev_prior_wins": (0, 50),                       # Bayesian EV 先验胜场
    "ev_prior_total": (1, 100),                     # Bayesian EV 先验总场
    "ev_strong_signal_threshold": (30, 100),        # EV 强信号豁免阈值
    "entry_timing_15m_strong_score_threshold": (30, 100),  # 15m 强信号豁免阈值
    "entry_timing_15m_timeout_hours": (0.5, 24),           # 15m defer 超时小时
    "rank_flush_delay": (1.0, 30.0),                       # Ranking flush 窗口秒
    "max_concurrent_positions": (1, 20),                   # 最大并发持仓数
    # Regime optimization
    "rr_floor_default": (1.0, 3.0),
    "rr_floor_long_bullish": (1.0, 2.0),
    "rr_floor_long_aligned_choppy": (1.0, 2.0),
    "rr_floor_short_bullish": (1.0, 3.0),
    "probe_rr_floor": (1.0, 2.0),
    "low_rr_max_leverage": (1, 20),
    "low_rr_max_position_pct": (0.1, 0.5),
    "low_rr_extra_slot": (0, 5),
    "probe_short_max_leverage": (1, 10),
    "probe_short_max_position_pct": (0.1, 0.3),
    "probe_short_max_concurrent": (1, 1),
    "probe_short_cooldown_hours": (1, 72),
    "short_live_min_score": (30, 100),
    "short_live_min_rsi": (1, 100),
    "short_live_min_range_pos": (0.0, 1.0),
    "short_live_min_htf_votes": (1, 3),
    "short_live_max_pre_move": (-0.20, 0.0),
    # Long Entry Position Guard
    "long_live_max_range_pos": (0.0, 1.0),
    "long_live_max_pre_move": (0.0, 0.30),
    "long_live_max_daily_gain": (0.0, 0.50),
    "long_live_daily_gain_range_pos": (0.0, 1.0),
    "long_live_pullback_min_pct": (0.005, 0.20),
    "long_live_pullback_timeout_hours": (0.5, 24),
    # EV bucket
    "ev_bucket_min_trades": (1, 200),
    # Paper limit fill
    "paper_limit_tick_staleness_sec": (1.0, 600.0),
    # Research liquidity hard filter
    "research_min_volume_24h_usdt": (0.0, 10_000_000_000.0),
    "research_min_open_interest_usd": (0.0, 10_000_000_000.0),
    # Agent health supervisor (#95) — observability-only 阈值
    "agent_stall_timeout_sec": (10, 3600),
    "queue_backlog_warn_pending": (50, 1000),
    "data_stale_timeout_sec": (30, 3600),
    "agent_tick_stall_timeout_sec": (30, 3600),
}


# 内置默认值（与 .env.example 保持一致，作为最后兜底）
DEFAULTS = {
    "exchange": "okx",
    "use_testnet": False,
    "leverage": 3,
    "max_trade_amount": 10.0,
    "max_drawdown_pct": 20.0,
    "daily_pnl_hard_stop": -50.0,
    "consecutive_loss_limit": 3,
    "research_interval": 4 * 3600,
    "max_active_symbols": 5,
    "rolling_window_size": 20,
    "decay_threshold_win_rate": 0.50,
    "decay_threshold_profit_factor": 1.5,
    "interval": "1h",
    # 逻辑账户拆分：None=用真实余额；设值则 risk_budget 用 min(real, cap)
    # 设计意图：6020 USDT 总余额中只让 1000 USDT 参与风控计算，相当于OKX逻辑拆分
    "effective_balance_cap": None,
    # 交易质量门：Judge/Executor/PaperExecutor 统一口径
    "min_confidence": 60,
    "min_deferred_signal_score": 45,
    "min_liquidity_score_for_weak_signal": 1,
    # RQ-01: Bayesian EV 保守化
    "ev_prior_wins": 2,
    "ev_prior_total": 5,
    "ev_strong_signal_threshold": 70,
    # RQ-06: 信号原型 cooldown
    "archetype_cooldown_enabled": True,
    # RQ-04: 早期持仓复核
    "early_review_enabled": True,
    # RQ-05: 盈利保护
    "profit_protection_enabled": True,
    # RQ-03: 候选排序
    "ranking_enabled": True,
    "rank_flush_delay": 5.0,
    "max_concurrent_positions": 3,
    # RQ-15M: 15m 入场时机确认
    "entry_timing_15m_enabled": True,
    "entry_timing_15m_required": True,
    "entry_timing_15m_neutral_allows_strong_signal": True,
    "entry_timing_15m_strong_score_threshold": 70,
    "entry_timing_15m_defer_on_block": True,
    "entry_timing_15m_timeout_hours": 4,
    # Regime optimization (Phase 1)
    "regime_hysteresis_enabled": True,
    "short_regime_guard_enabled": True,
    "probe_short_enabled": True,
    "low_rr_slot_enabled": True,
    "counterfactual_ledger_enabled": True,
    "rr_floor_default": 1.5,
    "rr_floor_long_bullish": 1.30,
    "rr_floor_long_aligned_choppy": 1.30,
    "rr_floor_short_bullish": 1.80,
    "probe_rr_floor": 1.30,
    "low_rr_long_aligned_enabled": True,
    "low_rr_max_leverage": 5,
    "low_rr_max_position_pct": 0.5,
    "low_rr_extra_slot": 1,
    "probe_short_max_position_pct": 0.3,
    "probe_short_max_leverage": 3,
    "probe_short_max_concurrent": 1,
    "probe_short_cooldown_hours": 24,
    "short_live_min_score": 55,
    "short_live_min_rsi": 40,
    "short_live_min_range_pos": 0.45,
    "short_live_require_daily_bearish": True,
    "short_live_min_htf_votes": 2,
    "short_live_max_pre_move": -0.01,
    # Long Entry Position Guard (PRD long_entry_position_guard_prd.md)
    "long_live_position_guard_enabled": True,
    "long_live_max_range_pos": 0.82,
    "long_live_max_pre_move": 0.05,
    "long_live_max_daily_gain": 0.10,
    "long_live_daily_gain_range_pos": 0.75,
    "long_live_pullback_min_pct": 0.025,
    "long_live_pullback_timeout_hours": 4,
    "long_live_overheat_disable_chase": True,
    # EV bucket sparse-sample protection
    "ev_bucket_min_trades": 10,
    "ev_bucket_sparse_allow_uplift": False,
    # Phase 2: 决策语义拆分 + 开仓解冻
    "phase2_signal_confidence_split_enabled": True,
    "phase2_momentum_probe_long_enabled": True,
    "phase2_trend_saturation_enabled": True,
    "phase2_bucketed_ev_enabled": True,
    # Drawdown baseline
    "drawdown_baseline_mode": "session_start",
    "reset_risk_baseline_on_start": True,
    # Paper limit fill: max tick staleness before fallback gates to no_tick rejection
    "paper_limit_tick_staleness_sec": 60,
    # Paper dual-track simulation (idealized vs realistic)
    "paper_dual_track_enabled": True,
    # Research liquidity hard filter: enforced before LLM candidate selection
    "research_min_volume_24h_usdt": 50_000_000,
    "research_min_open_interest_usd": 10_000_000,
    # Agent health supervisor (#95)
    "agent_stall_timeout_sec": 60,
    "queue_backlog_warn_pending": 200,
    "data_stale_timeout_sec": 180,
    "agent_tick_stall_timeout_sec": 120,
}


class ConfigError(RuntimeError):
    """配置错误：值越界、live模式凭证缺失等"""
    pass


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on")
    return bool(v)


def _load_yaml(path: str) -> dict:
    """加载 config.yaml 的 risk 子节点（兼容旧格式）"""
    if yaml is None or not os.path.exists(path):
        return {}
    try:
        with open(path, 'r') as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return {}

    risk = data.get('risk', {}) or {}
    out = {}
    if 'max_trade_amount' in risk:
        out['max_trade_amount'] = float(risk['max_trade_amount'])
    # yaml 用 max_drawdown（0.20格式），统一为 max_drawdown_pct（20.0格式）
    if 'max_drawdown' in risk:
        v = float(risk['max_drawdown'])
        out['max_drawdown_pct'] = v * 100 if v <= 1.0 else v
    if 'max_drawdown_pct' in risk:
        out['max_drawdown_pct'] = float(risk['max_drawdown_pct'])
    if 'max_daily_loss' in risk:
        # yaml 的 max_daily_loss 是正数，转为负的 daily_pnl_hard_stop
        out['daily_pnl_hard_stop'] = -abs(float(risk['max_daily_loss']))
    return out


def _read_env_overrides() -> dict:
    """从环境变量读取（覆盖 yaml）"""
    out = {}
    env_map = {
        "EXCHANGE": ("exchange", str),
        "USE_TESTNET": ("use_testnet", _to_bool),
        "LEVERAGE": ("leverage", int),
        "MAX_TRADE_AMOUNT": ("max_trade_amount", float),
        "MAX_DRAWDOWN_PCT": ("max_drawdown_pct", float),
        "MAX_DAILY_LOSS": ("daily_pnl_hard_stop", lambda v: -abs(float(v))),
        "DAILY_PNL_HARD_STOP": ("daily_pnl_hard_stop", float),
        "CONSECUTIVE_LOSS_LIMIT": ("consecutive_loss_limit", int),
        "RESEARCH_INTERVAL": ("research_interval", int),
        "MAX_ACTIVE_SYMBOLS": ("max_active_symbols", int),
        "EFFECTIVE_BALANCE_CAP": ("effective_balance_cap", float),
        "MIN_CONFIDENCE": ("min_confidence", int),
        "MIN_DEFERRED_SIGNAL_SCORE": ("min_deferred_signal_score", int),
        "MIN_LIQUIDITY_SCORE_FOR_WEAK_SIGNAL": ("min_liquidity_score_for_weak_signal", int),
        # RQ-01/06/04/05/03/09: 策略优化参数
        "EV_PRIOR_WINS": ("ev_prior_wins", int),
        "EV_PRIOR_TOTAL": ("ev_prior_total", int),
        "EV_STRONG_SIGNAL_THRESHOLD": ("ev_strong_signal_threshold", int),
        "ARCHETYPE_COOLDOWN_ENABLED": ("archetype_cooldown_enabled", _to_bool),
        "EARLY_REVIEW_ENABLED": ("early_review_enabled", _to_bool),
        "PROFIT_PROTECTION_ENABLED": ("profit_protection_enabled", _to_bool),
        "RANKING_ENABLED": ("ranking_enabled", _to_bool),
        "RANK_FLUSH_DELAY": ("rank_flush_delay", float),
        "MAX_CONCURRENT_POSITIONS": ("max_concurrent_positions", int),
        # RQ-15M: 15m 入场时机确认
        "ENTRY_TIMING_15M_ENABLED": ("entry_timing_15m_enabled", _to_bool),
        "ENTRY_TIMING_15M_REQUIRED": ("entry_timing_15m_required", _to_bool),
        "ENTRY_TIMING_15M_NEUTRAL_ALLOWS_STRONG_SIGNAL": ("entry_timing_15m_neutral_allows_strong_signal", _to_bool),
        "ENTRY_TIMING_15M_STRONG_SCORE_THRESHOLD": ("entry_timing_15m_strong_score_threshold", int),
        "ENTRY_TIMING_15M_DEFER_ON_BLOCK": ("entry_timing_15m_defer_on_block", _to_bool),
        "ENTRY_TIMING_15M_TIMEOUT_HOURS": ("entry_timing_15m_timeout_hours", int),
        # Regime optimization
        "REGIME_HYSTERESIS_ENABLED": ("regime_hysteresis_enabled", _to_bool),
        "SHORT_REGIME_GUARD_ENABLED": ("short_regime_guard_enabled", _to_bool),
        "PROBE_SHORT_ENABLED": ("probe_short_enabled", _to_bool),
        "LOW_RR_SLOT_ENABLED": ("low_rr_slot_enabled", _to_bool),
        "COUNTERFACTUAL_LEDGER_ENABLED": ("counterfactual_ledger_enabled", _to_bool),
        "RR_FLOOR_DEFAULT": ("rr_floor_default", float),
        "RR_FLOOR_LONG_BULLISH": ("rr_floor_long_bullish", float),
        "RR_FLOOR_LONG_ALIGNED_CHOPPY": ("rr_floor_long_aligned_choppy", float),
        "RR_FLOOR_SHORT_BULLISH": ("rr_floor_short_bullish", float),
        "PROBE_RR_FLOOR": ("probe_rr_floor", float),
        "LOW_RR_LONG_ALIGNED_ENABLED": ("low_rr_long_aligned_enabled", _to_bool),
        "LOW_RR_MAX_LEVERAGE": ("low_rr_max_leverage", int),
        "LOW_RR_MAX_POSITION_PCT": ("low_rr_max_position_pct", float),
        "LOW_RR_EXTRA_SLOT": ("low_rr_extra_slot", int),
        "PROBE_SHORT_MAX_POSITION_PCT": ("probe_short_max_position_pct", float),
        "PROBE_SHORT_MAX_LEVERAGE": ("probe_short_max_leverage", int),
        "PROBE_SHORT_MAX_CONCURRENT": ("probe_short_max_concurrent", int),
        "PROBE_SHORT_COOLDOWN_HOURS": ("probe_short_cooldown_hours", int),
        "SHORT_LIVE_MIN_SCORE": ("short_live_min_score", int),
        "SHORT_LIVE_MIN_RSI": ("short_live_min_rsi", int),
        "SHORT_LIVE_MIN_RANGE_POS": ("short_live_min_range_pos", float),
        "SHORT_LIVE_REQUIRE_DAILY_BEARISH": ("short_live_require_daily_bearish", _to_bool),
        "SHORT_LIVE_MIN_HTF_VOTES": ("short_live_min_htf_votes", int),
        "SHORT_LIVE_MAX_PRE_MOVE": ("short_live_max_pre_move", float),
        # Long Entry Position Guard
        "LONG_LIVE_POSITION_GUARD_ENABLED": ("long_live_position_guard_enabled", _to_bool),
        "LONG_LIVE_MAX_RANGE_POS": ("long_live_max_range_pos", float),
        "LONG_LIVE_MAX_PRE_MOVE": ("long_live_max_pre_move", float),
        "LONG_LIVE_MAX_DAILY_GAIN": ("long_live_max_daily_gain", float),
        "LONG_LIVE_DAILY_GAIN_RANGE_POS": ("long_live_daily_gain_range_pos", float),
        "LONG_LIVE_PULLBACK_MIN_PCT": ("long_live_pullback_min_pct", float),
        "LONG_LIVE_PULLBACK_TIMEOUT_HOURS": ("long_live_pullback_timeout_hours", int),
        "LONG_LIVE_OVERHEAT_DISABLE_CHASE": ("long_live_overheat_disable_chase", _to_bool),
        # EV bucket
        "EV_BUCKET_MIN_TRADES": ("ev_bucket_min_trades", int),
        "EV_BUCKET_SPARSE_ALLOW_UPLIFT": ("ev_bucket_sparse_allow_uplift", _to_bool),
        # Phase 2: 决策语义拆分 + 开仓解冻
        "PHASE2_SIGNAL_CONFIDENCE_SPLIT_ENABLED": ("phase2_signal_confidence_split_enabled", _to_bool),
        "PHASE2_MOMENTUM_PROBE_LONG_ENABLED": ("phase2_momentum_probe_long_enabled", _to_bool),
        "PHASE2_TREND_SATURATION_ENABLED": ("phase2_trend_saturation_enabled", _to_bool),
        "PHASE2_BUCKETED_EV_ENABLED": ("phase2_bucketed_ev_enabled", _to_bool),
        # Drawdown baseline
        "DRAWDOWN_BASELINE_MODE": ("drawdown_baseline_mode", str),
        "RESET_RISK_BASELINE_ON_START": ("reset_risk_baseline_on_start", _to_bool),
        # Paper limit fill
        "PAPER_LIMIT_TICK_STALENESS_SEC": ("paper_limit_tick_staleness_sec", float),
        # Paper dual-track simulation
        "PAPER_DUAL_TRACK_ENABLED": ("paper_dual_track_enabled", _to_bool),
        # Research liquidity hard filter
        "RESEARCH_MIN_VOLUME_24H_USDT": ("research_min_volume_24h_usdt", float),
        "RESEARCH_MIN_OPEN_INTEREST_USD": ("research_min_open_interest_usd", float),
        # Agent health supervisor (#95)
        "AGENT_STALL_TIMEOUT_SEC": ("agent_stall_timeout_sec", float),
        "QUEUE_BACKLOG_WARN_PENDING": ("queue_backlog_warn_pending", int),
        "DATA_STALE_TIMEOUT_SEC": ("data_stale_timeout_sec", float),
        "AGENT_TICK_STALL_TIMEOUT_SEC": ("agent_tick_stall_timeout_sec", float),
    }
    for env_key, (cfg_key, caster) in env_map.items():
        raw = os.getenv(env_key)
        if raw is None or raw == "":
            continue
        try:
            out[cfg_key] = caster(raw)
        except Exception as e:
            raise ConfigError(f"环境变量 {env_key}={raw!r} 解析失败: {e}")
    # 透传字段（不参与硬限制校验）
    for env_key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        v = os.getenv(env_key, "")
        out[env_key.lower()] = v
    return out


def _validate_hard_limits(cfg: dict):
    """硬限制范围校验"""
    for key, (lo, hi) in HARD_LIMITS.items():
        if key not in cfg:
            continue
        val = cfg[key]
        if val is None:  # 允许显式 None（如 effective_balance_cap）
            continue
        if not (lo <= val <= hi):
            raise ConfigError(
                f"配置项 {key}={val} 超出安全范围 [{lo}, {hi}]，拒绝启动"
            )


def clamp_to_hard_limits(cfg: dict) -> dict:
    """把风险限额 clamp 到 HARD_LIMITS 区间内（非破坏 None），返回新 dict。

    与 _validate_hard_limits（超界 raise 拒绝启动）不同：本函数 clamp 而非 raise，
    供 config_loader 加载失败时的 env 兜底路径使用，杜绝风险限额 fail-open 到未约束值。
    """
    out = dict(cfg)
    for key, (lo, hi) in HARD_LIMITS.items():
        if key not in out or out[key] is None:
            continue
        out[key] = max(lo, min(hi, out[key]))
    return out


def _validate_live_mode(cfg: dict):
    """live 模式凭证校验：USE_TESTNET=false 时关键凭证不能为空"""
    if cfg.get("use_testnet"):
        return  # testnet 模式跳过

    exchange = cfg.get("exchange", "okx")
    missing = []
    if exchange == "okx":
        for k in ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSWORD"):
            if not os.getenv(k):
                missing.append(k)
    elif exchange == "binance":
        for k in ("BINANCE_API_KEY", "BINANCE_SECRET"):
            if not os.getenv(k):
                missing.append(k)
    if missing:
        raise ConfigError(
            f"Live 模式（USE_TESTNET=false）下缺少凭证: {missing}，拒绝启动。"
            f"如确认要在 testnet 运行，请设置 USE_TESTNET=true"
        )


def load_config(yaml_path: str = "config.yaml",
                env_file: Optional[str] = ".env",
                strict_live_check: bool = True) -> dict:
    """加载并校验配置

    Args:
        yaml_path: config.yaml 路径
        env_file: .env 文件路径（None 则不加载）
        strict_live_check: 是否对 live 模式做凭证校验（测试场景可关闭）

    Returns:
        统一格式的 config 字典
    """
    if env_file:
        load_dotenv(env_file, override=False)

    cfg = dict(DEFAULTS)
    cfg.update(_load_yaml(yaml_path))
    cfg.update(_read_env_overrides())

    _validate_hard_limits(cfg)
    if strict_live_check:
        _validate_live_mode(cfg)

    return cfg


def format_banner(cfg: dict) -> str:
    """生成启动 banner（硬限制摘要）"""
    mode = "TESTNET" if cfg.get("use_testnet") else "LIVE 实盘"
    timing_15m = "开启" if cfg.get("entry_timing_15m_enabled") else "关闭"
    ranking = "开启" if cfg.get("ranking_enabled") else "关闭"
    regime_guard = "开启" if cfg.get("short_regime_guard_enabled") else "关闭"
    low_rr = "开启" if cfg.get("low_rr_slot_enabled") else "关闭"
    hysteresis = "开启" if cfg.get("regime_hysteresis_enabled") else "关闭"
    probe_short = "开启" if cfg.get("probe_short_enabled") else "关闭"
    ledger = "开启" if cfg.get("counterfactual_ledger_enabled") else "关闭"
    conf_split = "开启" if cfg.get("phase2_signal_confidence_split_enabled") else "关闭"
    momentum_probe = "开启" if cfg.get("phase2_momentum_probe_long_enabled") else "关闭"
    trend_sat = "开启" if cfg.get("phase2_trend_saturation_enabled") else "关闭"
    bucketed_ev = "开启" if cfg.get("phase2_bucketed_ev_enabled") else "关闭"
    long_pos_guard = "开启" if cfg.get("long_live_position_guard_enabled", True) else "关闭"
    overheat_chase = "禁止" if cfg.get("long_live_overheat_disable_chase", True) else "允许"
    bucket_uplift = "允许" if cfg.get("ev_bucket_sparse_allow_uplift", False) else "禁止"
    lines = [
        "=" * 60,
        f"配置摘要（{mode}）",
        "-" * 60,
        f"  交易所:                {cfg.get('exchange')}",
        f"  杠杆:                  {cfg.get('leverage')}x",
        f"  单笔最大保证金:        {cfg.get('max_trade_amount')} USDT",
        f"  最大回撤:              {cfg.get('max_drawdown_pct')}%",
        f"  每日硬熔断:            {cfg.get('daily_pnl_hard_stop')} USDT",
        f"  连续亏损熔断:          {cfg.get('consecutive_loss_limit')} 次",
        f"  研判周期:              {cfg.get('research_interval') // 3600}h",
        f"  最大活跃标的:          {cfg.get('max_active_symbols')}",
        f"  最大并发持仓:          {cfg.get('max_concurrent_positions', 3)}",
        f"  逻辑账户拆分:          {cfg.get('effective_balance_cap') or '未启用（用真实余额）'}",
        f"  回撤基准模式:          {cfg.get('drawdown_baseline_mode', 'session_start')} (cap={cfg.get('effective_balance_cap') or 'None'})",
        f"  开仓最低置信度:        {cfg.get('min_confidence')}",
        f"  回调最低信号强度:      {cfg.get('min_deferred_signal_score')}",
        f"  弱信号最低流动性:      {cfg.get('min_liquidity_score_for_weak_signal')}",
        f"  15m入场确认:           {timing_15m} (强信号≥{cfg.get('entry_timing_15m_strong_score_threshold')}, 超时{cfg.get('entry_timing_15m_timeout_hours')}h)",
        f"  Ranking裁决:           {ranking} (flush窗口={cfg.get('rank_flush_delay', 5)}s)",
        f"  Regime Hysteresis:     {hysteresis}",
        f"  Short Regime Guard:    {regime_guard} (R:R≥{cfg.get('rr_floor_short_bullish', 1.8)}, score≥{cfg.get('short_live_min_score', 55)}, RSI≥{cfg.get('short_live_min_rsi', 40)}, range≥{cfg.get('short_live_min_range_pos', 0.45)}, HTF票数≥{cfg.get('short_live_min_htf_votes', 2)}, daily={cfg.get('short_live_require_daily_bearish', True)})",
        f"  Probe Short:           {probe_short} (max_lev={cfg.get('probe_short_max_leverage', 3)}x, cooldown={cfg.get('probe_short_cooldown_hours', 24)}h)",
        f"  Low R:R Long:          {low_rr} (floor={cfg.get('rr_floor_long_bullish', 1.3)}, slot={cfg.get('low_rr_extra_slot', 1)}, lev≤{cfg.get('low_rr_max_leverage', 5)}x)",
        f"  R:R Floors:            default={cfg.get('rr_floor_default', 1.5)} long_bullish={cfg.get('rr_floor_long_bullish', 1.30)} long_aligned_choppy={cfg.get('rr_floor_long_aligned_choppy', 1.30)} probe={cfg.get('probe_rr_floor', 1.30)} short_bullish={cfg.get('rr_floor_short_bullish', 1.80)}",
        f"  Long Aligned Choppy:   {'开启' if cfg.get('low_rr_long_aligned_enabled', True) else '关闭'} (mixed/choppy 下趋势一致多头允许 floor={cfg.get('rr_floor_long_aligned_choppy', 1.30)})",
        f"  Counterfactual Ledger: {ledger}",
        f"  Phase2 Confidence Split: {conf_split}",
        f"  Phase2 Momentum Probe Long: {momentum_probe}",
        f"  Phase2 Trend Saturation: {trend_sat}",
        f"  Phase2 Bucketed EV: {bucketed_ev}",
        f"  Long Entry Position Guard: {long_pos_guard} (range_pos≥{cfg.get('long_live_max_range_pos', 0.82)}, "
        f"pre_12h≥{cfg.get('long_live_max_pre_move', 0.05)}, "
        f"daily_gain≥{cfg.get('long_live_max_daily_gain', 0.10)}, "
        f"pullback_min={cfg.get('long_live_pullback_min_pct', 0.025)}, "
        f"timeout={cfg.get('long_live_pullback_timeout_hours', 4)}h, chase={overheat_chase})",
        f"  EV Bucket Sparse:      min_trades={cfg.get('ev_bucket_min_trades', 10)} sparse_uplift={bucket_uplift}",
    ]
    # FR-008: 启动 banner 打印当前命名空间下的状态文件路径
    try:
        from utils.state_paths import get_state_paths
        sp = get_state_paths()
        lines.append("-" * 60)
        lines.extend(sp.as_banner_lines())
    except Exception:
        # banner 不影响启动；解析失败仅静默
        pass
    lines.append("=" * 60)
    return "\n".join(lines)
