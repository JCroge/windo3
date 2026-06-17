"""多标的裁判决策 Agent - 精确交易计划输出

消费 TechAnalyst 的9维度信号，输出精确交易计划：
入场区间、多级止盈、止损位、杠杆倍数、仓位大小
"""

import os
import time
import uuid
import asyncio
import ccxt
from agents.base import BaseAgent
from utils.symbol import to_internal
from utils.archetype_cooldown import ArchetypeCooldown
from utils.candidate_ranker import CandidateRanker
from utils.market_regime import RegimeManager
from utils.counterfactual_ledger import CounterfactualLedger
from utils.decision_tape import DecisionTape, build_bundle
from utils.state_paths import get_state_paths
from dotenv import load_dotenv

load_dotenv()


JUDGE_PROMPT = """你是加密货币合约交易的裁判。基于技术分析师提供的多维度市场信号，做出交易决策。

决策原则：
1. 信号明确就果断入场——趋势+多维度确认=高置信度开仓
2. 只在信号矛盾时观望——不是"有疑虑就不做"，而是"信号冲突才不做"
3. 风控优先：止损必须在关键支撑/阻力位外侧
4. 反人性：散户极度贪婪时谨慎，极度恐惧时寻找机会
5. 趋势是朋友：多周期(1h+4h+1d)方向一致时，给予高置信度

【关键禁令——违反即亏损】
- RSI < 30 时禁止做空：超卖区域反弹概率极高
- RSI > 70 时禁止做多：超买区域回调概率极高
- RSI < 25 + bullish divergence = 强烈做多信号
- RSI > 75 + bearish divergence = 强烈做空信号
- 趋势强度 > 90 往往是趋势末期，顺势开仓风险高
- 散户反指在RSI极端区域失效

【高置信度入场条件】
- 多周期趋势一致(1h+4h+1d同方向) + 任意2个辅助维度确认 → confidence≥70
- MA交叉/对齐 + 趋势方向一致 + 量价配合 → confidence≥65
- OI背离 + 鲸鱼方向 + Taker压力 三者共振 → confidence≥60

以JSON格式回复：
{
    "action": "open_long/open_short/close/hold",
    "confidence": 0-100,
    "reasoning": "决策理由（中文，2-3句话）",
    "key_factors": ["因素1", "因素2", "因素3"],
    "risk_warnings": ["风险1"]
}"""


class MultiJudge(BaseAgent):
    name = "judge"
    subscriptions = ["tech_analysis:*", "price_tick:*", "symbol_update", "execution_result:*", "news_snapshot", "strategy_review", "pnl_resolved", "pnl_mismatch"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self._symbol_state = {}
        self._decision_cooldown = 55
        self._force_close_cooldown = 300  # 强平后5分钟禁止同标的开仓
        # 与 utils/config_loader.py 的 DEFAULTS['max_trade_amount'] 保持一致
        self._max_trade_amount = config.get('max_trade_amount', 10) if config else 10
        self.exchange = None
        self._available_balance = 0.0
        self._news_snapshot = {}  # {base: [headlines]} 最新新闻快照

        # ═══ P2-N: 期望值（EV）门 ═══
        # 从 Reviewer 接收滚动窗口胜率，用于计算 EV = p_win × profit - (1-p_win) × loss
        self._recent_win_rate = None
        self._recent_profit_factor = None
        self._total_completed_trades = 0
        self._recent_wins = 0  # Reviewer 报告的近期胜场数
        # 历史交易不足时不应用 EV 门（让系统先积累样本）
        self._min_trades_for_ev_gate = config.get('min_trades_for_ev_gate', 10) if config else 10
        # 降级胜率：P2-O 调稳 0.55 → 0.52，让启动期 EV 门更严
        self._fallback_win_rate = config.get('fallback_win_rate', 0.52) if config else 0.52
        # EV 阈值：P2-O 调稳 0 → 0.05，要求 EV 至少 +0.05 USDT 才入场
        self._ev_min_threshold = config.get('ev_min_threshold', 0.05) if config else 0.05
        # ═══ RQ-01: 小样本 EV 保守化（Bayesian prior）═══
        self._ev_prior_wins = config.get('ev_prior_wins', 2) if config else 2
        self._ev_prior_total = config.get('ev_prior_total', 5) if config else 5
        # 强信号豁免阈值：score >= 此值时 EV 门放宽
        self._ev_strong_signal_threshold = config.get('ev_strong_signal_threshold', 70) if config else 70

        # ═══ P2-O: 最大并发持仓上限 ═══
        # 启动期保守：余额 ~100 USDT 时最多 3 个并发持仓，单仓 ~10 USDT margin
        # 后续胜率验证后可在 config 中放宽
        self._max_concurrent_positions = config.get('max_concurrent_positions', 3) if config else 3
        self._open_positions = set()  # 当前已知开仓的 symbol 集合
        self._position_slots = {}  # {symbol: slot_type} 记录每个持仓的 slot 类型
        self._pending_open_symbols = set()  # ranking selected 但尚未收到 execution_result
        self._pending_open_slots = {}  # {symbol: slot_type} pending 的 slot 类型
        self._pending_open_ts = {}  # {symbol: timestamp} pending 加入时间
        self._pending_ttl = 120  # pending 超时秒数，超时后自动释放
        # 与 Executor/PaperExecutor 保持同一实盘开仓门槛，避免 Judge 发布下游必然拒绝的开仓。
        self._min_confidence = config.get('min_confidence', 60) if config else 60
        # 弱信号只允许进入观察/回调队列，不允许回调命中后自动抬到 60 分实盘开仓。
        self._min_deferred_signal_score = config.get('min_deferred_signal_score', 45) if config else 45
        # 流动性评分为 0 时，只允许极强信号继续；否则容易被薄盘口噪音和滑点吞掉。
        self._min_liquidity_score_for_weak_signal = config.get('min_liquidity_score_for_weak_signal', 1) if config else 1

        # ═══ 逻辑账户拆分（2026-05-19）═══
        # None=用真实余额；设值则风险预算用 min(real_balance, cap)
        # 设计：总余额 6020 USDT 中只让 1000 USDT 参与风控计算
        # 等价于：max_loss 从 6020×5%=301 USDT 降到 1000×5%=50 USDT（与 Daily Hard Stop -50 对齐）
        self._effective_balance_cap = config.get('effective_balance_cap') if config else None
        self._balance_adapter = None

        # ═══ RQ-06: 信号原型级 cooldown ═══
        cooldown_enabled = config.get('archetype_cooldown_enabled', True) if config else True
        self._archetype_cooldown = ArchetypeCooldown(enabled=cooldown_enabled, logger=self.logger)

        # ═══ RQ-03: 候选排序 ═══
        ranking_enabled = config.get('ranking_enabled', True) if config else True
        low_rr_extra = config.get('low_rr_extra_slot', 1) if config else 1
        self._candidate_ranker = CandidateRanker(
            max_slots=self._max_concurrent_positions,
            enabled=ranking_enabled,
            low_rr_extra_slot=low_rr_extra if (config.get('low_rr_slot_enabled', True) if config else True) else 0,
            logger=self.logger,
        )

        # ═══ RQ-15M: 15m 入场时机确认 ═══
        self._15m_enabled = config.get('entry_timing_15m_enabled', True) if config else True
        self._15m_required = config.get('entry_timing_15m_required', True) if config else True
        self._15m_neutral_allows_strong = config.get('entry_timing_15m_neutral_allows_strong_signal', True) if config else True
        self._15m_strong_score_threshold = config.get('entry_timing_15m_strong_score_threshold', 70) if config else 70
        self._15m_defer_on_block = config.get('entry_timing_15m_defer_on_block', True) if config else True
        self._15m_timeout_hours = config.get('entry_timing_15m_timeout_hours', 4) if config else 4

        # ═══ LLM degraded 告警 ═══
        self._llm_consecutive_failures = 0
        self._llm_degraded_alerted = False

        # ═══ RQ-03: Ranking flush window ═══
        self._rank_flush_delay = config.get('rank_flush_delay', 5.0) if config else 5.0
        self._rank_flush_task = None

        # ═══ Regime Optimization (Phase 1) ═══
        self._regime_manager = RegimeManager(config or {}, self.logger)
        self._counterfactual_ledger = CounterfactualLedger(
            enabled=config.get('counterfactual_ledger_enabled', True) if config else True,
            logger=self.logger,
        )
        # ═══ Counterfactual Replay: decision tape (observability-only, fail-safe) ═══
        self._decision_tape = DecisionTape(
            path=get_state_paths().decision_replay_tape,
            enabled=config.get('decision_tape_enabled', True) if config else True,
            retention_days=config.get('decision_tape_retention_days', 90) if config else 90,
        )
        self._symbol_tech_cache = {}
        self._symbol_llm_cache = {}
        self._symbol_tech_tape_cache = {}
        self._short_regime_guard_enabled = config.get('short_regime_guard_enabled', True) if config else True
        self._probe_short_enabled = config.get('probe_short_enabled', True) if config else True
        self._low_rr_slot_enabled = config.get('low_rr_slot_enabled', True) if config else True
        self._rr_floor_default = config.get('rr_floor_default', 1.50) if config else 1.50
        self._rr_floor_long_bullish = config.get('rr_floor_long_bullish', 1.30) if config else 1.30
        self._rr_floor_long_aligned_choppy = config.get('rr_floor_long_aligned_choppy', 1.30) if config else 1.30
        self._rr_floor_short_bullish = config.get('rr_floor_short_bullish', 1.80) if config else 1.80
        self._probe_rr_floor = config.get('probe_rr_floor', 1.30) if config else 1.30
        self._low_rr_long_aligned_enabled = config.get('low_rr_long_aligned_enabled', True) if config else True
        # trend-entry-rr-fidelity 杠杆① P1:客观路径证据授对齐地板(默认关,灰度)
        self._path_evidence_aligned_enabled = config.get('path_evidence_aligned_enabled', False) if config else False
        self._path_evidence_min_pre12h_return = config.get('path_evidence_min_pre12h_return', 0.03) if config else 0.03
        self._path_evidence_max_range_pos = config.get('path_evidence_max_range_pos', 0.92) if config else 0.92
        self._path_evidence_min_strength = config.get('path_evidence_min_strength', 60) if config else 60
        # trend-entry-rr-fidelity 杠杆② v1:阶梯加权 effective_rr(默认关,灰度)
        self._ladder_rr_enabled = config.get('ladder_rr_enabled', False) if config else False
        self._low_rr_max_leverage = config.get('low_rr_max_leverage', 5) if config else 5
        self._low_rr_max_position_pct = config.get('low_rr_max_position_pct', 0.5) if config else 0.5
        self._probe_short_max_position_pct = config.get('probe_short_max_position_pct', 0.3) if config else 0.3
        self._probe_short_max_leverage = config.get('probe_short_max_leverage', 3) if config else 3
        self._probe_short_max_concurrent = config.get('probe_short_max_concurrent', 1) if config else 1
        self._probe_short_cooldown_hours = config.get('probe_short_cooldown_hours', 24) if config else 24
        self._probe_short_active = None
        self._probe_short_sl_count = 0
        self._probe_short_cooldown_until = 0

        # FR-3C: pnl_resolved 幂等去重(按 correction_event_id/resolution_id),
        # 防止 Resolver 重发同一升级事件导致重复计 SL hit / 重复推 probe_short。
        self._processed_resolution_ids: set = set()
        self._processed_resolution_max = 1024  # 简单 LRU 上限

        # ═══ Side guard live thresholds (short + long) ═══
        # 这些字段在 Judge 主路径与 deferred 路径都需要，统一从 config 读取。
        self._short_live_min_score = config.get('short_live_min_score', 55) if config else 55
        self._short_live_min_rsi = config.get('short_live_min_rsi', 40) if config else 40
        self._short_live_min_range_pos = config.get('short_live_min_range_pos', 0.45) if config else 0.45
        self._short_live_require_daily_bearish = config.get('short_live_require_daily_bearish', True) if config else True
        self._short_live_min_htf_votes = config.get('short_live_min_htf_votes', 2) if config else 2
        self._short_live_max_pre_move = config.get('short_live_max_pre_move', -0.01) if config else -0.01

        # ═══ Long Entry Position Guard ═══
        self._long_live_position_guard_enabled = config.get('long_live_position_guard_enabled', True) if config else True
        self._long_live_max_range_pos = config.get('long_live_max_range_pos', 0.82) if config else 0.82
        self._long_live_max_pre_move = config.get('long_live_max_pre_move', 0.05) if config else 0.05
        self._long_live_max_daily_gain = config.get('long_live_max_daily_gain', 0.10) if config else 0.10
        self._long_live_daily_gain_range_pos = config.get('long_live_daily_gain_range_pos', 0.75) if config else 0.75
        self._long_live_pullback_min_pct = config.get('long_live_pullback_min_pct', 0.025) if config else 0.025
        self._long_live_pullback_timeout_hours = config.get('long_live_pullback_timeout_hours', 4) if config else 4
        self._long_live_overheat_disable_chase = config.get('long_live_overheat_disable_chase', True) if config else True

        # ═══ EV bucket sparse-sample protection ═══
        self._ev_bucket_min_trades = config.get('ev_bucket_min_trades', 10) if config else 10
        self._ev_bucket_sparse_allow_uplift = config.get('ev_bucket_sparse_allow_uplift', False) if config else False

        # ═══ Phase 2 Feature Flags ═══
        self._confidence_split_enabled = config.get('phase2_signal_confidence_split_enabled', False) if config else False
        self._momentum_probe_long_enabled = config.get('phase2_momentum_probe_long_enabled', False) if config else False
        self._trend_saturation_enabled = config.get('phase2_trend_saturation_enabled', False) if config else False
        self._bucketed_ev_enabled = config.get('phase2_bucketed_ev_enabled', False) if config else False
        self._request_id_enabled = True  # deprecated: request_id is always-on (AC2-06)
        # probe_long parameters
        self._probe_long_max_concurrent = 1
        self._probe_long_max_position_pct = 0.3
        self._probe_long_max_leverage = 3
        self._probe_long_rsi_min = 70
        self._probe_long_rsi_max = 85
        # Bucketed EV storage
        self._bucketed_metrics = {}

    def _get_state(self, symbol: str) -> dict:
        if symbol not in self._symbol_state:
            self._symbol_state[symbol] = {
                "last_decision_time": 0,
                "last_tech": None,
                "last_force_close_time": 0,
                "last_open_time": 0,
                "trend_streak": 0,
                "trend_streak_dir": None,
                "pending_pullback": None,
                "pending_pullback_time": 0,
                "pullback_bonus": 0,
                "deferred_entry": None,
                "sl_timestamps": [],
            }
        return self._symbol_state[symbol]

    # ── P1-6: State Persistence ────────────────────────────────────────────

    _STATE_PATH = "data/judge_state.json"
    _state_dirty = False

    def _persist_state(self):
        """原子持久化关键风险状态（deferred_entry, cooldown timestamps, sl_timestamps）"""
        if not self._state_dirty:
            return
        try:
            from utils.atomic_io import atomic_write_json
            persist = {}
            for sym, st in self._symbol_state.items():
                persist[sym] = {
                    "last_force_close_time": st.get("last_force_close_time", 0),
                    "last_open_time": st.get("last_open_time", 0),
                    "sl_timestamps": st.get("sl_timestamps", []),
                    "deferred_entry": st.get("deferred_entry"),
                }
            atomic_write_json(self._STATE_PATH, persist)
            self._state_dirty = False
        except Exception as e:
            self.logger.warning(f"[Judge] state持久化失败: {e}")

    def _load_persisted_state(self):
        """启动时恢复关键风险状态，清理过期条目"""
        import json
        try:
            with open(self._STATE_PATH, 'r') as f:
                saved = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        except Exception as e:
            self.logger.warning(f"[Judge] state加载失败: {e}")
            return

        now = time.time()
        restored = 0
        for sym, data in saved.items():
            state = self._get_state(sym)
            lfc = data.get("last_force_close_time", 0)
            if lfc and now - lfc < 7200:
                state["last_force_close_time"] = lfc
            lot = data.get("last_open_time", 0)
            if lot and now - lot < 600:
                state["last_open_time"] = lot
            sl_ts = data.get("sl_timestamps", [])
            cutoff = now - 4 * 3600
            state["sl_timestamps"] = [t for t in sl_ts if t > cutoff]
            deferred = data.get("deferred_entry")
            if deferred:
                created = deferred.get("created_at", 0)
                timeout_h = deferred.get("timeout_hours", 4)
                if now - created < timeout_h * 3600:
                    state["deferred_entry"] = deferred
                    restored += 1
        if restored:
            self.logger.info(f"[Judge] 恢复 {restored} 个 deferred_entry")

    async def setup(self):
        self.init_llm()
        from utils.exchange_factory import create_exchange
        self.exchange = create_exchange(self.config, require_private=True, purpose="judge_balance")

        # 从 positions.json 恢复 _open_positions，防止重启后突破并发上限
        try:
            import json
            from utils.state_paths import get_state_paths
            with open(get_state_paths().positions, 'r') as f:
                saved_positions = json.load(f)
            if saved_positions:
                for sym in saved_positions:
                    norm = sym.replace('-SWAP', '')
                    self._open_positions.add(norm)
                    if isinstance(saved_positions, dict) and isinstance(saved_positions[sym], dict):
                        pos_data = saved_positions[sym]
                        attr = pos_data.get('attribution', {}) or {}
                        slot = pos_data.get('slot_type') or attr.get('slot_type', 'main')
                        is_probe = pos_data.get('is_probe') or attr.get('is_probe', False)
                    else:
                        slot = 'main'
                        is_probe = False
                    self._position_slots[norm] = slot
                    if is_probe and self._probe_short_active is None:
                        self._probe_short_active = norm
                self.logger.info(
                    f"[Judge] 从 positions.json 恢复 {len(self._open_positions)} 个持仓: "
                    f"{sorted(self._open_positions)}"
                )
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        except Exception as e:
            self.logger.warning(f"[Judge] positions.json 恢复失败: {e}")

        self._load_persisted_state()

        self.logger.info("精确决策裁判Agent就绪")
        # RQ-09: 启动日志打印关键阈值
        self.logger.info(
            f"[Judge Config] min_confidence={self._min_confidence} "
            f"ev_prior={self._ev_prior_wins}/{self._ev_prior_total} "
            f"ev_threshold={self._ev_min_threshold} "
            f"strong_signal={self._ev_strong_signal_threshold} "
            f"cooldown={self._archetype_cooldown.enabled} "
            f"ranking={self._candidate_ranker.enabled} "
            f"max_positions={self._max_concurrent_positions}"
        )
        self.logger.info(
            f"[Judge Config] regime_guard={self._short_regime_guard_enabled} "
            f"rr_default={self._rr_floor_default} "
            f"rr_floor_long_bull={self._rr_floor_long_bullish} "
            f"rr_floor_long_aligned_choppy={self._rr_floor_long_aligned_choppy} "
            f"rr_floor_short_bull={self._rr_floor_short_bullish} "
            f"probe_rr_floor={self._probe_rr_floor} "
            f"low_rr_long_aligned={self._low_rr_long_aligned_enabled} "
            f"low_rr_slot={self._low_rr_slot_enabled} "
            f"probe_short={self._probe_short_enabled} "
            f"counterfactual={self._counterfactual_ledger._enabled}"
        )
        try:
            from utils.balance_adapter import BalanceAdapter
            self._balance_adapter = BalanceAdapter(self.exchange, ttl=10.0, logger=self.logger)
        except Exception as e:
            self.logger.warning(f"BalanceAdapter 初始化失败（降级）: {e}")
            self._balance_adapter = None

    def _sweep_stale_pending(self):
        """清理超时的 pending 槽位，防止 Executor 拒单后槽位永久占用"""
        now = time.time()
        stale = [s for s, ts in self._pending_open_ts.items() if now - ts > self._pending_ttl]
        for s in stale:
            self._pending_open_symbols.discard(s)
            self._pending_open_ts.pop(s, None)
            self._pending_open_slots.pop(s, None)
            self.logger.warning(f"[Judge] pending TTL expired for {s}, releasing slot")

    async def on_message(self, msg: dict):
        if msg['type'] == 'symbol_update':
            for s in msg['payload'].get('removed', []):
                removed_state = self._symbol_state.pop(s, None)
                if removed_state and removed_state.get('deferred_entry'):
                    self.logger.info(f"[Judge] {s} 移除，取消延迟入场")
                self._symbol_tech_cache.pop(s, None)
                if hasattr(self, "_symbol_llm_cache"):
                    self._symbol_llm_cache.pop(s, None)
                if hasattr(self, "_symbol_tech_tape_cache"):
                    self._symbol_tech_tape_cache.pop(s, None)
            if msg['payload'].get('removed'):
                self._regime_manager.update(self._symbol_tech_cache)
            return

        if msg['type'] == 'news_snapshot':
            self._news_snapshot = msg['payload'].get('symbol_news', {})
            return

        if msg['type'] in ('pnl_resolved', 'pnl_mismatch'):
            payload = msg.get('payload', {})
            symbol = msg.get('symbol') or payload.get('symbol')
            if not symbol:
                return
            symbol = to_internal(symbol)
            status = payload.get('pnl_status', '')
            if status != 'final':
                self.logger.info(
                    f"[Judge] {symbol} pnl_resolution status={status} 不计 archetype/probe")
                return
            # F4-002: resolution_id 是 make_resolution_id 生成的强幂等键(优先级 corr/sup/key/pos),
            # 优先使用;老 payload 缺 resolution_id 时回退到 correction_event_id / supersedes_event_id。
            resolution_key = (payload.get('resolution_id')
                              or payload.get('correction_event_id')
                              or payload.get('supersedes_event_id') or '')
            if resolution_key:
                pos_id = payload.get('position_id', '')
                idem_key = f"{resolution_key}|{pos_id}"
                if idem_key in self._processed_resolution_ids:
                    self.logger.info(
                        f"[Judge] {symbol} pnl_resolved 幂等跳过 key={idem_key}")
                    return
                self._processed_resolution_ids.add(idem_key)
                if len(self._processed_resolution_ids) > self._processed_resolution_max:
                    # 简单 LRU:超量时清空一半最旧条目(set 没有顺序,这里直接清空最旧)
                    self._processed_resolution_ids = set(
                        list(self._processed_resolution_ids)[-self._processed_resolution_max // 2:])
            pnl = payload.get('realized_pnl_net_usdt')
            if pnl is None:
                pnl = payload.get('pnl', 0)
            pnl = float(pnl or 0)
            # archetype cooldown 记 final pnl
            attribution = payload.get('attribution', {})
            if attribution and pnl != 0:
                archetype = self._archetype_cooldown.classify(attribution)
                self._archetype_cooldown.record_result(archetype, pnl)
            # PRD §6.2 #5:final 升级后,若 close_cause 明确为 exchange_sl 才补计 SL hit。
            # close_cause 为 exchange_tp/external_unknown 不计 SL hit。
            close_cause = payload.get('close_cause', '')
            is_strategy_stop = bool(payload.get('is_strategy_stop', close_cause == 'exchange_sl'))
            if is_strategy_stop:
                state = self._get_state(symbol)
                closed_dir = payload.get('direction', payload.get('side', ''))
                sl_dir = 'long' if 'long' in closed_dir else ('short' if 'short' in closed_dir else None)
                self._record_sl_hit(state, sl_dir)
                self.logger.info(
                    f"[Judge] {symbol} pnl_resolved 升级补计 SL hit close_cause={close_cause}")
            # probe_short 升级:仅在 final close cause 为 exchange_sl(is_strategy_stop)
            # 时才补计 SL count;external_unknown/manual_close/liquidation 即便 PnL 为负
            # 也不污染 probe_short cooldown(FR-3C / AC3-P1-004)。
            if self._probe_short_active == symbol:
                self._probe_short_active = None
                if is_strategy_stop and pnl < 0:
                    self._probe_short_sl_count += 1
                    if self._probe_short_sl_count >= 2:
                        self._probe_short_cooldown_until = time.time() + self._probe_short_cooldown_hours * 3600
                        self.logger.info(
                            f"[Judge] probe_short final 升级 SL,连续{self._probe_short_sl_count}次,冷却{self._probe_short_cooldown_hours}h")
                elif pnl >= 0:
                    self._probe_short_sl_count = 0
            self._state_dirty = True
            self._persist_state()
            return

        if msg['type'] == 'strategy_review':
            payload = msg.get('payload', {})
            recent = payload.get('recent_metrics', {})
            self._recent_win_rate = recent.get('win_rate')
            self._recent_profit_factor = recent.get('profit_factor')
            self._total_completed_trades = payload.get('total_trades', 0)
            self._recent_wins = recent.get('winning_trades', 0)
            # Phase 2 EPIC D: 分桶指标
            if getattr(self, '_bucketed_ev_enabled', False):
                segmented = payload.get('segmented_metrics', {})
                if segmented:
                    self._bucketed_metrics = self._parse_segmented_metrics(segmented)
            if self._recent_win_rate is not None:
                self.logger.info(
                    f"[Judge] 接收策略复盘: win_rate={self._recent_win_rate:.1%} "
                    f"profit_factor={self._recent_profit_factor:.2f} "
                    f"total_trades={self._total_completed_trades} "
                    f"wins={self._recent_wins}"
                )
            return

        if msg['type'] == 'execution_result':
            payload = msg.get('payload', msg)
            symbol = msg.get('symbol') or payload.get('symbol')
            if symbol:
                # 统一为系统内部规范 BASE-USDT
                symbol = to_internal(symbol)
                state = self._get_state(symbol)
                status = payload.get('status')
                action = payload.get('action', '')
                self._pending_open_symbols.discard(symbol)
                self._pending_open_ts.pop(symbol, None)
                self._pending_open_slots.pop(symbol, None)
                if status == 'force_closed':
                    state["last_force_close_time"] = time.time()
                    closed_dir = payload.get('direction', payload.get('action', ''))
                    sl_dir = 'long' if 'long' in closed_dir else ('short' if 'short' in closed_dir else None)
                    # FR-004: 只对 is_strategy_stop=True 的 force_closed 计 SL hit;
                    # 风控/全平/价格失败/外部平仓未归因不污染策略 SL cooldown
                    is_strategy_stop = bool(payload.get('is_strategy_stop'))
                    if is_strategy_stop:
                        self._record_sl_hit(state, sl_dir)
                    cooldown = self._get_escalating_cooldown(state)
                    self._open_positions.discard(symbol)
                    self._position_slots.pop(symbol, None)
                    # Probe short tracking
                    if self._probe_short_active == symbol:
                        self._probe_short_active = None
                        if is_strategy_stop:
                            self._probe_short_sl_count += 1
                            if self._probe_short_sl_count >= 2:
                                self._probe_short_cooldown_until = time.time() + self._probe_short_cooldown_hours * 3600
                                self.logger.info(f"[Judge] probe_short 连续{self._probe_short_sl_count}次SL，冷却{self._probe_short_cooldown_hours}h")
                    self.logger.warning(
                        f"[Judge] {symbol} 强平冷却启动 ({payload.get('exit_reason', 'unknown')}),"
                        f"{cooldown}s内禁止同方向开仓 (active={len(self._open_positions)}) "
                        f"strategy_stop={is_strategy_stop}"
                    )
                elif status == 'closed_externally':
                    state["last_force_close_time"] = time.time()
                    state["deferred_entry"] = None
                    closed_dir = payload.get('direction', payload.get('action', ''))
                    sl_dir = 'long' if 'long' in closed_dir else ('short' if 'short' in closed_dir else None)
                    # PRD §6.2 #5 + §6.8 P1-d:外部平仓 pending 阶段(pnl_is_final=False)
                    # 不能计 SL hit;只有 Resolver final 后明确归因 exchange_sl 才计。
                    result_data = payload.get('result', {})
                    pnl_is_final = bool(result_data.get('pnl_is_final',
                                                         payload.get('pnl_is_final', False)))
                    is_strategy_stop = bool(payload.get('is_strategy_stop'))
                    if is_strategy_stop and pnl_is_final:
                        self._record_sl_hit(state, sl_dir)
                    elif is_strategy_stop and not pnl_is_final:
                        self.logger.info(
                            f"[Judge] {symbol} closed_externally pending,等 pnl_resolved 升级再计 SL hit")
                    cooldown = self._get_escalating_cooldown(state)
                    self._open_positions.discard(symbol)
                    self._position_slots.pop(symbol, None)
                    # PRD §6.2: probe_short SL tracking 必须等 pnl_is_final=True 才计;
                    # closed_externally pending PnL=None,会误判为 SL,要门控。
                    if self._probe_short_active == symbol:
                        result_data = payload.get('result', {})
                        pnl_is_final = bool(result_data.get('pnl_is_final',
                                                              payload.get('pnl_is_final', False)))
                        if not pnl_is_final:
                            self.logger.info(
                                f"[Judge] {symbol} probe_short pending PnL,等 pnl_resolved 升级再计 SL")
                        else:
                            self._probe_short_active = None
                            pnl = result_data.get('realized_pnl_net_usdt')
                            if pnl is None:
                                pnl = result_data.get('pnl', result_data.get('realized_pnl', -1))
                            exit_reason = result_data.get('exit_reason', result_data.get('reason', ''))
                            is_loss = (pnl is not None and pnl < 0) or \
                                exit_reason in ('sl', 'stop_loss', 'force_closed')
                            if is_loss:
                                self._probe_short_sl_count += 1
                                if self._probe_short_sl_count >= 2:
                                    self._probe_short_cooldown_until = time.time() + self._probe_short_cooldown_hours * 3600
                                    self.logger.info(f"[Judge] probe_short 连续{self._probe_short_sl_count}次SL(external)，冷却{self._probe_short_cooldown_hours}h")
                            else:
                                self._probe_short_sl_count = 0
                    self.logger.info(f"[Judge] {symbol} 被交易所平仓，清除延迟入场+冷却{cooldown}s (active={len(self._open_positions)})")
                elif status == 'executed' and action in ('open_long', 'open_short'):
                    state["last_open_time"] = time.time()
                    self._open_positions.add(symbol)
                    # Invalidate counterfactual shadows for this symbol
                    self._counterfactual_ledger.invalidate(symbol, "position_opened")
                    # Record slot type from attribution
                    slot_type = (payload.get('result', {}).get('attribution', {}).get('slot_type')
                                 or self._pending_open_slots.pop(symbol, 'main'))
                    self._position_slots[symbol] = slot_type
                    # Track probe_short
                    if payload.get('result', {}).get('attribution', {}).get('is_probe'):
                        self._probe_short_active = symbol
                    self.logger.info(f"[Judge] {symbol} 开仓成功 slot={slot_type} (active={len(self._open_positions)}/{self._max_concurrent_positions})")
                elif status == 'executed' and action == 'close':
                    self._open_positions.discard(symbol)
                    self._position_slots.pop(symbol, None)
                    # Probe short tracking: normal close (TP or manual) resets SL count
                    if self._probe_short_active == symbol:
                        self._probe_short_active = None
                        self._probe_short_sl_count = 0
                    self.logger.info(f"[Judge] {symbol} 主动平仓 (active={len(self._open_positions)})")

                # RQ-06: 记录交易结果到原型 cooldown
                # PRD §6.2: 仅 pnl_is_final=True 才能进入 archetype cooldown,
                # closed_externally pending pnl=None 不能污染 cooldown 决策
                result = payload.get('result', {})
                attribution = result.get('attribution') or payload.get('attribution', {})
                pnl_is_final = bool(result.get('pnl_is_final',
                                                 payload.get('pnl_is_final', False)))
                pnl = result.get('realized_pnl_net_usdt')
                if pnl is None:
                    pnl = result.get('pnl', 0)
                if pnl_is_final and pnl != 0 and attribution and \
                        status in ('executed', 'force_closed', 'closed_externally'):
                    archetype = self._archetype_cooldown.classify(attribution)
                    self._archetype_cooldown.record_result(archetype, pnl)
                self._state_dirty = True
                self._persist_state()
            return

        if msg['type'] == 'price_tick':
            if self._counterfactual_ledger._enabled:
                symbol = msg.get('symbol') or msg.get('payload', {}).get('symbol')
                price = msg.get('payload', {}).get('price', 0)
                if symbol and price > 0:
                    self._counterfactual_ledger.check_price(symbol, price, time.time())
            return

        if msg['type'] != 'tech_analysis':
            return

        symbol = msg.get('symbol') or msg['payload'].get('symbol')
        if not symbol:
            return

        state = self._get_state(symbol)
        now = time.time()
        if now - state["last_decision_time"] < self._decision_cooldown:
            return

        full_cooldown = self._get_escalating_cooldown(state)
        elapsed_since_sl = now - state["last_force_close_time"]
        # 反方向入场只需50%冷却时间（V型反转场景）
        if elapsed_since_sl < full_cooldown // 2:
            remaining = int(full_cooldown // 2 - elapsed_since_sl)
            self.logger.info(f"[Judge] {symbol} 强平冷却中(最小)，剩余{remaining}s (连续SL:{len(state.get('sl_timestamps',[]))}次)")
            return

        if now - state["last_open_time"] < 300:
            remaining = int(300 - (now - state["last_open_time"]))
            self.logger.info(f"[Judge] {symbol} 开仓冷却中，剩余{remaining}s")
            return

        state["last_tech"] = msg['payload']
        state["last_decision_time"] = now

        # Update regime from all cached tech snapshots
        self._symbol_tech_cache[symbol] = msg['payload']
        self._regime_manager.update(self._symbol_tech_cache)

        # Check counterfactual ledger shadow positions
        if self._counterfactual_ledger._enabled:
            price = msg['payload'].get('indicators', {}).get('price', 0)
            if price > 0:
                self._counterfactual_ledger.check_price(symbol, price, now)

        await self._make_decision(symbol, msg['payload'])
        self._state_dirty = True
        self._persist_state()

    async def _make_decision(self, symbol: str, tech: dict):
        await self._update_balance()
        # 决策磁带：本次决策的 LLM 输出按 symbol 重置，rule-only 路径保持 None（诚实）
        if hasattr(self, "_symbol_llm_cache"):
            self._symbol_llm_cache[symbol] = None
        if hasattr(self, "_symbol_tech_tape_cache"):
            self._symbol_tech_tape_cache[symbol] = tech

        # 数据质量门槛：降级数据下不开仓（dimensions_ok < 6/9）
        data_quality = tech.get('data_quality', {})
        if data_quality.get('degraded'):
            self.logger.warning(
                f"[Judge] {symbol} 数据降级 "
                f"{data_quality.get('dimensions_ok', '?')}/{data_quality.get('dimensions_total', 9)}，"
                f"强制 hold"
            )
            await self.publish("trade_decision", {
                "symbol": symbol,
                "decision": "hold",
                "confidence": 0,
                "reason": "data_degraded",
                "data_quality": data_quality,
            }, symbol=symbol)
            return

        # ═══ P2-O: 并发持仓上限保护（按 slot 类型分开记账）═══
        self._sweep_stale_pending()
        occupied = self._open_positions | self._pending_open_symbols
        if symbol not in occupied:
            # Count main slots (exclude low_rr_extra and probe_short)
            all_slots = {**self._position_slots, **self._pending_open_slots}
            main_count = sum(1 for s in occupied if all_slots.get(s, 'main') == 'main')
            low_rr_count = sum(1 for s in occupied if all_slots.get(s) == 'low_rr_extra')
            probe_count = sum(1 for s in occupied if all_slots.get(s) == 'probe_short')

            # low_rr_extra has its own cap (from ranker config)
            low_rr_extra_cap = self._candidate_ranker.low_rr_extra_slot if hasattr(self, '_candidate_ranker') else 1
            probe_cap = self._probe_short_max_concurrent

            # Block only if ALL slot types are full
            if main_count >= self._max_concurrent_positions:
                # Main full — still allow low_rr_extra or probe if their caps aren't hit
                # (actual slot_type assignment happens later in _apply_regime_policy)
                if low_rr_count >= low_rr_extra_cap and probe_count >= probe_cap:
                    self.logger.info(
                        f"[Judge] {symbol} 所有槽位已满 "
                        f"(main={main_count}/{self._max_concurrent_positions}, "
                        f"low_rr={low_rr_count}/{low_rr_extra_cap}, "
                        f"probe={probe_count}/{probe_cap})，跳过新开仓"
                    )
                    await self.publish("trade_decision", {
                        "symbol": symbol, "timestamp": time.time(),
                        "action": "hold", "confidence": 0,
                        "plan": None, "size_pct": 0,
                        "reasoning": f"并发持仓已达上限(main={main_count},low_rr={low_rr_count},probe={probe_count})",
                        "key_factors": [f"active_positions={sorted(self._open_positions)}"],
                        "risk_warnings": ["concurrent_limit_reached"],
                    }, symbol=symbol)
                    return

        # 回调入场检查：之前因RSI超买/超卖被拒，现在RSI回落到合理区间
        state = self._get_state(symbol)
        rsi = tech.get('momentum', {}).get('rsi', 50)
        pending = state.get('pending_pullback')
        if pending:
            pullback_age = time.time() - state.get('pending_pullback_time', 0)
            if pullback_age > 4 * 3600:
                state['pending_pullback'] = None
            elif pending == 'long' and rsi < 65:
                self.logger.info(f"[Judge] {symbol} RSI回落至{rsi:.0f}，回调入场触发")
                state['pending_pullback'] = None
                state['pullback_bonus'] = 15
            elif pending == 'short' and rsi > 35:
                self.logger.info(f"[Judge] {symbol} RSI回升至{rsi:.0f}，回调入场触发")
                state['pending_pullback'] = None
                state['pullback_bonus'] = -15

        # 延迟入场检查（R:R不足时等待回调）
        deferred = state.get('deferred_entry')
        if deferred:
            current_price = tech.get('indicators', {}).get('price', 0)
            age_seconds = time.time() - deferred['created_at']
            def_action = deferred['action']
            target = deferred['target_price']
            is_long = (def_action == 'open_long')

            if is_long:
                deferred['lowest_since'] = min(deferred['lowest_since'], current_price)
            else:
                deferred['highest_since'] = max(deferred['highest_since'], current_price)

            # RQ-15M-04: deferred_15m_confirmation 类型 — 等待 15m 转向而非价格回调
            if deferred.get('entry_type') == 'deferred_15m_confirmation':
                timeout_hours = deferred.get('timeout_hours', self._15m_timeout_hours)
                if age_seconds > timeout_hours * 3600:
                    self.logger.info(f"[Judge] {symbol} 15m确认等待超时({age_seconds/3600:.1f}h/{timeout_hours}h)，放弃")
                    state['deferred_entry'] = None
                    return
                # 检查 15m 是否已转为顺向/中性
                timing_15m_fail = self._check_15m_deferred_reconfirm(tech, def_action)
                if timing_15m_fail:
                    return  # 静默等待，不发布 hold（避免刷屏）
                # 15m 已顺向，重新走 plan → 风控 → 发布
                self.logger.info(f"[Judge] {symbol} 15m已转向确认，重新构建plan")
                reconfirm_fail = self._reconfirm_deferred(
                    symbol, tech, def_action, deferred.get('signal_score', 50),
                    context='deferred_15m_confirmation'
                )
                if reconfirm_fail:
                    self.logger.info(f"[Judge] {symbol} 15m确认后HTF二次确认失败: {reconfirm_fail}")
                    state['deferred_entry'] = None
                    await self._publish_hold(symbol, reconfirm_fail, [reconfirm_fail])
                    return
                plan = self._build_plan(tech, def_action, current_price, 60, deferred.get('signal_score', 50))
                if plan['size_usdt'] < 1.0:
                    self.logger.info(f"[Judge] {symbol} 15m确认入场余额不足，放弃")
                    state['deferred_entry'] = None
                    return
                new_rr = plan.get('effective_risk_reward_ratio', plan.get('risk_reward_ratio', 0))
                if new_rr < 1.2:
                    self.logger.info(f"[Judge] {symbol} 15m确认入场R:R={new_rr:.2f}<1.2，放弃")
                    state['deferred_entry'] = None
                    return
                if not self._check_expected_value(symbol, plan, deferred.get('signal_score', 50)):
                    self.logger.info(f"[Judge] {symbol} 15m确认入场EV<0，放弃")
                    state['deferred_entry'] = None
                    return
                # Regime policy: short guard + dynamic R:R floor + low R:R scaling
                regime_reject = self._apply_regime_policy(
                    symbol, def_action, plan, deferred.get('signal_score', 50), tech
                )
                if regime_reject:
                    self.logger.info(f"[Judge] {symbol} 15m确认入场被regime policy拦截: {regime_reject}")
                    state['deferred_entry'] = None
                    await self._publish_hold(symbol, regime_reject, [regime_reject])
                    return
                # ═══ Entry Position Guard (AC-LONGPOS-09 一致性) ═══
                pos_policy = self._check_entry_position_policy(
                    symbol, def_action, plan, tech, deferred.get('signal_score', 50),
                    context='deferred_15m_confirmation'
                )
                if not pos_policy['allowed']:
                    block_reason = pos_policy['block_reason']
                    self.logger.info(
                        f"[Judge] {symbol} 15m确认入场被 entry_position_guard 拦截: {block_reason}"
                    )
                    state['deferred_entry'] = None
                    await self._publish_hold(symbol, block_reason, [block_reason])
                    return
                # P1-2: 统一走 open quality gate
                reject_reason = self._open_quality_rejection(
                    symbol, tech, def_action, deferred.get('signal_score', 50),
                    confidence=60, context='deferred_15m_confirmation'
                )
                if reject_reason:
                    self.logger.info(f"[Judge] {symbol} 15m确认入场被质量门拦截: {reject_reason}")
                    state['deferred_entry'] = None
                    await self._publish_hold(symbol, reject_reason, [reject_reason])
                    return
                # Short gate check for deferred_15m_confirmation
                if def_action == 'open_short':
                    short_gate = self._classify_short_entry_risk(
                        symbol, def_action, plan, tech, deferred.get('signal_score', 50),
                        llm_result=None,
                    )
                    if not short_gate['allowed']:
                        block_reason = short_gate['reason']
                        self.logger.info(f"[Judge] {symbol} deferred_15m short gate blocked: {block_reason}")
                        state['deferred_entry'] = None
                        await self._publish_hold(symbol, block_reason, [block_reason])
                        return

                plan['order_type'] = 'market'
                plan['entry_type'] = 'deferred_15m_confirmation'
                # P1-2: 统一构建 attribution
                attribution = self._build_attribution(
                    tech, def_action, deferred.get('signal_score', 50),
                    plan, None, 'deferred_15m_confirmation'
                )
                attribution['tf_15m_wait_seconds'] = int(age_seconds)
                # Apply short gate attribution if short
                if def_action == 'open_short':
                    attribution = self._apply_short_gate_attribution(attribution, short_gate)
                plan['attribution'] = attribution
                state['deferred_entry'] = None
                decision = {
                    "symbol": symbol, "timestamp": time.time(),
                    "action": def_action, "confidence": 60,
                    "plan": plan, "size_pct": 1.0,
                    "reasoning": f"15m确认入场：等待{age_seconds/60:.0f}min后15m转向确认",
                    "key_factors": ["deferred_15m_confirmed"],
                    "risk_warnings": [],
                    "entry_type": "deferred_15m_confirmation",
                    "attribution": attribution,
                }
                published = await self._gate_and_publish_open(symbol, decision, state)
                if not published:
                    self.logger.info(f"[Judge] {symbol} deferred_15m open rejected by slot gate")
                return

            pullback_hit = (is_long and current_price <= target) or \
                           (not is_long and current_price >= target)
            if pullback_hit:
                self.logger.info(f"[Judge] {symbol} 回调到位 price={current_price:.4f} target={target:.4f}，重新构建plan")
                # RQ-02: 二次确认 — HTF/趋势/流动性
                reconfirm_fail = self._reconfirm_deferred(
                    symbol, tech, def_action, deferred.get('signal_score', 50),
                    context='deferred_pullback'
                )
                if reconfirm_fail:
                    self.logger.info(f"[Judge] {symbol} 回调入场二次确认失败: {reconfirm_fail}")
                    state['deferred_entry'] = None
                    await self._publish_hold(symbol, reconfirm_fail, [reconfirm_fail])
                    return
                # RQ-15M-05: deferred 触发时 15m 二次确认
                timing_15m_fail = self._check_15m_deferred_reconfirm(tech, def_action)
                if timing_15m_fail:
                    self.logger.info(f"[Judge] {symbol} 回调入场15m确认失败: {timing_15m_fail}")
                    # 不清除 deferred，保留等待 15m 转向
                    await self._publish_hold(symbol, timing_15m_fail, [timing_15m_fail])
                    return
                # RSI入场禁令：deferred路径同样适用
                if def_action == 'open_short' and rsi <= 30:
                    self.logger.info(f"[Judge] {symbol} 回调入场时RSI={rsi:.0f}<=30超卖，禁止做空")
                    state['deferred_entry'] = None
                    await self._publish_hold(symbol, f"RSI={rsi:.0f}超卖禁止做空", [f"RSI={rsi:.0f}超卖"])
                    return
                if def_action == 'open_long' and rsi >= 70:
                    self.logger.info(f"[Judge] {symbol} 回调入场时RSI={rsi:.0f}>=70超买，禁止做多")
                    state['deferred_entry'] = None
                    await self._publish_hold(symbol, f"RSI={rsi:.0f}超买禁止做多", [f"RSI={rsi:.0f}超买"])
                    return
                plan = self._build_plan(tech, def_action, current_price, 60, deferred.get('signal_score', 50))
                if plan['size_usdt'] < 1.0:
                    self.logger.info(f"[Judge] {symbol} 回调入场时余额不足，放弃")
                    state['deferred_entry'] = None
                    return
                new_rr = plan.get('effective_risk_reward_ratio', plan.get('risk_reward_ratio', 0))
                if new_rr < 1.2:
                    self.logger.info(f"[Judge] {symbol} 回调入场重算R:R={new_rr:.2f}<1.2，放弃")
                    state['deferred_entry'] = None
                    return
                reject_reason = self._open_quality_rejection(
                    symbol, tech, def_action, deferred.get('signal_score', 50),
                    confidence=60, context='deferred_pullback'
                )
                if reject_reason:
                    self.logger.info(f"[Judge] {symbol} 回调入场被质量门拦截: {reject_reason}")
                    state['deferred_entry'] = None
                    await self._publish_hold(symbol, reject_reason, [reject_reason])
                    return
                # EV 门（回调触发同样适用）
                if not self._check_expected_value(symbol, plan, deferred.get('signal_score', 50)):
                    self.logger.info(f"[Judge] {symbol} 回调入场 EV<0，放弃")
                    state['deferred_entry'] = None
                    return
                # Regime policy: short guard + dynamic R:R floor + low R:R scaling
                regime_reject = self._apply_regime_policy(
                    symbol, def_action, plan, deferred.get('signal_score', 50), tech
                )
                if regime_reject:
                    self.logger.info(f"[Judge] {symbol} 回调入场被regime policy拦截: {regime_reject}")
                    state['deferred_entry'] = None
                    await self._publish_hold(symbol, regime_reject, [regime_reject])
                    return
                # ═══ Entry Position Guard（AC-LONGPOS-08：deferred 二次入场必须重过 guard） ═══
                pos_policy = self._check_entry_position_policy(
                    symbol, def_action, plan, tech, deferred.get('signal_score', 50),
                    context='deferred_pullback'
                )
                if not pos_policy['allowed']:
                    block_reason = pos_policy['block_reason']
                    self.logger.info(
                        f"[Judge] {symbol} 回调入场被 entry_position_guard 拦截: {block_reason}"
                    )
                    state['deferred_entry'] = None
                    await self._publish_hold(symbol, block_reason, [block_reason])
                    return
                # Short gate check for deferred_pullback
                if def_action == 'open_short':
                    short_gate = self._classify_short_entry_risk(
                        symbol, def_action, plan, tech, deferred.get('signal_score', 50),
                        llm_result=None,
                    )
                    if not short_gate['allowed']:
                        block_reason = short_gate['reason']
                        self.logger.info(f"[Judge] {symbol} deferred_pullback short gate blocked: {block_reason}")
                        state['deferred_entry'] = None
                        await self._publish_hold(symbol, block_reason, [block_reason])
                        return

                plan['order_type'] = 'limit'
                # Carry overheat tag through if this was an overheat-triggered deferred.
                if deferred.get('entry_type') == 'deferred_pullback_overheat':
                    plan['entry_type'] = 'deferred_pullback_overheat'
                    attribution_label = 'deferred_pullback_overheat'
                else:
                    plan['entry_type'] = 'deferred_pullback'
                    attribution_label = 'deferred_pullback'
                attribution = self._build_attribution(
                    tech, def_action, deferred.get('signal_score', 50),
                    plan, None, attribution_label
                )
                # Apply short gate attribution if short
                if def_action == 'open_short':
                    attribution = self._apply_short_gate_attribution(attribution, short_gate)
                plan['attribution'] = attribution
                state['deferred_entry'] = None
                decision = {
                    "symbol": symbol, "timestamp": time.time(),
                    "action": def_action, "confidence": 60,
                    "plan": plan, "size_pct": 1.0,
                    "reasoning": f"回调入场触发：目标价{target:.4f}达到，R:R={new_rr:.2f}",
                    "key_factors": ["deferred_pullback_filled"],
                    "risk_warnings": [],
                    "entry_type": attribution_label,
                    "attribution": attribution,
                }
                published = await self._gate_and_publish_open(symbol, decision, state)
                if not published:
                    self.logger.info(f"[Judge] {symbol} deferred_pullback open rejected by slot gate")
                return

            signal_price = deferred['signal_price']
            if is_long:
                move_pct = (current_price - signal_price) / signal_price
            else:
                move_pct = (signal_price - current_price) / signal_price

            if move_pct > 0.015 and deferred.get('chase_eligible'):
                # RQ-02: 追价入场二次确认
                reconfirm_fail = self._reconfirm_deferred(
                    symbol, tech, def_action, deferred.get('signal_score', 50),
                    context='deferred_chase'
                )
                if reconfirm_fail:
                    self.logger.info(f"[Judge] {symbol} 追价入场二次确认失败: {reconfirm_fail}")
                    state['deferred_entry'] = None
                    await self._publish_hold(symbol, reconfirm_fail, [reconfirm_fail])
                    return
                # RQ-15M-05: 追价入场 15m 确认
                timing_15m_fail = self._check_15m_deferred_reconfirm(tech, def_action)
                if timing_15m_fail:
                    self.logger.info(f"[Judge] {symbol} 追价入场15m确认失败: {timing_15m_fail}")
                    await self._publish_hold(symbol, timing_15m_fail, [timing_15m_fail])
                    return
                # RSI入场禁令：chase路径同样适用
                if def_action == 'open_short' and rsi <= 30:
                    self.logger.info(f"[Judge] {symbol} 追价入场时RSI={rsi:.0f}<=30超卖，禁止做空")
                    state['deferred_entry'] = None
                    await self._publish_hold(symbol, f"RSI={rsi:.0f}超卖禁止做空", [f"RSI={rsi:.0f}超卖"])
                    return
                if def_action == 'open_long' and rsi >= 70:
                    self.logger.info(f"[Judge] {symbol} 追价入场时RSI={rsi:.0f}>=70超买，禁止做多")
                    state['deferred_entry'] = None
                    await self._publish_hold(symbol, f"RSI={rsi:.0f}超买禁止做多", [f"RSI={rsi:.0f}超买"])
                    return
                plan = self._build_plan(tech, def_action, current_price, 60, deferred.get('signal_score', 50))
                plan['size_usdt'] = round(plan['size_usdt'] * 0.6, 2)
                if plan['size_usdt'] < 1.0:
                    self.logger.info(f"[Judge] {symbol} 追价入场时余额不足，放弃")
                    state['deferred_entry'] = None
                    return
                reject_reason = self._open_quality_rejection(
                    symbol, tech, def_action, deferred.get('signal_score', 50),
                    confidence=60, context='deferred_chase'
                )
                if reject_reason:
                    self.logger.info(f"[Judge] {symbol} 追价入场被质量门拦截: {reject_reason}")
                    state['deferred_entry'] = None
                    await self._publish_hold(symbol, reject_reason, [reject_reason])
                    return
                # EV 门（追价同样适用——价格已移动 R:R 通常更差）
                if not self._check_expected_value(symbol, plan, deferred.get('signal_score', 50)):
                    self.logger.info(f"[Judge] {symbol} 追价入场 EV<0，放弃")
                    state['deferred_entry'] = None
                    return
                # Regime policy: short guard + dynamic R:R floor + low R:R scaling
                regime_reject = self._apply_regime_policy(
                    symbol, def_action, plan, deferred.get('signal_score', 50), tech
                )
                if regime_reject:
                    self.logger.info(f"[Judge] {symbol} 追价入场被regime policy拦截: {regime_reject}")
                    state['deferred_entry'] = None
                    await self._publish_hold(symbol, regime_reject, [regime_reject])
                    return
                # ═══ Entry Position Guard (AC-LONGPOS-09 一致性) ═══
                pos_policy = self._check_entry_position_policy(
                    symbol, def_action, plan, tech, deferred.get('signal_score', 50),
                    context='deferred_chase'
                )
                if not pos_policy['allowed']:
                    block_reason = pos_policy['block_reason']
                    self.logger.info(
                        f"[Judge] {symbol} 追价入场被 entry_position_guard 拦截: {block_reason}"
                    )
                    state['deferred_entry'] = None
                    await self._publish_hold(symbol, block_reason, [block_reason])
                    return
                # Short gate check for deferred_chase
                if def_action == 'open_short':
                    short_gate = self._classify_short_entry_risk(
                        symbol, def_action, plan, tech, deferred.get('signal_score', 50),
                        llm_result=None,
                    )
                    if not short_gate['allowed']:
                        block_reason = short_gate['reason']
                        self.logger.info(f"[Judge] {symbol} deferred_chase short gate blocked: {block_reason}")
                        state['deferred_entry'] = None
                        await self._publish_hold(symbol, block_reason, [block_reason])
                        return

                # RQ-02: chase 有效 RR 约束
                chase_rr = plan.get('effective_risk_reward_ratio', plan.get('risk_reward_ratio', 0))
                if chase_rr < self._rr_floor_default:
                    self.logger.info(f"[Judge] {symbol} 追价入场 R:R={chase_rr:.2f}<{self._rr_floor_default}，放弃")
                    state['deferred_entry'] = None
                    return
                plan['order_type'] = 'market'
                plan['entry_type'] = 'deferred_chase'
                attribution = self._build_attribution(
                    tech, def_action, deferred.get('signal_score', 50),
                    plan, None, 'deferred_chase'
                )
                attribution['chase_move_pct'] = round(move_pct, 4)
                # Apply short gate attribution if short
                if def_action == 'open_short':
                    attribution = self._apply_short_gate_attribution(attribution, short_gate)
                plan['attribution'] = attribution
                state['deferred_entry'] = None
                self.logger.info(f"[Judge] {symbol} 价格已移动{move_pct:.1%}无回调，追价入场（仓位60%）")
                decision = {
                    "symbol": symbol, "timestamp": time.time(),
                    "action": def_action, "confidence": 60,
                    "plan": plan, "size_pct": 0.6,
                    "reasoning": f"追价入场：价格移动{move_pct:.1%}无回调，R:R={chase_rr:.2f}，缩仓60%补偿",
                    "key_factors": ["chase_entry_no_pullback"],
                    "risk_warnings": [f"chase R:R={chase_rr:.2f}"],
                    "entry_type": "deferred_chase",
                    "attribution": attribution,
                }
                published = await self._gate_and_publish_open(symbol, decision, state)
                if not published:
                    self.logger.info(f"[Judge] {symbol} deferred_chase open rejected by slot gate")
                return

            # 动态超时：ma_aligned持续时间长的信号允许更长等待
            timeout_hours = deferred.get('timeout_hours', 3)
            if age_seconds > timeout_hours * 3600:
                self.logger.info(f"[Judge] {symbol} 延迟入场过期（{age_seconds/3600:.1f}h/{timeout_hours}h），放弃")
                state['deferred_entry'] = None
                return

            trend_dir = tech.get('trend', {}).get('direction', 'neutral')
            if (is_long and trend_dir == 'bearish') or (not is_long and trend_dir == 'bullish'):
                self.logger.info(f"[Judge] {symbol} 趋势反转({trend_dir})，取消延迟入场")
                state['deferred_entry'] = None
                return

            state['pullback_bonus'] = 0
            return

        score = self._compute_score(tech)

        # 回调入场bonus
        state = self._get_state(symbol)
        pullback_bonus = state.get('pullback_bonus', 0)
        if pullback_bonus != 0:
            score += pullback_bonus
            state['pullback_bonus'] = 0
            self.logger.info(f"[Judge] {symbol} 回调入场bonus={pullback_bonus:+d}")

        self.logger.info(f"[Judge] {symbol} 原始score={score:.1f} RSI={tech.get('momentum',{}).get('rsi',0):.0f}")
        price = tech.get('indicators', {}).get('price', 0)

        # 趋势持续性加分：连续多轮同方向强趋势时累加
        trend = tech.get('trend', {})
        state = self._get_state(symbol)
        cur_dir = trend.get('direction', 'neutral')
        cur_strength = trend.get('strength', 50)
        if cur_dir in ('bullish', 'bearish') and cur_strength >= 75:
            if cur_dir == state.get('trend_streak_dir'):
                state['trend_streak'] += 1
            else:
                state['trend_streak'] = 1
                state['trend_streak_dir'] = cur_dir
        else:
            state['trend_streak'] = 0
            state['trend_streak_dir'] = None

        if state['trend_streak'] >= 5:
            streak_bonus = 10
            if cur_dir == 'bearish':
                score -= streak_bonus
            else:
                score += streak_bonus
            self.logger.info(f"[Judge] {symbol} 趋势持续{state['trend_streak']}轮({cur_dir}/{cur_strength})，加分±{streak_bonus}")

        # 日线阻力区反欺骗：分级衰减（放量突破不衰减，缩量接近强衰减）
        vol_ratio = tech.get('momentum', {}).get('volume_ratio', 1.0)
        if score > 0 and trend.get('daily_near_resistance'):
            if vol_ratio >= 2.0:
                pass  # 放量突破确认，不衰减
                self.logger.info(f"[Judge] {symbol} 接近日线阻力但放量(vol={vol_ratio:.1f}x)，视为突破确认")
            elif vol_ratio >= 1.2:
                score *= 0.5  # 正常量接近，中等衰减
                self.logger.info(f"[Judge] {symbol} 接近日线阻力区，做多信号衰减50%")
            else:
                score *= 0.3  # 缩量接近，强衰减（假突破概率高）
                self.logger.info(f"[Judge] {symbol} 缩量接近日线阻力区，做多信号衰减70%（假突破）")
        # 日线支撑区：同理分级
        if score < 0 and trend.get('daily_near_support'):
            if vol_ratio >= 2.0:
                pass
                self.logger.info(f"[Judge] {symbol} 接近日线支撑但放量(vol={vol_ratio:.1f}x)，视为突破确认")
            elif vol_ratio >= 1.2:
                score *= 0.5
                self.logger.info(f"[Judge] {symbol} 接近日线支撑区，做空信号衰减50%")
            else:
                score *= 0.3
                self.logger.info(f"[Judge] {symbol} 缩量接近日线支撑区，做空信号衰减70%（反弹陷阱）")

        # price-in衰减：催化剂已被价格消化，信号可靠性下降
        action_hint = "open_long" if score > 0 else "open_short"
        if abs(score) >= 30 and self._check_price_in(symbol, action_hint, tech):
            score *= 0.5

        # 方向级冷却：同方向需完整冷却，反方向已在早期gate放行（50%冷却）
        state = self._get_state(symbol)
        now = time.time()
        full_cooldown = self._get_escalating_cooldown(state)
        elapsed_since_sl = now - state.get("last_force_close_time", 0)
        if elapsed_since_sl < full_cooldown:
            signal_dir = 'long' if score > 0 else 'short'
            last_sl_dir = state.get('last_sl_direction')
            if last_sl_dir and signal_dir == last_sl_dir:
                remaining = int(full_cooldown - elapsed_since_sl)
                self.logger.info(f"[Judge] {symbol} 同方向({signal_dir})冷却中，剩余{remaining}s")
                decision = {
                    "symbol": symbol, "timestamp": time.time(),
                    "action": "hold", "confidence": 0,
                    "plan": None, "size_pct": 0,
                    "reasoning": f"同方向SL冷却中({remaining}s)",
                    "key_factors": [], "risk_warnings": [],
                }
                await self.publish("trade_decision", decision, symbol=symbol)
                return

        # 入场门槛：有主驱动(rule_signal/ma_aligned)时25分，HTF一致时35分，完全无驱动时40分
        rule = tech.get('rule_signal', {})
        has_rule_signal = rule.get('entry_long') or rule.get('entry_short') or \
                         rule.get('ma_aligned_long') or rule.get('ma_aligned_short')
        htf_bias = tech.get('trend', {}).get('higher_tf_bias', 'neutral')
        if has_rule_signal:
            entry_threshold = 25
        elif htf_bias in ('bullish', 'bearish'):
            entry_threshold = 35
        else:
            entry_threshold = 40

        if abs(score) < entry_threshold:
            hold_reason = self._hold_reason(tech, score)
            decision = {
                "symbol": symbol, "timestamp": time.time(),
                "action": "hold", "confidence": 50 - abs(score),
                "plan": None, "size_pct": 0,
                "reasoning": hold_reason,
                "key_factors": [], "risk_warnings": [],
            }
            if self._confidence_split_enabled:
                decision['signal_score'] = round(score, 1)
                decision['execution_confidence'] = 0
                decision['position_scale'] = 0.0
                decision['hold_reason'] = 'score_below_threshold'
        else:
            action = "open_long" if score > 0 else "open_short"
            confidence = min(95, abs(score))
            plan = self._build_plan(tech, action, price, confidence, score)

            llm_result = await self._ask_llm(symbol, tech, score)
            if hasattr(self, "_symbol_llm_cache"):
                self._symbol_llm_cache[symbol] = llm_result

            # LLM作为修正因子，不作为否决权
            # rule_signal触发时（score含±35基础分），LLM只能降低仓位，不能阻止入场

            if llm_result.get('action') == 'hold' and not has_rule_signal and confidence < 55:
                decision = {
                    "symbol": symbol, "timestamp": time.time(),
                    "action": "hold", "confidence": llm_result.get('confidence', 40),
                    "plan": None, "size_pct": 0,
                    "reasoning": llm_result.get('reasoning', ''),
                    "key_factors": llm_result.get('key_factors', []),
                    "risk_warnings": llm_result.get('risk_warnings', []),
                }
                if self._confidence_split_enabled:
                    decision['signal_score'] = round(score, 1)
                    decision['execution_confidence'] = 0
                    decision['position_scale'] = 0.0
                    decision['hold_reason'] = 'llm_hold_weak_signal'
            else:
                final_action = action  # rule_signal有时，锁定方向
                if not has_rule_signal:
                    final_action = llm_result.get('action', action)
                    if final_action not in ('open_long', 'open_short', 'close', 'hold'):
                        final_action = action

                if final_action != action and final_action in ('open_long', 'open_short'):
                    plan = self._build_plan(tech, final_action, price, confidence, score)

                final_conf = llm_result.get('confidence', confidence)

                # 无主驱动时，LLM confidence上限65（多维度共振时允许更高置信度）
                if not has_rule_signal:
                    final_conf = min(65, final_conf)

                # LLM方向确认/冲突处理
                llm_action = llm_result.get('action', 'hold')
                if llm_action == final_action and final_conf < 70:
                    # 有rule_signal时boost到70，无rule_signal时boost到65
                    boost_target = 70 if has_rule_signal else 65
                    self.logger.info(f"[Judge] {symbol} LLM确认{llm_action}方向，confidence提升至{boost_target}")
                    final_conf = boost_target
                elif llm_action in ('open_long', 'open_short') and llm_action != final_action:
                    final_conf = max(30, int(final_conf * 0.5))
                    self.logger.warning(f"[Judge] {symbol} LLM建议{llm_action}与规则方向{final_action}冲突，confidence衰减至{final_conf}")

                # LLM反对但rule_signal触发：降低仓位30%而非阻止入场
                if has_rule_signal and llm_action == 'hold':
                    if self._confidence_split_enabled and self._is_htf_aligned(tech, action):
                        # Phase 2: rule+HTF aligned → LLM hold只降仓, 不降到executor门槛以下
                        final_conf = max(60, int(confidence * 0.7))
                    else:
                        final_conf = max(40, int(confidence * 0.7))
                    self.logger.info(f"[Judge] {symbol} rule_signal触发但LLM观望，仓位衰减30%")
                # LLM给出反向开仓建议：比hold更强的冲突信号，衰减60%
                elif has_rule_signal and llm_action in ('open_long', 'open_short') and llm_action != action:
                    if self._confidence_split_enabled and self._is_htf_aligned(tech, action):
                        # Phase 2: rule+HTF aligned → 即使LLM反向也不硬拒, 但大幅缩仓
                        final_conf = max(60, int(confidence * 0.4))
                    else:
                        final_conf = max(30, int(confidence * 0.4))
                    self.logger.warning(f"[Judge] {symbol} rule_signal={action}但LLM建议反向{llm_action}，强冲突衰减60%，confidence={final_conf}")

                if final_action in ('open_long', 'open_short'):
                    reject_reason = self._open_quality_rejection(
                        symbol, tech, final_action, score, final_conf,
                        context='live_open'
                    )
                    if reject_reason:
                        self.logger.info(f"[Judge] {symbol} 实盘开仓质量门拦截: {reject_reason}")
                        self._record_rejected_plan(symbol, final_action, plan, score, final_conf, f"quality_gate:{reject_reason}")
                        decision = {
                            "symbol": symbol, "timestamp": time.time(),
                            "action": "hold", "confidence": 0,
                            "plan": None, "size_pct": 0,
                            "reasoning": reject_reason,
                            "key_factors": llm_result.get('key_factors', []),
                            "risk_warnings": [reject_reason],
                            "attribution": self._rejection_attribution(final_action, plan, f"quality_gate:{reject_reason}", tech=tech),
                        }
                        await self.publish("trade_decision", decision, symbol=symbol)
                        return

                    # ═══ Short Regime Guard (Phase 1B) ═══
                    regime_snap = self._regime_manager.snapshot()
                    if (final_action == 'open_short' and self._short_regime_guard_enabled
                            and regime_snap['effective_regime'] == 'bullish'):
                        trend = tech.get('trend', {})
                        entry_timing = tech.get('entry_timing', {})
                        htf_bearish = 0
                        for d in [trend.get('direction'), trend.get('higher_tf_bias'), trend.get('daily_bias')]:
                            if d == 'bearish':
                                htf_bearish += 1
                        rr_for_guard = plan.get('effective_risk_reward_ratio', plan.get('risk_reward_ratio', 0))
                        confirm_15m_short = entry_timing.get('tf_15m_confirm_short', False)
                        allowed, guard_reason = self._regime_manager.is_short_allowed(
                            score, htf_bearish, rr_for_guard, confirm_15m_short,
                            trend.get('daily_bias', 'neutral')
                        )
                        if not allowed:
                            probe_ok, probe_reason = self._can_route_probe_short(symbol, score, confirm_15m_short, rr_for_guard)
                            if probe_ok:
                                self._route_to_probe(plan, symbol)
                                self.logger.info(
                                    f"[Judge] {symbol} probe_short: size={plan['size_usdt']} lev={plan['leverage']}x"
                                )
                            else:
                                # Record to counterfactual ledger and reject
                                self._record_rejected_plan(
                                    symbol, final_action, plan, score, final_conf,
                                    f"short_regime_guard:{guard_reason}"
                                )
                                self.logger.info(
                                    f"[Judge] {symbol} short blocked by regime guard "
                                    f"(regime={regime_snap['effective_regime']}, score={score:.0f})"
                                )
                                decision = {
                                    "symbol": symbol, "timestamp": time.time(),
                                    "action": "hold", "confidence": 0,
                                    "plan": None, "size_pct": 0,
                                    "reasoning": f"Short blocked: bullish regime, score={score:.0f} (need ≤-70)",
                                    "key_factors": [f"blocked_by={guard_reason}"],
                                    "risk_warnings": ["short_regime_guard"],
                                    "attribution": {
                                        "entry_regime": regime_snap['effective_regime'],
                                        "blocked_by": guard_reason,
                                    },
                                }
                                await self.publish("trade_decision", decision, symbol=symbol)
                                return
                        elif guard_reason == 'degrade_to_probe':
                            probe_ok, probe_reason = self._can_route_probe_short(symbol, score, confirm_15m_short, rr_for_guard)
                            if probe_ok:
                                self._route_to_probe(plan, symbol)
                                self.logger.info(
                                    f"[Judge] {symbol} strong short degraded to probe (daily=bullish): "
                                    f"size={plan['size_usdt']} lev={plan['leverage']}x"
                                )
                            else:
                                self._record_rejected_plan(
                                    symbol, final_action, plan, score, final_conf,
                                    f"degrade_to_probe:{probe_reason}"
                                )
                                self.logger.info(
                                    f"[Judge] {symbol} degrade_to_probe rejected: probe unavailable"
                                )
                                decision = {
                                    "symbol": symbol, "timestamp": time.time(),
                                    "action": "hold", "confidence": 0,
                                    "plan": None, "size_pct": 0,
                                    "reasoning": "Strong short degraded to probe but probe unavailable",
                                    "key_factors": ["degrade_to_probe:probe_unavailable"],
                                    "risk_warnings": ["short_regime_guard"],
                                    "attribution": {
                                        "entry_regime": regime_snap['effective_regime'],
                                        "blocked_by": "degrade_to_probe:probe_unavailable",
                                    },
                                }
                                await self.publish("trade_decision", decision, symbol=symbol)
                                return

                    # RSI超买/超卖硬性入场禁令：不追高不追低，标记待回调
                    rsi = tech.get('momentum', {}).get('rsi', 50)
                    if final_action == 'open_long' and rsi >= 70:
                        # Phase 2 EPIC B: momentum probe_long (RSI 70-85, 强趋势追随)
                        probe_ok, probe_reason = self._can_route_probe_long(symbol, tech, score)
                        if probe_ok:
                            self._route_to_probe_long(plan, symbol)
                            self.logger.info(
                                f"[Judge] {symbol} RSI={rsi:.0f} momentum_probe_long: "
                                f"size={plan['size_usdt']} lev={plan['leverage']}x"
                            )
                        else:
                            self.logger.info(f"[Judge] {symbol} RSI={rsi:.0f}>=70超买，禁止追多，标记待回调 (probe: {probe_reason})")
                            state = self._get_state(symbol)
                            state['pending_pullback'] = 'long'
                            state['pending_pullback_time'] = time.time()
                            decision = {
                                "symbol": symbol, "timestamp": time.time(),
                                "action": "hold", "confidence": 0,
                                "plan": None, "size_pct": 0,
                                "reasoning": f"RSI={rsi:.0f}超买，等待回调至65以下再入场",
                                "key_factors": [], "risk_warnings": [f"RSI={rsi:.0f}超买"],
                                "attribution": {
                                    "slot_type": "probe_long",
                                    "is_probe": True,
                                    "probe_trigger_reason": "rsi_overbought_momentum",
                                    "probe_block_reason": probe_reason,
                                    "blocked_by": probe_reason,
                                    "dispatch_path": "probe_long",
                                },
                            }
                            await self.publish("trade_decision", decision, symbol=symbol)
                            return
                    if final_action == 'open_short' and rsi <= 30:
                        self.logger.info(f"[Judge] {symbol} RSI={rsi:.0f}<=30超卖，禁止追空，标记待回调")
                        state = self._get_state(symbol)
                        state['pending_pullback'] = 'short'
                        state['pending_pullback_time'] = time.time()
                        decision = {
                            "symbol": symbol, "timestamp": time.time(),
                            "action": "hold", "confidence": 0,
                            "plan": None, "size_pct": 0,
                            "reasoning": f"RSI={rsi:.0f}超卖，等待回调至35以上再入场",
                            "key_factors": [], "risk_warnings": [f"RSI={rsi:.0f}超卖"],
                        }
                        await self.publish("trade_decision", decision, symbol=symbol)
                        return

                    # R:R分级响应：统一通过 _select_rr_floor 选择 floor
                    rr = plan.get('effective_risk_reward_ratio', plan.get('risk_reward_ratio', 0))
                    is_long = (final_action == 'open_long')
                    min_rr, rr_policy, rr_reason = self._select_rr_floor(
                        final_action, plan, tech, score
                    )
                    plan['rr_floor_used'] = min_rr
                    plan['rr_floor_reason'] = rr_reason
                    plan['rr_policy'] = rr_policy
                    _trend_snap = (tech or {}).get('trend', {}) or {}
                    plan['symbol_trend'] = _trend_snap.get('direction', 'neutral')
                    plan['symbol_higher_tf_bias'] = _trend_snap.get('higher_tf_bias', 'neutral')
                    plan['symbol_daily_bias'] = _trend_snap.get('daily_bias', 'neutral')

                    if rr < min_rr:
                        self.logger.info(
                            f"[Judge] {symbol} R:R={rr:.2f}<{min_rr:.2f}，低于动态地板 "
                            f"policy={rr_policy} reason={rr_reason}"
                        )
                        self._record_rejected_plan(symbol, final_action, plan, score, final_conf, f"rr_below_floor:{rr:.2f}<{min_rr:.2f}")
                        decision = {
                            "symbol": symbol, "timestamp": time.time(),
                            "action": "hold", "confidence": 0,
                            "plan": None, "size_pct": 0,
                            "reasoning": f"R:R={rr:.2f}<{min_rr:.2f}(动态地板)，赔率不足",
                            "key_factors": [], "risk_warnings": [f"R:R={rr:.2f}"],
                            "attribution": self._rejection_attribution(final_action, plan, f"rr_below_floor:{rr:.2f}", tech=tech),
                        }
                        await self.publish("trade_decision", decision, symbol=symbol)
                        return

                    # ═══ Low R:R position scaling (Phase 1C) ═══
                    # 通过动态门槛但 R:R 仍低于默认 1.5 时，缩仓 + 进入 low_rr_extra slot
                    low_rr_policies = {'long_bullish_low_rr', 'long_aligned_low_rr', 'long_aligned_path_evidence'}
                    if (rr < 1.5 and is_long and rr_policy in low_rr_policies
                            and not plan.get('is_probe')):
                        rr_scale = min(0.8, max(0.4, (rr - 1.2) / 0.3))
                        plan['size_usdt'] = round(
                            plan['size_usdt'] * rr_scale * self._low_rr_max_position_pct, 2
                        )
                        plan['leverage'] = min(plan.get('leverage', 5), self._low_rr_max_leverage)
                        plan['is_low_rr'] = True
                        plan['slot_type'] = 'low_rr_extra'
                        self.logger.info(
                            f"[Judge] {symbol} low_rr_long policy={rr_policy} "
                            f"R:R={rr:.2f} scale={rr_scale:.0%} "
                            f"size={plan['size_usdt']} lev={plan['leverage']}x"
                        )

                    # 余额兜底检查（size_usdt已经是保证金）
                    required_margin = plan['size_usdt']
                    if self._available_balance > 0 and self._available_balance < required_margin * 1.1:
                        plan['size_usdt'] = round(self._available_balance * 0.9, 2)
                        if plan['size_usdt'] < 1.0:
                            self.logger.warning(f"[{symbol}] 余额不足({self._available_balance:.2f} USDT)，放弃交易")
                            decision = {
                                "symbol": symbol, "timestamp": time.time(),
                                "action": "hold", "confidence": 0,
                                "plan": None, "size_pct": 0,
                                "reasoning": f"余额不足，需要{required_margin:.2f} USDT保证金",
                                "key_factors": [], "risk_warnings": ["余额不足"],
                            }
                            await self.publish("trade_decision", decision, symbol=symbol)
                            return
                        self.logger.info(f"[{symbol}] 调整仓位: {plan['size_usdt']} USDT (余额{self._available_balance:.2f})")

                    # ═══ AC-LONGPOS-11: entry_type must be set BEFORE EV gate ═══
                    # 让 bucket EV 拿到真实 entry_type，避免出现 "unknown" bucket key。
                    # _route_to_probe_long 已设置 'momentum_probe_long'，此处不能覆盖。
                    existing_et = plan.get('entry_type')
                    if existing_et == 'momentum_probe_long':
                        entry_type = existing_et
                    elif rule.get('entry_long') or rule.get('entry_short'):
                        entry_type = 'rule_signal'
                    elif rule.get('ma_aligned_long') or rule.get('ma_aligned_short'):
                        entry_type = 'ma_aligned'
                    else:
                        entry_type = 'llm_driven'
                    plan['entry_type'] = entry_type

                    # 数据回测验证：ma_aligned/probe_long 主路径走 0.5% 回调限价 + 2.0×ATR SL/2R TP。
                    # 不动开仓条件，仅改写执行参数；deferred_* 与 rule_signal/llm_driven 保持原样。
                    plan = self._apply_pullback_atr_policy(plan, tech, entry_type)

                    # ═══ P2-N: 期望值门（在所有开仓决策前的最后闸门）═══
                    # 历史交易不足时使用降级胜率（不会过严）；rolling 胜率生效时严格执行
                    if not self._check_expected_value(symbol, plan, score):
                        self._record_rejected_plan(
                            symbol, final_action, plan, score, final_conf,
                            f"ev_gate:EV={plan.get('expected_value', 0):.3f}"
                        )
                        decision = {
                            "symbol": symbol, "timestamp": time.time(),
                            "action": "hold", "confidence": 0,
                            "plan": None, "size_pct": 0,
                            "reasoning": (
                                f"EV={plan['expected_value']:.3f}<{self._ev_min_threshold} "
                                f"(p_win={plan['p_win_used']:.0%}/{plan['p_win_source']})"
                            ),
                            "key_factors": [
                                f"net_profit={plan['net_profit_usdt']:.2f}",
                                f"net_loss={plan['net_loss_usdt']:.2f}",
                            ],
                            "risk_warnings": [f"negative_ev:{plan['expected_value']:.3f}"],
                            "attribution": self._rejection_attribution(final_action, plan, f"ev_gate:EV={plan['expected_value']:.3f}", tech=tech),
                        }
                        await self.publish("trade_decision", decision, symbol=symbol)
                        return

                    # ═══ Short Main Path Risk Guard (Task 3 enforcement) ═══
                    short_gate = self._classify_short_entry_risk(
                        symbol, final_action, plan, tech, score, llm_result
                    )
                    if final_action == 'open_short' and not short_gate['allowed']:
                        block_reason = short_gate['reason']
                        self._record_rejected_plan(
                            symbol, final_action, plan, score, final_conf, block_reason,
                            self._apply_short_gate_attribution(
                                self._rejection_attribution(final_action, plan, block_reason, tech=tech),
                                short_gate,
                            )
                        )
                        attr = self._apply_short_gate_attribution(
                            self._rejection_attribution(final_action, plan, block_reason, tech=tech),
                            short_gate,
                        )
                        decision = {
                            "symbol": symbol, "timestamp": time.time(),
                            "action": "hold", "confidence": 0,
                            "plan": None, "size_pct": 0,
                            "reasoning": block_reason,
                            "key_factors": [f"blocked_by={block_reason}"],
                            "risk_warnings": [block_reason],
                            "attribution": attr,
                        }
                        await self.publish("trade_decision", decision, symbol=symbol)
                        return

                    # ═══ Long Entry Position Guard (AC-LONGPOS-01..06) ═══
                    pos_policy = self._check_entry_position_policy(
                        symbol, final_action, plan, tech, score, context='main'
                    )
                    if not pos_policy['allowed']:
                        block_reason = pos_policy['block_reason']
                        if pos_policy['should_defer']:
                            target = pos_policy['target_price']
                            state['deferred_entry'] = {
                                'action': final_action,
                                'signal_price': price,
                                'signal_score': score,
                                'target_price': target,
                                'created_at': time.time(),
                                'entry_type': 'deferred_pullback_overheat',
                                'timeout_hours': self._long_live_pullback_timeout_hours,
                                'expiry_bars': 999,
                                'chase_eligible': False,
                                'highest_since': price,
                                'lowest_since': price,
                                'entry_position_status': pos_policy['entry_position_status'],
                                'entry_position_block_reason': block_reason,
                            }
                            self.logger.info(
                                f"[Judge] {symbol} long overheat -> deferred_pullback_overheat "
                                f"reason={block_reason} target={target:.6f} (signal={price:.6f})"
                            )
                            self._record_rejected_plan(
                                symbol, final_action, plan, score, final_conf, block_reason
                            )
                            attr = self._rejection_attribution(
                                final_action, plan, block_reason, tech=tech
                            )
                            attr['entry_position_status'] = pos_policy['entry_position_status']
                            attr['entry_position_block_reason'] = block_reason
                            attr['entry_range_pos_24h'] = pos_policy['metrics']['position_in_24h_range']
                            attr['entry_pre_12h_return_pct'] = pos_policy['metrics']['pre_12h_return_pct']
                            attr['entry_prev_daily_return_pct'] = pos_policy['metrics']['prev_daily_return_pct']
                            attr['entry_position_policy'] = 'long_overheat_v1'
                            attr['deferred_target_price'] = target
                            attr['deferred_reason'] = block_reason
                            decision = {
                                "symbol": symbol, "timestamp": time.time(),
                                "action": "hold", "confidence": 0,
                                "plan": None, "size_pct": 0,
                                "reasoning": (
                                    f"Long overheat: {block_reason} (range_pos="
                                    f"{pos_policy['metrics']['position_in_24h_range']:.2f}, "
                                    f"prev_daily={pos_policy['metrics']['prev_daily_return_pct']:+.2%}), "
                                    f"等待回调至 {target:.6f}"
                                ),
                                "key_factors": [f"deferred_pullback_overheat={block_reason}"],
                                "risk_warnings": ["long_overheat"],
                                "attribution": attr,
                            }
                            await self.publish("trade_decision", decision, symbol=symbol)
                            return
                        self._record_rejected_plan(
                            symbol, final_action, plan, score, final_conf, block_reason
                        )
                        attr = self._rejection_attribution(
                            final_action, plan, block_reason, tech=tech
                        )
                        attr['entry_position_status'] = pos_policy['entry_position_status']
                        attr['entry_position_block_reason'] = block_reason
                        attr['entry_range_pos_24h'] = pos_policy['metrics']['position_in_24h_range']
                        attr['entry_pre_12h_return_pct'] = pos_policy['metrics']['pre_12h_return_pct']
                        attr['entry_prev_daily_return_pct'] = pos_policy['metrics']['prev_daily_return_pct']
                        attr['entry_position_policy'] = 'long_overheat_v1'
                        decision = {
                            "symbol": symbol, "timestamp": time.time(),
                            "action": "hold", "confidence": 0,
                            "plan": None, "size_pct": 0,
                            "reasoning": f"Entry position guard blocked: {block_reason}",
                            "key_factors": [f"blocked_by={block_reason}"],
                            "risk_warnings": [block_reason],
                            "attribution": attr,
                        }
                        await self.publish("trade_decision", decision, symbol=symbol)
                        return

                    # ═══ RQ-15M-03/04: 15m 入场时机过滤（在确定开仓意图后、发布前）═══
                    timing_allowed, timing_reason, timing_defer = self._check_15m_entry_timing(
                        tech, final_action, score
                    )
                    if not timing_allowed:
                        entry_timing = tech.get('entry_timing', {})
                        self._record_rejected_plan(symbol, final_action, plan, score, final_conf, f"15m_blocked:{timing_reason}")
                        self.logger.info(
                            f"[Judge] {symbol} 15m入场过滤拦截: action={final_action} "
                            f"score={score:.0f} 15m_bias={entry_timing.get('tf_15m_bias')} "
                            f"15m_rsi={entry_timing.get('tf_15m_rsi')} "
                            f"15m_closes={entry_timing.get('tf_15m_recent_closes')} "
                            f"reason={timing_reason} defer={timing_defer}"
                        )
                        if timing_defer:
                            # 动态超时：信号类型决定等待时长
                            if entry_type == 'rule_signal' and self._has_directional_confirmation(tech, final_action):
                                defer_timeout = 6
                            elif entry_type == 'ma_aligned' and self._has_directional_confirmation(tech, final_action):
                                defer_timeout = 4
                            else:
                                defer_timeout = self._15m_timeout_hours
                            state['deferred_entry'] = {
                                'action': final_action,
                                'signal_price': price,
                                'signal_score': score,
                                'target_price': price,
                                'created_at': time.time(),
                                'entry_type': 'deferred_15m_confirmation',
                                'timeout_hours': defer_timeout,
                                'expiry_bars': 999,
                                'chase_eligible': False,
                                'highest_since': price,
                                'lowest_since': price,
                            }
                        against_label = f"15m_{'bearish' if final_action == 'open_long' else 'bullish'}_against_{final_action.split('_')[1]}"
                        _15m_attr = {
                            "tf_15m_bias": entry_timing.get('tf_15m_bias', 'unavailable'),
                            "tf_15m_rsi": entry_timing.get('tf_15m_rsi'),
                            "tf_15m_entry_status": "blocked",
                            "tf_15m_block_reason": timing_reason,
                        }
                        _15m_attr.update(self._rejection_attribution(final_action, plan, f"15m_blocked:{timing_reason}", tech=tech))
                        decision = {
                            "symbol": symbol, "timestamp": time.time(),
                            "action": "hold", "confidence": 0,
                            "plan": None, "size_pct": 0,
                            "reasoning": f"15m入场时机不符: {timing_reason}",
                            "key_factors": [f"deferred_15m={timing_defer}"],
                            "risk_warnings": [against_label],
                            "attribution": _15m_attr,
                        }
                        await self.publish("trade_decision", decision, symbol=symbol)
                        return

                    # RQ-07: 归因字段
                    attribution = self._build_attribution(
                        tech, final_action, score, plan, llm_result, entry_type
                    )

                    # ═══ Short Main Path Risk Guard pass attribution ═══
                    if final_action == 'open_short':
                        attribution = self._apply_short_gate_attribution(attribution, short_gate)

                    # RQ-03: 计算排名分数并 buffer 候选
                    rank_candidate = {
                        'symbol': symbol, 'score': score, 'plan': plan,
                        'tech': tech, 'attribution': attribution,
                        'entry_type': entry_type,
                    }
                    rank_score = self._candidate_ranker._compute_rank_score(rank_candidate)
                    attribution['rank_score'] = rank_score
                    plan['attribution'] = attribution

                    decision = {
                        "symbol": symbol, "timestamp": time.time(),
                        "action": final_action,
                        "confidence": final_conf,
                        "plan": plan,
                        "size_pct": plan['size_usdt'] / self._max_trade_amount,
                        "reasoning": llm_result.get('reasoning', ''),
                        "key_factors": llm_result.get('key_factors', []),
                        "risk_warnings": llm_result.get('risk_warnings', []),
                        "entry_type": entry_type,
                        "attribution": attribution,
                    }
                    if self._confidence_split_enabled:
                        htf_aligned = self._is_htf_aligned(tech, final_action)
                        llm_action = llm_result.get('action', 'hold')
                        split = self._compute_confidence_split(
                            score, confidence, final_conf, llm_action,
                            final_action, has_rule_signal, htf_aligned
                        )
                        decision.update(split)
                        attribution.update(split)

                    # Final slot gate: verify slot availability before publish/buffer
                    slot_type = plan.get('slot_type', 'main')
                    if slot_type == 'main':
                        occupied = self._open_positions | self._pending_open_symbols
                        all_slots = {**self._position_slots, **self._pending_open_slots}
                        main_count = sum(1 for s in occupied if all_slots.get(s, 'main') == 'main')
                        if main_count >= self._max_concurrent_positions:
                            gate_attr = self._rejection_attribution(final_action, plan, "main_slot_full", tech=tech)
                            self._record_rejected_plan(symbol, final_action, plan, score, final_conf, "main_slot_full", gate_attr)
                            await self.publish("trade_decision", {
                                "symbol": symbol, "timestamp": time.time(),
                                "action": "hold", "confidence": 0,
                                "plan": None, "size_pct": 0,
                                "reasoning": f"Main slot full ({main_count}/{self._max_concurrent_positions})",
                                "key_factors": ["slot_gate:main_full"],
                                "risk_warnings": ["concurrent_limit_reached"],
                                "attribution": gate_attr,
                            }, symbol=symbol)
                            return
                    elif slot_type == 'low_rr_extra':
                        occupied = self._open_positions | self._pending_open_symbols
                        all_slots = {**self._position_slots, **self._pending_open_slots}
                        low_rr_count = sum(1 for s in occupied if all_slots.get(s) == 'low_rr_extra')
                        low_rr_cap = self._candidate_ranker.low_rr_extra_slot if hasattr(self, '_candidate_ranker') else 1
                        if low_rr_count >= low_rr_cap:
                            gate_attr = self._rejection_attribution(final_action, plan, "low_rr_slot_full", tech=tech)
                            self._record_rejected_plan(symbol, final_action, plan, score, final_conf, "low_rr_slot_full", gate_attr)
                            await self.publish("trade_decision", {
                                "symbol": symbol, "timestamp": time.time(),
                                "action": "hold", "confidence": 0,
                                "plan": None, "size_pct": 0,
                                "reasoning": f"Low R:R extra slot full ({low_rr_count}/{low_rr_cap})",
                                "key_factors": ["slot_gate:low_rr_full"],
                                "risk_warnings": ["concurrent_limit_reached"],
                                "attribution": gate_attr,
                            }, symbol=symbol)
                            return
                    elif slot_type == 'probe_short':
                        occupied = self._open_positions | self._pending_open_symbols
                        all_slots = {**self._position_slots, **self._pending_open_slots}
                        probe_count = sum(1 for s in occupied if all_slots.get(s) == 'probe_short')
                        if probe_count >= self._probe_short_max_concurrent:
                            gate_attr = self._rejection_attribution(final_action, plan, "probe_slot_full", tech=tech)
                            self._record_rejected_plan(symbol, final_action, plan, score, final_conf, "probe_slot_full", gate_attr)
                            await self.publish("trade_decision", {
                                "symbol": symbol, "timestamp": time.time(),
                                "action": "hold", "confidence": 0,
                                "plan": None, "size_pct": 0,
                                "reasoning": f"Probe short slot full ({probe_count}/{self._probe_short_max_concurrent})",
                                "key_factors": ["slot_gate:probe_full"],
                                "risk_warnings": ["concurrent_limit_reached"],
                                "attribution": gate_attr,
                            }, symbol=symbol)
                            return
                    elif slot_type == 'probe_long':
                        occupied = self._open_positions | self._pending_open_symbols
                        all_slots = {**self._position_slots, **self._pending_open_slots}
                        probe_long_count = sum(1 for s in occupied if all_slots.get(s) == 'probe_long')
                        max_pl = getattr(self, '_probe_long_max_concurrent', 1)
                        if probe_long_count >= max_pl:
                            gate_attr = self._rejection_attribution(final_action, plan, "probe_long_slot_full", tech=tech)
                            self._record_rejected_plan(symbol, final_action, plan, score, final_conf, "probe_long_slot_full", gate_attr)
                            await self.publish("trade_decision", {
                                "symbol": symbol, "timestamp": time.time(),
                                "action": "hold", "confidence": 0,
                                "plan": None, "size_pct": 0,
                                "reasoning": f"Probe long slot full ({probe_long_count}/{max_pl})",
                                "key_factors": ["slot_gate:probe_long_full"],
                                "risk_warnings": ["concurrent_limit_reached"],
                                "attribution": gate_attr,
                            }, symbol=symbol)
                            return

                    # Buffer into ranker for Top-N selection
                    if self._candidate_ranker.enabled:
                        rank_candidate['decision'] = decision
                        rank_candidate['llm_output'] = llm_result
                        rank_candidate['tech'] = tech
                        self._candidate_ranker.add_candidate(rank_candidate)
                        self._schedule_rank_flush()
                        self.logger.info(
                            f"[决策] {symbol} {decision['action']} "
                            f"置信度={decision['confidence']} rank={rank_score:.1f} → 等待排名裁决"
                        )
                        return
                else:
                    decision = {
                        "symbol": symbol, "timestamp": time.time(),
                        "action": final_action,
                        "confidence": final_conf,
                        "plan": None,
                        "size_pct": 0,
                        "reasoning": llm_result.get('reasoning', ''),
                        "key_factors": llm_result.get('key_factors', []),
                        "risk_warnings": llm_result.get('risk_warnings', []),
                    }

        if decision['action'] in ('open_long', 'open_short') and decision.get('confidence', 0) >= 60:
            published = await self._gate_and_publish_open(symbol, decision, state)
            if not published:
                self.logger.info(f"[Judge] {symbol} open rejected by slot gate (ranking disabled)")
        else:
            await self.publish("trade_decision", decision, symbol=symbol)
            self.logger.info(
                f"[决策] {symbol} {decision['action']} "
                f"置信度={decision['confidence']} "
                f"{'plan='+str(decision['plan']['leverage'])+'x' if decision.get('plan') else ''} "
                f"理由: {(decision.get('reasoning') or '')[:60]}"
            )

    # ═══ Unified Open Dispatch ═══

    async def _gate_and_publish_open(self, symbol: str, decision: dict, state: dict) -> bool:
        """Unified open dispatch: final slot gate + pending reservation + publish.

        Returns True if published, False if rejected by slot gate.
        All open decisions (main path ranking-disabled, deferred flows) MUST use this.
        """
        self._sweep_stale_pending()
        plan = decision.get('plan') or {}
        slot_type = plan.get('slot_type', 'main')
        action = decision.get('action', 'hold')
        occupied = self._open_positions | self._pending_open_symbols
        all_slots = {**self._position_slots, **self._pending_open_slots}

        if slot_type == 'main':
            main_count = sum(1 for s in occupied if all_slots.get(s, 'main') == 'main')
            if main_count >= self._max_concurrent_positions:
                gate_attr = self._rejection_attribution(action, plan, "main_slot_full")
                self._record_rejected_plan(symbol, action, plan, 0, 0, "main_slot_full", gate_attr)
                await self.publish("trade_decision", {
                    "symbol": symbol, "timestamp": time.time(),
                    "action": "hold", "confidence": 0,
                    "plan": None, "size_pct": 0,
                    "reasoning": f"Main slot full ({main_count}/{self._max_concurrent_positions})",
                    "key_factors": ["slot_gate:main_full"],
                    "risk_warnings": ["concurrent_limit_reached"],
                    "attribution": gate_attr,
                }, symbol=symbol)
                return False
        elif slot_type == 'low_rr_extra':
            low_rr_count = sum(1 for s in occupied if all_slots.get(s) == 'low_rr_extra')
            low_rr_cap = self._candidate_ranker.low_rr_extra_slot if hasattr(self, '_candidate_ranker') else 1
            if low_rr_count >= low_rr_cap:
                gate_attr = self._rejection_attribution(action, plan, "low_rr_slot_full")
                self._record_rejected_plan(symbol, action, plan, 0, 0, "low_rr_slot_full", gate_attr)
                await self.publish("trade_decision", {
                    "symbol": symbol, "timestamp": time.time(),
                    "action": "hold", "confidence": 0,
                    "plan": None, "size_pct": 0,
                    "reasoning": f"Low R:R extra slot full ({low_rr_count}/{low_rr_cap})",
                    "key_factors": ["slot_gate:low_rr_full"],
                    "risk_warnings": ["concurrent_limit_reached"],
                    "attribution": gate_attr,
                }, symbol=symbol)
                return False
        elif slot_type == 'probe_short':
            probe_count = sum(1 for s in occupied if all_slots.get(s) == 'probe_short')
            if probe_count >= self._probe_short_max_concurrent:
                gate_attr = self._rejection_attribution(action, plan, "probe_slot_full")
                self._record_rejected_plan(symbol, action, plan, 0, 0, "probe_slot_full", gate_attr)
                await self.publish("trade_decision", {
                    "symbol": symbol, "timestamp": time.time(),
                    "action": "hold", "confidence": 0,
                    "plan": None, "size_pct": 0,
                    "reasoning": f"Probe short slot full ({probe_count}/{self._probe_short_max_concurrent})",
                    "key_factors": ["slot_gate:probe_full"],
                    "risk_warnings": ["concurrent_limit_reached"],
                    "attribution": gate_attr,
                }, symbol=symbol)
                return False
        elif slot_type == 'probe_long':
            probe_long_count = sum(1 for s in occupied if all_slots.get(s) == 'probe_long')
            max_pl = getattr(self, '_probe_long_max_concurrent', 1)
            if probe_long_count >= max_pl:
                gate_attr = self._rejection_attribution(action, plan, "probe_long_slot_full")
                self._record_rejected_plan(symbol, action, plan, 0, 0, "probe_long_slot_full", gate_attr)
                await self.publish("trade_decision", {
                    "symbol": symbol, "timestamp": time.time(),
                    "action": "hold", "confidence": 0,
                    "plan": None, "size_pct": 0,
                    "reasoning": f"Probe long slot full ({probe_long_count}/{max_pl})",
                    "key_factors": ["slot_gate:probe_long_full"],
                    "risk_warnings": ["concurrent_limit_reached"],
                    "attribution": gate_attr,
                }, symbol=symbol)
                return False

        # Reserve pending slot
        self._pending_open_symbols.add(symbol)
        self._pending_open_ts[symbol] = time.time()
        self._pending_open_slots[symbol] = slot_type
        state['last_open_time'] = time.time()

        # Generate request_id
        from datetime import datetime
        date_str = datetime.utcnow().strftime('%Y%m%d')
        symbol_base = symbol.split('-')[0] if '-' in symbol else symbol
        req_id = f"{date_str}-{symbol_base}-{str(uuid.uuid4())[:8]}"
        decision['request_id'] = req_id
        decision['schema_version'] = 'trade_decision.v2'

        # Assign dispatch_path based on entry_type
        entry_type = decision.get('entry_type', '')
        if 'deferred_15m' in entry_type:
            dp = 'deferred_15m'
        elif 'deferred_pullback' in entry_type or entry_type == 'pullback':
            dp = 'deferred_pullback'
        elif 'deferred_chase' in entry_type or entry_type == 'chase':
            dp = 'deferred_chase'
        elif 'ranking_selected' in entry_type:
            dp = 'main_ranking'
        elif slot_type == 'probe_long':
            dp = 'probe_long'
        elif slot_type == 'probe_short':
            dp = 'probe_short'
        elif slot_type == 'low_rr_extra':
            dp = 'main_ranking'
        else:
            dp = 'main_direct'
        decision['dispatch_path'] = dp

        # Ensure attribution dict exists and propagate fields
        if 'attribution' not in decision:
            decision['attribution'] = {}
        if isinstance(decision.get('attribution'), dict):
            decision['attribution']['dispatch_path'] = dp
            decision['attribution']['request_id'] = req_id

        # Ensure split fields exist at top level for contract compliance
        if 'signal_score' not in decision:
            decision['signal_score'] = decision.get('attribution', {}).get('signal_score', decision.get('confidence', 0))
        if 'execution_confidence' not in decision:
            decision['execution_confidence'] = decision.get('attribution', {}).get('execution_confidence', decision.get('confidence', 0))
        if 'position_scale' not in decision:
            decision['position_scale'] = decision.get('attribution', {}).get('position_scale', 1.0)

        # Counterfactual replay: tape the accepted open decision (observability-only).
        # Guarded: tape absence (e.g. partial construction in tests) must never break the decision path.
        if action in ("open_long", "open_short") and getattr(self, "_decision_tape", None) is not None:
            self._decision_tape.record_decision(build_bundle(
                symbol=symbol, decision="accept",
                request_id=decision.get("request_id"),
                tech_analysis=getattr(self, "_symbol_tech_tape_cache", {}).get(symbol) or {},
                price_at_decision=(plan or {}).get("entry_ref"),
                regime_state=getattr(self._regime_manager, "_effective_regime", None),
                llm_output=getattr(self, "_symbol_llm_cache", {}).get(symbol), llm_audit_ref=None,
                trade_decision_output={
                    "plan": decision.get("plan"),
                    "attribution": decision.get("attribution"),
                },
                state_snapshot=self._capture_state_snapshot(symbol),
                config_snapshot=dict(getattr(self, "config", {}) or {}),
            ))

        await self.publish("trade_decision", decision, symbol=symbol)
        self.logger.info(
            f"[决策] {symbol} {action} slot={slot_type} req={req_id} "
            f"置信度={decision.get('confidence', 0)} "
            f"{'plan='+str(plan.get('leverage', '?'))+'x' if plan else ''}"
        )
        return True

    # ═══ RQ-03: Ranking Top-N flush ═══

    def _schedule_rank_flush(self):
        """Schedule a flush after a short window to collect all candidates in this tick."""
        if self._rank_flush_task and not self._rank_flush_task.done():
            self._rank_flush_task.cancel()
        loop = asyncio.get_event_loop()
        self._rank_flush_task = loop.create_task(self._delayed_rank_flush())

    async def _delayed_rank_flush(self):
        await asyncio.sleep(self._rank_flush_delay)
        await self._flush_ranked_candidates()

    async def _flush_ranked_candidates(self):
        """Flush ranker buffer: publish selected, hold rejected."""
        self._sweep_stale_pending()
        occupied = self._open_positions | self._pending_open_symbols
        all_slots = {**self._position_slots, **self._pending_open_slots}
        slot_occupancy = {
            'main': sum(1 for s in occupied if all_slots.get(s, 'main') == 'main'),
            'low_rr_extra': sum(1 for s in occupied if all_slots.get(s) == 'low_rr_extra'),
            'probe_short': sum(1 for s in occupied if all_slots.get(s) == 'probe_short'),
            'probe_long': sum(1 for s in occupied if all_slots.get(s) == 'probe_long'),
        }
        selected, rejected = self._candidate_ranker.rank_and_select(occupied, slot_occupancy)

        for candidate in selected:
            decision = candidate['decision']
            symbol = decision['symbol']
            state = self._get_state(symbol)
            # 延迟派发：用候选入队时挂载的 llm/tech re-prime cache，避免读到被新决策 reset 的串味值
            if hasattr(self, "_symbol_llm_cache"):
                self._symbol_llm_cache[symbol] = candidate.get('llm_output')
            if hasattr(self, "_symbol_tech_tape_cache") and candidate.get('tech') is not None:
                self._symbol_tech_tape_cache[symbol] = candidate.get('tech')
            decision['entry_type'] = decision.get('entry_type', 'ranking_selected')
            published = await self._gate_and_publish_open(symbol, decision, state)
            if published:
                self.logger.info(
                    f"[Ranking] {symbol} SELECTED rank={candidate.get('attribution', {}).get('rank_score', 0):.1f} slot={(candidate.get('plan') or {}).get('slot_type', 'main')}"
                )
            else:
                ranked_attr = candidate.get('attribution') or self._rejection_attribution(
                    decision.get('action', 'hold'), candidate.get('plan'), 'ranking_slot_full'
                )
                self._record_rejected_plan(
                    symbol, decision.get('action', 'hold'), candidate.get('plan'),
                    candidate.get('score', 0), decision.get('confidence', 0), 'ranking_slot_full',
                    ranked_attr
                )

        for candidate in rejected:
            decision = candidate['decision']
            symbol = decision['symbol']
            ranked_attr = candidate.get('attribution') or self._rejection_attribution(
                decision.get('action', 'hold'), candidate.get('plan'), 'ranked_out'
            )
            self._record_rejected_plan(
                symbol, decision.get('action', 'hold'), candidate.get('plan'),
                candidate.get('score', 0), decision.get('confidence', 0), 'ranked_out',
                ranked_attr
            )
            await self.publish("trade_decision", {
                "symbol": symbol, "timestamp": time.time(),
                "action": "hold", "confidence": 0,
                "plan": None, "size_pct": 0,
                "reasoning": f"排名淘汰: rank={candidate.get('attribution', {}).get('rank_score', 0):.1f}，槽位不足",
                "key_factors": ["ranked_out"],
                "risk_warnings": [],
                "attribution": ranked_attr,
            }, symbol=symbol)
            self.logger.info(
                f"[Ranking] {symbol} REJECTED rank={candidate.get('attribution', {}).get('rank_score', 0):.1f}"
            )

    # ═══ 信号聚合评分 ═══

    async def _publish_hold(self, symbol: str, reason: str, warnings: list = None):
        await self.publish("trade_decision", {
            "symbol": symbol, "timestamp": time.time(),
            "action": "hold", "confidence": 0,
            "plan": None, "size_pct": 0,
            "reasoning": reason,
            "key_factors": [],
            "risk_warnings": warnings or [],
        }, symbol=symbol)

    def _open_quality_rejection(self, symbol: str, tech: dict, action: str,
                                score: float, confidence: float,
                                context: str = 'live_open') -> str:
        """实盘开仓质量门。

        这层只拦截低质量开仓，不改变评分体系本身。目标是让 Judge 与 Executor
        对“什么能进入实盘”保持同一口径，并防止弱信号在回调命中后被自动抬分。
        """
        if confidence < self._min_confidence:
            return f"confidence={confidence:.0f}<min_confidence={self._min_confidence}"

        risk = tech.get('risk', {})
        liquidity_score = risk.get('liquidity_score')
        if (liquidity_score is not None and
            liquidity_score < self._min_liquidity_score_for_weak_signal and
            abs(score) < 60):
            return f"liquidity_score={liquidity_score}<min_liquidity_for_weak_signal"

        if context.startswith('deferred'):
            directional_ok = self._has_directional_confirmation(tech, action)
            if abs(score) < self._min_deferred_signal_score and not directional_ok:
                return (
                    f"deferred_signal_score={abs(score):.0f}<"
                    f"{self._min_deferred_signal_score} without HTF confirmation"
                )

        # RQ-06: 原型级 cooldown 检查
        if self._archetype_cooldown.enabled:
            # 预判原型（用当前可用信息）
            trend = tech.get('trend', {})
            expected = 'bullish' if action == 'open_long' else 'bearish'
            htf_dirs = [
                trend.get('direction', 'neutral'),
                trend.get('higher_tf_bias', 'neutral'),
                trend.get('daily_bias', 'neutral'),
            ]
            htf_votes = htf_dirs.count(expected)
            pre_attribution = {
                'entry_type': context if context.startswith('deferred') else 'standard',
                'htf_votes': htf_votes,
                'liquidity_bucket': 'low' if (risk.get('liquidity_score', 50) < 30) else 'medium',
            }
            archetype = self._archetype_cooldown.classify(pre_attribution)
            is_cooled, remaining = self._archetype_cooldown.is_cooled(archetype)
            if is_cooled and abs(score) < self._ev_strong_signal_threshold:
                return f"archetype_cooldown: {archetype} ({remaining}s remaining)"

        return ""

    def _reconfirm_deferred(self, symbol: str, tech: dict, action: str,
                            signal_score: float, context: str) -> str:
        """RQ-02: deferred/chase 触发时二次确认。

        返回空字符串表示通过，非空为拒绝原因。
        检查项：
        1. HTF 至少不反向（优先同向）
        2. LLM 最近结果不为 hold/reverse（通过 tech 中的 llm_relation 判断）
        3. 有效 RR >= 1.5（强信号可放宽到 1.2）
        4. 低流动性弱信号不得从 deferred 转 live
        """
        trend = tech.get('trend', {})
        expected_dir = 'bullish' if action == 'open_long' else 'bearish'
        opposite_dir = 'bearish' if action == 'open_long' else 'bullish'

        # 1. HTF 不得反向
        htf_bias = trend.get('higher_tf_bias', 'neutral')
        if htf_bias == opposite_dir:
            return f"htf_mismatch: HTF={htf_bias} vs signal={expected_dir}"

        # 2. 1h 趋势方向不得反向
        trend_dir = trend.get('direction', 'neutral')
        if trend_dir == opposite_dir and trend.get('strength', 0) >= 60:
            return f"trend_reversed: 1h={trend_dir}(strength={trend.get('strength',0)}) vs signal={expected_dir}"

        # 3. 流动性检查（弱信号 + 低流动性 → 拒绝）
        risk = tech.get('risk', {})
        liquidity = risk.get('liquidity_score', 50)
        if liquidity < self._min_liquidity_score_for_weak_signal and abs(signal_score) < 60:
            return f"low_liquidity_deferred: liquidity={liquidity}, score={signal_score:.0f}"

        return ""

    def _has_directional_confirmation(self, tech: dict, action: str) -> bool:
        trend = tech.get('trend', {})
        expected = 'bullish' if action == 'open_long' else 'bearish'
        votes = [
            trend.get('direction', 'neutral'),
            trend.get('higher_tf_bias', 'neutral'),
            trend.get('daily_bias', 'neutral'),
        ]
        return votes.count(expected) >= 2 and trend.get('strength', 0) >= 50

    def _check_15m_entry_timing(self, tech: dict, action: str, score: float) -> tuple:
        """RQ-15M-03: 15m 入场时机过滤。

        返回 (allowed: bool, reason: str, should_defer: bool)
        """
        if not self._15m_enabled:
            return (True, "", False)

        entry_timing = tech.get('entry_timing', {})
        available = entry_timing.get('tf_15m_available', False)

        if not available:
            if self._15m_required:
                return (False, "15m_unavailable", False)
            return (True, "15m_unavailable_not_required", False)

        is_long = (action == 'open_long')
        block_key = 'tf_15m_block_long' if is_long else 'tf_15m_block_short'
        confirm_key = 'tf_15m_confirm_long' if is_long else 'tf_15m_confirm_short'

        if entry_timing.get(block_key, False):
            reason = entry_timing.get('tf_15m_reason', '15m_blocked')
            return (False, reason, self._15m_defer_on_block)

        if entry_timing.get(confirm_key, False):
            return (True, "15m_confirmed", False)

        # Neutral: 强信号 + HTF 同向可通过，否则等待
        bias = entry_timing.get('tf_15m_bias', 'neutral')
        if bias == 'neutral':
            if self._15m_neutral_allows_strong and abs(score) >= self._15m_strong_score_threshold:
                # P2-2: 需求要求 neutral 放行需 4h/1d 同向
                if self._has_directional_confirmation(tech, action):
                    return (True, "15m_neutral_strong_allowed", False)
                return (False, f"15m_neutral_no_htf_confirmation(score={score:.0f})", self._15m_defer_on_block)
            return (False, f"15m_neutral_weak_signal(score={score:.0f})", self._15m_defer_on_block)

        return (True, "", False)

    def _check_15m_deferred_reconfirm(self, tech: dict, action: str) -> str:
        """RQ-15M-05: deferred 触发时 15m 二次确认。返回空字符串表示通过。"""
        if not self._15m_enabled:
            return ""

        entry_timing = tech.get('entry_timing', {})
        if not entry_timing.get('tf_15m_available', False):
            if self._15m_required:
                return "15m_unavailable_for_deferred"
            return ""

        rsi = entry_timing.get('tf_15m_rsi', 50) or 50
        recent_closes = entry_timing.get('tf_15m_recent_closes', 'mixed')
        bias = entry_timing.get('tf_15m_bias', 'neutral')

        if action == 'open_long':
            if bias == 'bearish':
                return f"15m_bearish_against_deferred_long(RSI={rsi:.1f})"
            if rsi < 45:
                return f"15m_rsi_too_low_for_long({rsi:.1f}<45)"
            if recent_closes == 'down':
                return f"15m_closes_down_against_long"
        else:
            if bias == 'bullish':
                return f"15m_bullish_against_deferred_short(RSI={rsi:.1f})"
            if rsi > 55:
                return f"15m_rsi_too_high_for_short({rsi:.1f}>55)"
            if recent_closes == 'up':
                return f"15m_closes_up_against_short"

        return ""

    @staticmethod
    def _summarize_provenance(tech: dict) -> dict:
        """Return a metadata-only summary of the provenance block in tech_analysis.

        This is WRITE-ONLY metadata: the result is stored in attribution but
        must never be read by any gate, ranking, or veto logic.
        """
        prov = (tech or {}).get("provenance") or {}
        if not prov:
            return {"quality": "unknown", "weakest_confidence": None, "has_cross_exchange": False}
        confs = [e.get("confidence", 0.0) for e in prov.values()]
        weakest = min(confs) if confs else 0.0
        has_cross = any(
            ((e.get("source") or "").split("_", 1)[0] != "okx") for e in prov.values()
        )
        return {"quality": "known", "weakest_confidence": round(weakest, 4),
                "has_cross_exchange": bool(has_cross)}

    def _build_attribution(self, tech: dict, action: str, score: float,
                           plan: dict, llm_result: dict = None,
                           entry_type: str = 'unknown') -> dict:
        """RQ-07: 构建完整归因字段，附加到 trade_decision。"""
        trend = tech.get('trend', {})
        momentum = tech.get('momentum', {})
        risk = tech.get('risk', {})
        rule = tech.get('rule_signal', {})

        # HTF votes: 统计多周期同向票数
        expected = 'bullish' if action == 'open_long' else 'bearish'
        htf_dirs = [
            trend.get('direction', 'neutral'),
            trend.get('higher_tf_bias', 'neutral'),
            trend.get('daily_bias', 'neutral'),
        ]
        htf_votes = htf_dirs.count(expected)

        # LLM relation
        llm_action = (llm_result or {}).get('action', 'hold')
        if llm_action == action:
            llm_relation = 'agree'
        elif llm_action == 'hold':
            llm_relation = 'hold'
        elif llm_action in ('open_long', 'open_short') and llm_action != action:
            llm_relation = 'reverse'
        else:
            llm_relation = 'neutral'

        # Liquidity bucket
        liq = risk.get('liquidity_score', 50)
        if liq >= 70:
            liquidity_bucket = 'high'
        elif liq >= 30:
            liquidity_bucket = 'medium'
        else:
            liquidity_bucket = 'low'

        # RR bucket
        rr = plan.get('effective_risk_reward_ratio', 0) if plan else 0
        if rr >= 2.5:
            rr_bucket = 'excellent'
        elif rr >= 1.8:
            rr_bucket = 'good'
        elif rr >= 1.5:
            rr_bucket = 'acceptable'
        else:
            rr_bucket = 'poor'

        # Rule signal type
        if rule.get('entry_long'):
            rule_signal_type = 'ma_crossover_long'
        elif rule.get('entry_short'):
            rule_signal_type = 'ma_crossover_short'
        elif rule.get('ma_aligned_long'):
            rule_signal_type = 'ma_aligned_long'
        elif rule.get('ma_aligned_short'):
            rule_signal_type = 'ma_aligned_short'
        else:
            rule_signal_type = 'none'

        # 15m entry timing attribution
        entry_timing = tech.get('entry_timing', {})
        tf_15m_available = entry_timing.get('tf_15m_available', False)
        if tf_15m_available:
            is_long = (action == 'open_long')
            confirm_key = 'tf_15m_confirm_long' if is_long else 'tf_15m_confirm_short'
            block_key = 'tf_15m_block_long' if is_long else 'tf_15m_block_short'
            if entry_timing.get(confirm_key):
                tf_15m_entry_status = 'confirmed'
            elif entry_timing.get(block_key):
                tf_15m_entry_status = 'blocked'
            else:
                tf_15m_entry_status = 'neutral'
        else:
            tf_15m_entry_status = 'unavailable'

        attribution = {
            'entry_type': entry_type,
            'rule_signal_type': rule_signal_type,
            'signal_score': round(score, 1),
            'llm_relation': llm_relation,
            'htf_votes': htf_votes,
            'liquidity_bucket': liquidity_bucket,
            'rr_bucket': rr_bucket,
            'ev_at_entry': plan.get('expected_value', 0) if plan else 0,
            'p_win_used': plan.get('p_win_used', 0) if plan else 0,
            'p_win_source': plan.get('p_win_source', 'unknown') if plan else 'unknown',
            'tf_15m_bias': entry_timing.get('tf_15m_bias', 'unavailable'),
            'tf_15m_rsi': entry_timing.get('tf_15m_rsi'),
            'tf_15m_ma_alignment': entry_timing.get('tf_15m_ma_alignment', 'unavailable'),
            'tf_15m_recent_closes': entry_timing.get('tf_15m_recent_closes', 'unavailable'),
            'tf_15m_entry_status': tf_15m_entry_status,
            'tf_15m_block_reason': entry_timing.get('tf_15m_reason', '') if tf_15m_entry_status == 'blocked' else '',
            # Regime attribution
            'entry_regime': self._regime_manager._effective_regime,
            'raw_regime': self._regime_manager._raw_regime,
            'regime_confidence': self._regime_manager._confidence,
            'rr_policy': self._get_rr_policy_label(action, plan),
            'rr_floor_used': (plan or {}).get('rr_floor_used'),
            'rr_floor_reason': (plan or {}).get('rr_floor_reason', ''),
            'symbol_trend': (plan or {}).get('symbol_trend',
                                             trend.get('direction', 'neutral')),
            'symbol_higher_tf_bias': (plan or {}).get('symbol_higher_tf_bias',
                                                     trend.get('higher_tf_bias', 'neutral')),
            'symbol_daily_bias': (plan or {}).get('symbol_daily_bias',
                                                  trend.get('daily_bias', 'neutral')),
            'slot_type': (plan or {}).get('slot_type', 'main'),
            'is_low_rr': (plan or {}).get('is_low_rr', False),
            'is_probe': (plan or {}).get('is_probe', False),
            'probe_trigger_reason': (plan or {}).get('probe_trigger_reason', ''),
            'probe_evidence': (plan or {}).get('probe_evidence', {}),
            'blocked_by': '',
            'request_id': str(uuid.uuid4())[:12],
            # ═══ Long Entry Position Guard attribution ═══
            'entry_position_status': 'normal',
            'entry_position_block_reason': '',
            'entry_range_pos_24h': self._coalesce_float(
                (tech.get('entry_context') or tech.get('short_context')
                 or {}).get('position_in_24h_range'), default=0.5),
            'entry_pre_12h_return_pct': self._coalesce_float(
                (tech.get('entry_context') or tech.get('short_context')
                 or {}).get('pre_12h_return_pct'), default=0.0),
            'entry_prev_daily_return_pct': float((tech.get('entry_context')
                                                  or {}).get('prev_daily_return_pct', 0.0) or 0.0),
            'entry_position_policy': 'long_overheat_v1',
            'deferred_target_price': (plan or {}).get('deferred_target_price', 0.0),
            'deferred_reason': (plan or {}).get('deferred_reason', ''),
            'ev_bucket_key': (plan or {}).get('ev_bucket_key', ''),
            'ev_bucket_trade_count': (plan or {}).get('ev_bucket_trade_count', 0),
            'ev_bucket_min_trades': (plan or {}).get('ev_bucket_min_trades',
                                                     getattr(self, '_ev_bucket_min_trades', 10)),
            'ev_bucket_sparse': (plan or {}).get('ev_bucket_sparse', False),
        }
        attribution['provenance'] = self._summarize_provenance(tech)
        return attribution

    def _is_htf_aligned(self, tech: dict, action: str) -> bool:
        """Check if higher timeframe bias aligns with the action direction."""
        htf = tech.get('trend', {}).get('higher_tf_bias', 'neutral')
        if action == 'open_long' and htf == 'bullish':
            return True
        if action == 'open_short' and htf == 'bearish':
            return True
        return False

    def _compute_confidence_split(self, score: float, confidence: int, final_conf: int,
                                  llm_action: str, action: str, has_rule_signal: bool,
                                  htf_aligned: bool) -> dict:
        """Phase 2 EPIC A: compute signal_score / execution_confidence / position_scale."""
        signal_score = round(score, 1)
        execution_confidence = final_conf
        if confidence > 0:
            position_scale = round(min(1.0, final_conf / confidence), 3)
        else:
            position_scale = 1.0

        # Determine LLM relation
        if llm_action == action:
            llm_relation = 'agree'
        elif llm_action == 'hold':
            llm_relation = 'hold'
        elif llm_action in ('open_long', 'open_short') and llm_action != action:
            llm_relation = 'reverse'
        else:
            llm_relation = 'neutral'

        # Hold reason
        hold_reason = ''
        if llm_action == 'hold':
            if has_rule_signal and htf_aligned:
                hold_reason = 'llm_hold_scale_only'
            elif has_rule_signal:
                hold_reason = 'llm_hold_rule_override'
            else:
                hold_reason = 'llm_hold_no_rule'
        elif llm_action in ('open_long', 'open_short') and llm_action != action:
            hold_reason = 'llm_direction_conflict'

        return {
            'signal_score': signal_score,
            'execution_confidence': execution_confidence,
            'position_scale': position_scale,
            'llm_relation': llm_relation,
            'hold_reason': hold_reason,
        }

    def _hold_reason(self, tech: dict, score: float) -> str:
        trend = tech.get('trend', {})
        direction = trend.get('direction', 'neutral')
        strength = trend.get('strength', 50)
        rsi = tech.get('momentum', {}).get('rsi', 50)
        if direction == 'bearish' and strength > 70 and rsi < 35:
            return f"趋势bearish/{strength}但RSI={rsi:.0f}超卖，追空风险高，观望"
        if direction == 'bullish' and strength > 70 and rsi > 65:
            return f"趋势bullish/{strength}但RSI={rsi:.0f}超买，追多风险高，观望"
        if direction == 'neutral':
            return "趋势中性，无明确方向，观望"
        if abs(score) < 10:
            return "多空信号对冲，净得分接近零，观望"
        return "信号强度不足，观望"

    def _select_rr_floor(self, action: str, plan: dict, tech: dict,
                         score: float) -> tuple:
        """统一 R:R floor 选择，主路径和 deferred 路径必须共用。

        返回 (min_rr, rr_policy, rr_floor_reason)。

        优先级：
        1. is_probe → probe_rr_floor
        2. open_long + bullish + low_rr_slot_enabled → rr_floor_long_bullish
        3. open_long + (mixed|choppy) + 趋势强一致 + low_rr_long_aligned_enabled
           → rr_floor_long_aligned_choppy
        4. open_short + bullish + short_regime_guard → rr_floor_short_bullish
        5. 默认 → rr_floor_default
        """
        plan = plan or {}
        is_long = ('long' in (action or ''))
        regime_snap = self._regime_manager.snapshot()
        eff_regime = regime_snap.get('effective_regime', 'mixed')

        rr_floor_default = getattr(self, '_rr_floor_default', 1.50)
        rr_floor_long_bullish = getattr(self, '_rr_floor_long_bullish', 1.30)
        rr_floor_long_aligned = getattr(self, '_rr_floor_long_aligned_choppy', 1.30)
        rr_floor_short_bullish = getattr(self, '_rr_floor_short_bullish', 1.80)
        probe_rr_floor = getattr(self, '_probe_rr_floor', 1.30)
        low_rr_slot_enabled = getattr(self, '_low_rr_slot_enabled', True)
        low_rr_long_aligned_enabled = getattr(self, '_low_rr_long_aligned_enabled', True)
        short_regime_guard_enabled = getattr(self, '_short_regime_guard_enabled', True)
        min_deferred_score = getattr(self, '_min_deferred_signal_score', 45)

        if plan.get('is_probe'):
            return (
                probe_rr_floor,
                'probe',
                f'probe:{eff_regime}',
            )

        if is_long and eff_regime == 'bullish' and low_rr_slot_enabled:
            return (
                rr_floor_long_bullish,
                'long_bullish_low_rr',
                f'long_bullish:regime={eff_regime}',
            )

        if (is_long and eff_regime in ('mixed', 'choppy')
                and low_rr_slot_enabled
                and low_rr_long_aligned_enabled):
            trend = (tech or {}).get('trend', {}) or {}
            entry_timing = (tech or {}).get('entry_timing', {}) or {}
            sym_dir = trend.get('direction', 'neutral')
            htf_bias = trend.get('higher_tf_bias', 'neutral')
            daily_bias = trend.get('daily_bias', 'neutral')
            block_long = entry_timing.get('tf_15m_block_long', False)
            aligned = (sym_dir == 'bullish'
                       and (htf_bias == 'bullish' or daily_bias == 'bullish')
                       and not block_long
                       and abs(score) >= min_deferred_score)
            # trend-entry-rr-fidelity 杠杆① P1:bias 漏报时用入场前客观路径证据补判
            path_evidence = False
            ectx = (tech or {}).get('entry_context', {}) or {}
            if (not aligned
                    and getattr(self, '_path_evidence_aligned_enabled', False)
                    and not block_long
                    and abs(score) >= min_deferred_score):
                strength = trend.get('strength', 0)
                pre12h = ectx.get('pre_12h_return_pct', 0.0)
                range_pos = ectx.get('position_in_24h_range', 0.5)
                path_evidence = (sym_dir == 'bullish'
                                 and strength >= getattr(self, '_path_evidence_min_strength', 60)
                                 and pre12h >= getattr(self, '_path_evidence_min_pre12h_return', 0.03)
                                 and range_pos <= getattr(self, '_path_evidence_max_range_pos', 0.92))
            if aligned:
                return (
                    rr_floor_long_aligned,
                    'long_aligned_low_rr',
                    f'long_aligned:regime={eff_regime},'
                    f'sym_trend={sym_dir},htf={htf_bias},daily={daily_bias}',
                )
            if path_evidence:
                return (
                    rr_floor_long_aligned,
                    'long_aligned_path_evidence',
                    f'long_aligned_path:regime={eff_regime},'
                    f'strength={trend.get("strength")},pre12h={ectx.get("pre_12h_return_pct")},'
                    f'range_pos={ectx.get("position_in_24h_range")}',
                )

        if (not is_long and eff_regime == 'bullish'
                and short_regime_guard_enabled):
            return (
                rr_floor_short_bullish,
                'short_bullish_strong',
                f'short_bullish:regime={eff_regime}',
            )

        return (
            rr_floor_default,
            'default',
            f'default:regime={eff_regime}',
        )

    def _get_rr_policy_label(self, action: str, plan: dict) -> str:
        if not plan:
            return 'default'
        cached = plan.get('rr_policy')
        if cached:
            return cached
        if plan.get('is_probe'):
            return 'probe'
        if plan.get('is_low_rr'):
            slot = plan.get('slot_type', '')
            if slot == 'low_rr_extra':
                regime = self._regime_manager._effective_regime
                if regime == 'bullish':
                    return 'long_bullish_low_rr'
                return 'long_aligned_low_rr'
            return 'long_bullish_low_rr'
        regime = self._regime_manager._effective_regime
        if action == 'open_short' and regime == 'bullish':
            return 'short_bullish_strong'
        return 'default'

    def _can_route_probe_short(self, symbol: str, score: float,
                               confirm_15m_short: bool, rr: float):
        """Check all probe_short prerequisites. Returns (bool, str) tuple."""
        if not self._probe_short_enabled:
            return (False, 'probe_disabled')
        if time.time() <= self._probe_short_cooldown_until:
            return (False, 'probe_cooldown')
        if self._probe_short_active is not None:
            return (False, 'probe_active_full')
        if any(s == 'probe_short' for s in self._pending_open_slots.values()):
            return (False, 'probe_pending_full')
        btc_tech = self._symbol_tech_cache.get('BTC-USDT')
        if not self._regime_manager.is_probe_short_eligible(btc_tech, self._symbol_tech_cache):
            return (False, 'probe_not_eligible')
        if abs(score) < 50:
            return (False, 'score_too_low')
        if not confirm_15m_short:
            return (False, '15m_not_confirmed')
        if rr < getattr(self, '_probe_rr_floor', 1.30):
            return (False, 'rr_too_low')
        sym_tech = self._symbol_tech_cache.get(symbol, {})
        liquidity_score = sym_tech.get('risk', {}).get('liquidity_score', 0)
        if liquidity_score <= 0:
            return (False, 'liquidity_zero')
        return (True, 'probe_eligible')

    def _route_to_probe(self, plan: dict, symbol: str):
        """Mutate plan to probe_short sizing."""
        plan['size_usdt'] = round(self._max_trade_amount * self._probe_short_max_position_pct, 2)
        plan['leverage'] = min(plan.get('leverage', 3), self._probe_short_max_leverage)
        plan['is_probe'] = True
        plan['slot_type'] = 'probe_short'
        plan['probe_trigger_reason'] = 'short_regime_guard_probe'
        plan['probe_evidence'] = {'type': 'probe_short', 'slot_type': 'probe_short'}

    def _can_route_probe_long(self, symbol: str, tech: dict, score: float) -> tuple:
        """Check momentum probe_long prerequisites. Returns (allowed, reason)."""
        if not getattr(self, '_momentum_probe_long_enabled', False):
            return (False, 'probe_long_disabled')
        rsi = tech.get('momentum', {}).get('rsi', 50)
        rsi_min = getattr(self, '_probe_long_rsi_min', 70)
        rsi_max = getattr(self, '_probe_long_rsi_max', 85)
        if rsi < rsi_min or rsi > rsi_max:
            return (False, f'rsi_out_of_range:{rsi:.0f}')
        # Must have strong trend + HTF aligned bullish
        trend = tech.get('trend', {})
        if trend.get('direction') != 'bullish' or trend.get('strength', 0) < 70:
            return (False, 'trend_not_strong_bullish')
        if trend.get('higher_tf_bias') != 'bullish':
            return (False, 'htf_not_bullish')
        # No bearish divergence
        div = tech.get('momentum', {}).get('rsi_divergence')
        if div == 'bearish_div':
            return (False, 'bearish_divergence')
        # Check concurrent probe_long count
        occupied = self._open_positions | self._pending_open_symbols
        all_slots = {**self._position_slots, **self._pending_open_slots}
        probe_long_count = sum(1 for s in occupied if all_slots.get(s) == 'probe_long')
        max_concurrent = getattr(self, '_probe_long_max_concurrent', 1)
        if probe_long_count >= max_concurrent:
            return (False, 'probe_long_slot_full')
        # Liquidity check
        liquidity = tech.get('risk', {}).get('liquidity_score', 0)
        if liquidity <= 0:
            return (False, 'liquidity_zero')
        return (True, 'probe_long_eligible')

    def _route_to_probe_long(self, plan: dict, symbol: str):
        """Mutate plan to probe_long sizing."""
        max_pos = getattr(self, '_probe_long_max_position_pct', 0.3)
        max_lev = getattr(self, '_probe_long_max_leverage', 3)
        plan['size_usdt'] = round(self._max_trade_amount * max_pos, 2)
        plan['leverage'] = min(plan.get('leverage', 3), max_lev)
        plan['is_probe'] = True
        plan['slot_type'] = 'probe_long'
        plan['entry_type'] = 'momentum_probe_long'
        plan['probe_trigger_reason'] = 'rsi_overbought_momentum'
        plan['probe_evidence'] = {'type': 'momentum_probe_long', 'slot_type': 'probe_long'}

    @staticmethod
    def _coalesce_float(*vals, default: float) -> float:
        """Return first non-None value as float; only an absent (None) value
        falls back to default. Unlike `a or b or default`, a present 0.0 is
        preserved (not treated as falsy)."""
        for v in vals:
            if v is not None:
                return float(v)
        return float(default)

    def _classify_short_entry_risk(
        self,
        symbol: str,
        action: str,
        plan: dict,
        tech: dict,
        score: float,
        llm_result: dict = None,
    ) -> dict:
        """Classify short entry risk for main-path parity gate.

        Returns a dict with keys: allowed, decision, reason, llm_short_reversal_risk,
        short_gate_version, metrics.
        """
        def _reject(reason, llm_risk, range_pos, pre_move, rsi_val, htf_bearish):
            return {
                "allowed": False,
                "decision": "reject",
                "reason": reason,
                "llm_short_reversal_risk": llm_risk,
                "short_gate_version": "short_main_path_parity_v1",
                "metrics": {
                    "range_position_24h": range_pos,
                    "pre_12h_return_pct": pre_move,
                    "rsi": rsi_val,
                    "htf_bearish_votes": htf_bearish,
                },
            }

        # Step 1: Not applicable cases — pass immediately
        if not self._short_regime_guard_enabled or 'long' in action or plan.get('is_probe'):
            return {
                "allowed": True,
                "decision": "pass",
                "reason": "",
                "llm_short_reversal_risk": False,
                "short_gate_version": "short_main_path_parity_v1",
                "metrics": {
                    "range_position_24h": 0.5,
                    "pre_12h_return_pct": 0.0,
                    "rsi": 50.0,
                    "htf_bearish_votes": 0,
                },
            }

        # Step 2: Parse LLM reversal risk
        llm_short_reversal_risk = False
        if llm_result:
            _keywords = ['禁止做空', '超卖', '看涨背离', '支撑', '追空风险']
            for field in ['reasoning']:
                val = llm_result.get(field, '') or ''
                if any(kw in val for kw in _keywords):
                    llm_short_reversal_risk = True
                    break
            if not llm_short_reversal_risk:
                for field in ['key_factors', 'risk_warnings']:
                    items = llm_result.get(field) or []
                    for item in items:
                        if isinstance(item, str) and any(kw in item for kw in _keywords):
                            llm_short_reversal_risk = True
                            break
                    if llm_short_reversal_risk:
                        break

        # Step 3: Extract metrics
        trend = tech.get('trend', {}) or {}
        short_ctx = tech.get('short_context', {}) or {}
        entry_ctx = tech.get('entry_context', {}) or {}
        indicators = tech.get('indicators', {}) or {}
        momentum = tech.get('momentum', {}) or {}

        daily_bias = trend.get('daily_bias', 'neutral')
        range_pos = self._coalesce_float(
            short_ctx.get('position_in_24h_range'),
            entry_ctx.get('position_in_24h_range'), default=0.5)
        pre_move = self._coalesce_float(
            short_ctx.get('pre_12h_return_pct'),
            entry_ctx.get('pre_12h_return_pct'), default=0.0)
        rsi_val = self._coalesce_float(
            indicators.get('rsi'), momentum.get('rsi'), default=50.0)
        htf_bearish = sum(
            1 for d in [trend.get('direction'), trend.get('higher_tf_bias'), trend.get('daily_bias')]
            if d == 'bearish'
        )

        # Step 4: Check gates in order
        if daily_bias != 'bearish' and self._short_live_require_daily_bearish:
            return _reject('daily_bearish_required', llm_short_reversal_risk, range_pos, pre_move, rsi_val, htf_bearish)
        if range_pos < self._short_live_min_range_pos:
            return _reject('range_position_too_low', llm_short_reversal_risk, range_pos, pre_move, rsi_val, htf_bearish)
        if pre_move <= self._short_live_max_pre_move:
            return _reject('pre_move_too_deep', llm_short_reversal_risk, range_pos, pre_move, rsi_val, htf_bearish)
        if rsi_val < self._short_live_min_rsi:
            return _reject('rsi_too_low_for_short', llm_short_reversal_risk, range_pos, pre_move, rsi_val, htf_bearish)
        if abs(score) < self._short_live_min_score:
            return _reject('short_score_too_low', llm_short_reversal_risk, range_pos, pre_move, rsi_val, htf_bearish)
        if htf_bearish < self._short_live_min_htf_votes:
            return _reject('htf_votes_insufficient', llm_short_reversal_risk, range_pos, pre_move, rsi_val, htf_bearish)

        # Step 5: Pass
        return {
            "allowed": True,
            "decision": "pass",
            "reason": "",
            "llm_short_reversal_risk": llm_short_reversal_risk,
            "short_gate_version": "short_main_path_parity_v1",
            "metrics": {
                "range_position_24h": range_pos,
                "pre_12h_return_pct": pre_move,
                "rsi": rsi_val,
                "htf_bearish_votes": htf_bearish,
            },
        }

    def _check_entry_position_policy(self, symbol: str, action: str, plan: dict,
                                     tech: dict, score: float, context: str = 'main') -> dict:
        """Long Entry Position Guard + Short side guard 的统一入口。

        返回字典:
            {
                'allowed': bool,             # False 表示拒绝主动 open
                'should_defer': bool,         # True 表示应转 deferred_pullback_overheat
                'reason': str,
                'entry_position_status': str, # normal | overheated | oversold
                'target_price': float,        # deferred 目标价
                'block_reason': str,          # 同 reason，机器可读
                'metrics': {...}
            }

        主路径与 deferred 路径必须共用同一份判定，避免漂移。
        """
        result = {
            'allowed': True,
            'should_defer': False,
            'reason': '',
            'block_reason': '',
            'entry_position_status': 'normal',
            'target_price': 0.0,
            'metrics': {},
        }

        plan = plan or {}
        tech = tech or {}
        is_long = ('long' in (action or ''))

        ctx = tech.get('entry_context') or tech.get('short_context') or {}
        range_pos = self._coalesce_float(ctx.get('position_in_24h_range'), default=0.5)
        pre_move = float(ctx.get('pre_12h_return_pct', 0.0) or 0.0)
        prev_daily = float(ctx.get('prev_daily_return_pct', 0.0) or 0.0)
        result['metrics'] = {
            'position_in_24h_range': round(range_pos, 4),
            'pre_12h_return_pct': round(pre_move, 4),
            'prev_daily_return_pct': round(prev_daily, 4),
        }

        # Long overheat guard
        if (is_long and self._long_live_position_guard_enabled
                and not plan.get('is_probe')):
            max_range = self._long_live_max_range_pos
            max_pre = self._long_live_max_pre_move
            max_daily = self._long_live_max_daily_gain
            daily_gain_range_pos = self._long_live_daily_gain_range_pos

            block_reason = ''
            if range_pos >= max_range:
                block_reason = 'long_overheat_range_pos'
            elif pre_move >= max_pre and range_pos >= daily_gain_range_pos:
                block_reason = 'long_overheat_pre_move'
            elif prev_daily >= max_daily and range_pos >= daily_gain_range_pos:
                block_reason = 'long_overheat_daily_gain'

            if block_reason:
                result['entry_position_status'] = 'overheated'
                result['allowed'] = False
                result['reason'] = block_reason
                result['block_reason'] = block_reason

                signal_price = plan.get('entry_zone', [None])[0] or tech.get('indicators', {}).get('price', 0)
                stop_loss = plan.get('stop_loss')
                atr_pct = tech.get('momentum', {}).get('atr_pct', 0.02) or 0.02
                pullback_pct = max(self._long_live_pullback_min_pct, float(atr_pct))
                target_price = 0.0
                target_valid = False

                if signal_price and stop_loss and signal_price > 0:
                    raw_target = signal_price * (1 - pullback_pct)
                    floor_target = stop_loss * 1.005
                    target_price = max(floor_target, raw_target)
                    if (target_price < signal_price and target_price > stop_loss):
                        target_valid = True

                if target_valid:
                    result['should_defer'] = True
                    result['target_price'] = round(target_price, 6)
                else:
                    result['should_defer'] = False
                    result['reason'] = 'long_overheat_no_valid_pullback_target'
                    result['block_reason'] = 'long_overheat_no_valid_pullback_target'
                return result

        # Short side guard (与 _apply_regime_policy 中保持一致语义；不通过则拒绝主动 open)
        if (not is_long and self._short_regime_guard_enabled
                and not plan.get('is_probe')):
            trend = tech.get('trend', {}) or {}
            entry_timing = tech.get('entry_timing', {}) or {}
            indicators = tech.get('indicators', {}) or {}

            daily_bias = trend.get('daily_bias', 'neutral')
            if self._short_live_require_daily_bearish and daily_bias != 'bearish':
                # 留给 _apply_regime_policy 决定是否走 probe；此处仅打 attribution。
                result['entry_position_status'] = 'normal'
                return result

            if range_pos < self._short_live_min_range_pos:
                result['entry_position_status'] = 'overheated'
                result['allowed'] = False
                result['reason'] = 'range_position_too_low'
                result['block_reason'] = 'range_position_too_low'
                return result

            max_pre_short = self._short_live_max_pre_move
            if pre_move <= max_pre_short:
                result['entry_position_status'] = 'overheated'
                result['allowed'] = False
                result['reason'] = 'pre_move_too_deep'
                result['block_reason'] = 'pre_move_too_deep'
                return result

            rsi_val = indicators.get('rsi', tech.get('momentum', {}).get('rsi', 50))
            if rsi_val < self._short_live_min_rsi:
                result['entry_position_status'] = 'oversold'
                result['allowed'] = False
                result['reason'] = 'rsi_too_low_for_short'
                result['block_reason'] = 'rsi_too_low_for_short'
                return result

        return result

    def _apply_regime_policy(self, symbol: str, action: str, plan: dict,
                             score: float, tech: dict):
        """Apply short guard + dynamic R:R floor + low R:R scaling to a plan.

        Returns rejection reason string if blocked, None if passed (plan may be mutated).
        Used by both main path and deferred paths for consistency.
        """
        is_long = 'long' in action
        regime_snap = self._regime_manager.snapshot()
        eff_regime = regime_snap['effective_regime']

        # ── Short Regime Guard ──
        if (not is_long and self._short_regime_guard_enabled
                and eff_regime == 'bullish'):
            trend = tech.get('trend', {})
            entry_timing = tech.get('entry_timing', {})
            htf_bearish = 0
            for d in [trend.get('direction'), trend.get('higher_tf_bias'), trend.get('daily_bias')]:
                if d == 'bearish':
                    htf_bearish += 1
            rr_for_guard = plan.get('effective_risk_reward_ratio', plan.get('risk_reward_ratio', 0))
            confirm_15m_short = entry_timing.get('tf_15m_confirm_short', False)
            allowed, guard_reason = self._regime_manager.is_short_allowed(
                score, htf_bearish, rr_for_guard, confirm_15m_short,
                trend.get('daily_bias', 'neutral')
            )
            if not allowed:
                probe_ok, probe_reason = self._can_route_probe_short(symbol, score, confirm_15m_short, rr_for_guard)
                if probe_ok:
                    self._route_to_probe(plan, symbol)
                else:
                    self._record_rejected_plan(symbol, action, plan, score, 60,
                                              f"short_regime_guard:{guard_reason}")
                    return f"short_regime_guard:{guard_reason}"
            elif guard_reason == 'degrade_to_probe':
                probe_ok, probe_reason = self._can_route_probe_short(symbol, score, confirm_15m_short, rr_for_guard)
                if probe_ok:
                    self._route_to_probe(plan, symbol)
                else:
                    self._record_rejected_plan(symbol, action, plan, score, 60,
                                              f"degrade_to_probe:{probe_reason}")
                    return f"degrade_to_probe:{probe_reason}"

        # ── Side-Aware Short Entry Gates (all regimes) ──
        if not is_long and self._short_regime_guard_enabled and not plan.get('is_probe'):
            short_gate = self._classify_short_entry_risk(
                symbol, action, plan, tech, score, llm_result=None
            )
            if not short_gate['allowed']:
                reason = short_gate['reason']
                if reason == 'daily_bearish_required':
                    entry_timing = tech.get('entry_timing', {})
                    confirm_15m = entry_timing.get('tf_15m_confirm_short', False)
                    rr_val = plan.get('effective_risk_reward_ratio',
                                      plan.get('risk_reward_ratio', 0))
                    probe_ok, _ = self._can_route_probe_short(
                        symbol, score, confirm_15m, rr_val)
                    if probe_ok:
                        self._route_to_probe(plan, symbol)
                    else:
                        self._record_rejected_plan(
                            symbol, action, plan, score, 60, 'daily_bearish_required')
                        return 'daily_bearish_required'
                else:
                    self._record_rejected_plan(symbol, action, plan, score, 60, reason)
                    return reason

        # ── Dynamic R:R Floor ──
        rr = plan.get('effective_risk_reward_ratio', plan.get('risk_reward_ratio', 0))
        min_rr, rr_policy, rr_reason = self._select_rr_floor(action, plan, tech, score)
        plan['rr_floor_used'] = min_rr
        plan['rr_floor_reason'] = rr_reason
        plan['rr_policy'] = rr_policy
        _trend_snap = (tech or {}).get('trend', {}) or {}
        plan['symbol_trend'] = _trend_snap.get('direction', 'neutral')
        plan['symbol_higher_tf_bias'] = _trend_snap.get('higher_tf_bias', 'neutral')
        plan['symbol_daily_bias'] = _trend_snap.get('daily_bias', 'neutral')

        if rr < min_rr:
            self._record_rejected_plan(symbol, action, plan, score, 60,
                                       f"rr_below_floor:{rr:.2f}<{min_rr:.2f}")
            return f"rr_below_floor:{rr:.2f}<{min_rr:.2f}"

        # ── Low R:R Scaling ──
        low_rr_policies = {'long_bullish_low_rr', 'long_aligned_low_rr', 'long_aligned_path_evidence'}
        if (rr < 1.5 and is_long and rr_policy in low_rr_policies
                and not plan.get('is_probe')):
            rr_scale = min(0.8, max(0.4, (rr - 1.2) / 0.3))
            plan['size_usdt'] = round(plan['size_usdt'] * rr_scale * self._low_rr_max_position_pct, 2)
            plan['leverage'] = min(plan.get('leverage', 5), self._low_rr_max_leverage)
            plan['is_low_rr'] = True
            plan['slot_type'] = 'low_rr_extra'

        return None

    def _capture_state_snapshot(self, symbol: str) -> dict:
        """白名单采集决策时跨决策可变状态（observability-only）。不 pickle 整个对象。"""
        ac = getattr(self, "_archetype_cooldown", None)
        rm = getattr(self, "_regime_manager", None)
        return {
            "_open_positions": list(getattr(self, "_open_positions", set())),
            "_pending_open_symbols": list(getattr(self, "_pending_open_symbols", set())),
            "_pending_open_ts": dict(getattr(self, "_pending_open_ts", {})),
            "_position_slots": dict(getattr(self, "_position_slots", {})),
            "_pending_open_slots": dict(getattr(self, "_pending_open_slots", {})),
            "_archetype_cooldown": {
                "_history": getattr(ac, "_history", {}),
                "_cooldown_until": getattr(ac, "_cooldown_until", {}),
            } if ac is not None else None,
            "_recent_wins": getattr(self, "_recent_wins", 0),
            "_total_completed_trades": getattr(self, "_total_completed_trades", 0),
            "_recent_win_rate": getattr(self, "_recent_win_rate", None),
            "_probe_short_active": getattr(self, "_probe_short_active", None),
            "_probe_short_sl_count": getattr(self, "_probe_short_sl_count", 0),
            "_probe_short_cooldown_until": getattr(self, "_probe_short_cooldown_until", 0.0),
            "_symbol_state": dict(getattr(self, "_symbol_state", {}).get(symbol, {})),
            "_available_balance": getattr(self, "_available_balance", 0.0),
            "_regime_manager": rm.snapshot() if rm is not None and hasattr(rm, "snapshot") else None,
        }

    def _record_rejected_plan(self, symbol: str, action: str, plan: dict,
                              score: float, confidence: float, reason: str,
                              attribution: dict = None):
        """Record a rejected planned signal to the counterfactual ledger."""
        if not self._counterfactual_ledger._enabled or not plan:
            return
        side = 'long' if 'long' in action else 'short'
        regime = self._regime_manager._effective_regime
        attr = attribution or plan.get('attribution') or self._rejection_attribution(action, plan, reason)
        self._counterfactual_ledger.record_rejection(
            symbol, side, plan, regime, score, confidence, reason,
            attribution=attr
        )
        # Counterfactual replay: tape the rejected plan (observability-only).
        # Guarded: tape absence (e.g. partial construction in tests) must never break the decision path.
        if getattr(self, "_decision_tape", None) is not None:
            self._decision_tape.record_decision(build_bundle(
                symbol=symbol, decision="reject",
                request_id=(attr or {}).get("request_id") if isinstance(attr, dict) else None,
                tech_analysis=getattr(self, "_symbol_tech_tape_cache", {}).get(symbol) or {},
                price_at_decision=(plan or {}).get("entry_ref") or (plan or {}).get("entry_price"),
                regime_state=regime,
                llm_output=getattr(self, "_symbol_llm_cache", {}).get(symbol), llm_audit_ref=None,
                trade_decision_output={"reject_reason": reason, "attribution": attr},
                state_snapshot=self._capture_state_snapshot(symbol),
                config_snapshot=dict(getattr(self, "config", {}) or {}),
            ))

    def _rejection_attribution(self, action: str, plan: dict, blocked_by: str,
                               dispatch_path: str = '', tech: dict = None) -> dict:
        """Build minimal attribution for rejected decisions."""
        regime_snap = self._regime_manager.snapshot()
        slot_type = (plan or {}).get('slot_type', 'main')
        if not dispatch_path:
            if 'slot_full' in blocked_by or 'ranked_out' in blocked_by:
                dispatch_path = f"main_{'ranking' if 'ranked' in blocked_by else 'direct'}"
            else:
                dispatch_path = 'main_direct'

        plan_dict = plan or {}
        rr_floor_used = plan_dict.get('rr_floor_used')
        rr_floor_reason = plan_dict.get('rr_floor_reason', '')
        rr_policy = plan_dict.get('rr_policy') or self._get_rr_policy_label(action, plan_dict)
        if rr_floor_used is None and tech is not None:
            rr_floor_used, derived_policy, rr_floor_reason = self._select_rr_floor(
                action, plan_dict, tech, 0
            )
            if not plan_dict.get('rr_policy'):
                rr_policy = derived_policy

        trend = (tech or {}).get('trend', {}) if tech else {}
        symbol_trend = plan_dict.get('symbol_trend', trend.get('direction', 'neutral'))
        symbol_higher_tf_bias = plan_dict.get('symbol_higher_tf_bias',
                                              trend.get('higher_tf_bias', 'neutral'))
        symbol_daily_bias = plan_dict.get('symbol_daily_bias',
                                          trend.get('daily_bias', 'neutral'))

        # Sanity: 防止 rr_floor_reason 字符串与 symbol_* 字段漂移
        if rr_floor_reason and 'sym_trend=' in rr_floor_reason:
            try:
                tagged = dict(
                    kv.split('=', 1) for kv in rr_floor_reason.split(',')
                    if '=' in kv
                )
                expected_pairs = (
                    ('sym_trend', symbol_trend),
                    ('htf', symbol_higher_tf_bias),
                    ('daily', symbol_daily_bias),
                )
                for tag_key, field_val in expected_pairs:
                    tag_val = tagged.get(tag_key)
                    if tag_val and tag_val != field_val:
                        self.logger.warning(
                            f"[Attribution] rr_floor_reason {tag_key}={tag_val} "
                            f"!= field={field_val} (rr_policy={rr_policy})"
                        )
                        break
            except Exception:
                pass
        rejection_attribution = {
            'entry_regime': regime_snap['effective_regime'],
            'raw_regime': regime_snap.get('raw_regime', regime_snap['effective_regime']),
            'regime_confidence': regime_snap.get('confidence', 0),
            'rr_policy': rr_policy,
            'rr_floor_used': rr_floor_used,
            'rr_floor_reason': rr_floor_reason,
            'symbol_trend': symbol_trend,
            'symbol_higher_tf_bias': symbol_higher_tf_bias,
            'symbol_daily_bias': symbol_daily_bias,
            'slot_type': slot_type,
            'is_low_rr': plan_dict.get('is_low_rr', False),
            'is_probe': plan_dict.get('is_probe', False),
            'probe_block_reason': blocked_by if plan_dict.get('is_probe') else '',
            'blocked_by': blocked_by,
            'dispatch_path': dispatch_path,
            # ═══ Long Entry Position Guard attribution ═══
            'entry_position_status': plan_dict.get('entry_position_status', 'normal'),
            'entry_position_block_reason': plan_dict.get('entry_position_block_reason', ''),
            'entry_range_pos_24h': plan_dict.get('entry_range_pos_24h', 0.0),
            'entry_pre_12h_return_pct': plan_dict.get('entry_pre_12h_return_pct', 0.0),
            'entry_prev_daily_return_pct': plan_dict.get('entry_prev_daily_return_pct', 0.0),
            'entry_position_policy': plan_dict.get('entry_position_policy', 'long_overheat_v1'),
            'deferred_target_price': plan_dict.get('deferred_target_price', 0.0),
            'deferred_reason': plan_dict.get('deferred_reason', ''),
            'ev_bucket_key': plan_dict.get('ev_bucket_key', ''),
            'ev_bucket_trade_count': plan_dict.get('ev_bucket_trade_count', 0),
            'ev_bucket_min_trades': plan_dict.get('ev_bucket_min_trades',
                                                  getattr(self, '_ev_bucket_min_trades', 10)),
            'ev_bucket_sparse': plan_dict.get('ev_bucket_sparse', False),
        }
        rejection_attribution['provenance'] = self._summarize_provenance(tech)
        return rejection_attribution

    def _apply_short_gate_attribution(self, attribution: dict, gate: dict) -> dict:
        """Apply short gate classification metadata to attribution.

        Called after _classify_short_entry_risk to merge gate results into
        trade_decision attribution for both pass and reject paths.
        """
        attribution = attribution or {}
        gate = gate or {}
        attribution['short_gate_version'] = gate.get('short_gate_version', 'short_main_path_parity_v1')
        attribution['short_gate_decision'] = gate.get('decision', 'pass')
        attribution['short_gate_reason'] = gate.get('reason', '')
        attribution['llm_short_reversal_risk'] = gate.get('llm_short_reversal_risk', False)
        metrics = gate.get('metrics') or {}
        if metrics:
            attribution['short_gate_metrics'] = metrics
        return attribution

    def _record_sl_hit(self, state: dict, direction: str = None):
        """记录一次SL触发，用于escalating cooldown（参考Freqtrade StoplossGuard）"""
        now = time.time()
        if 'sl_timestamps' not in state:
            state['sl_timestamps'] = []
        state['sl_timestamps'].append(now)
        if direction:
            state['last_sl_direction'] = direction
        cutoff = now - 4 * 3600
        state['sl_timestamps'] = [t for t in state['sl_timestamps'] if t > cutoff]

    def _get_escalating_cooldown(self, state: dict) -> int:
        """根据4h内连续SL次数返回递增冷却时间
        1次=300s, 2次=600s, 3次=1200s, 4次+=3600s
        """
        now = time.time()
        cutoff = now - 4 * 3600
        recent_sl = [t for t in state.get('sl_timestamps', []) if t > cutoff]
        n = len(recent_sl)
        if n <= 1:
            return 300
        elif n == 2:
            return 600
        elif n == 3:
            return 1200
        else:
            return 3600

    def _compute_score(self, tech: dict) -> float:
        """多空评分: +100=极度看多, -100=极度看空, 0=中性

        架构：rule_signal（回测验证83%胜率）为主驱动，其他维度为辅助加减分。
        rule_signal触发时基础分±35，确保能过30分入场门槛。
        """
        score = 0.0

        # ═══ 0. 回测验证信号（主驱动）═══
        rule = tech.get('rule_signal', {})
        if rule.get('entry_long'):
            score += 35
        elif rule.get('entry_short'):
            score -= 35
        # MA alignment持续信号（次驱动）：趋势已建立≥3根K线，无crossover时提供基础分
        elif rule.get('ma_aligned_long'):
            score += 20
        elif rule.get('ma_aligned_short'):
            score -= 20

        momentum = tech.get('momentum', {})
        rsi = momentum.get('rsi', 50)
        div = momentum.get('rsi_divergence')

        # ═══ 极端值保护：RSI超买超卖区域的硬性约束 ═══
        # RSI <= 30: 超卖区，禁止做空（与入场禁令阈值统一）
        # RSI >= 70: 超买区，禁止做多（与入场禁令阈值统一）
        rsi_extreme_bullish = rsi <= 30
        rsi_extreme_bearish = rsi >= 70
        rsi_oversold = rsi < 35
        rsi_overbought = rsi > 65

        # ═══ 1. 趋势 ═══
        trend = tech.get('trend', {})
        direction = trend.get('direction', 'neutral')
        strength = trend.get('strength', 50)
        htf = trend.get('higher_tf_bias', 'neutral')

        # 趋势强度处理：Phase 2 饱和逻辑 vs 旧版线性压扁
        effective_strength = strength
        if getattr(self, '_trend_saturation_enabled', False):
            # Phase 2: 饱和封顶，不再反向压扁
            # strength > 90 → cap at 90 (不惩罚强趋势)
            # 只在有背离+派发迹象时才降权（在后续 div 评分中处理）
            if strength > 90:
                effective_strength = 90
        else:
            # 旧逻辑：趋势强度>90时打折（趋势末期信号）
            if strength > 90:
                effective_strength = 90 - (strength - 90) * 2  # 98→74, 95→80

        if direction == 'bullish' and effective_strength > 70:
            trend_score = 20 * (effective_strength / 100)
            # 超买区域趋势做多打折
            if rsi_overbought:
                trend_score *= 0.3
            score += trend_score
        elif direction == 'bearish' and effective_strength > 70:
            trend_score = 20 * (effective_strength / 100)
            # 超卖区域趋势做空打折
            if rsi_oversold:
                trend_score *= 0.3
            score -= trend_score

        # ═══ 2. RSI背离（权重提升：背离是反转的强信号）═══
        if div == 'bullish_div':
            div_score = 20
            if rsi_extreme_bullish:
                div_score = 35  # 极度超卖+背离=强烈反转信号
            elif rsi_oversold:
                div_score = 28
            # 日线趋势bearish时，1h bullish_div可能是回调而非反转
            if htf == 'bearish' and not rsi_extreme_bullish:
                div_score = min(div_score, 15)
            score += div_score
        elif div == 'bearish_div':
            div_score = 20
            if rsi_extreme_bearish:
                div_score = 35
            elif rsi_overbought:
                div_score = 28
            # 日线趋势bullish时，1h bearish_div可能是回调而非反转
            if htf == 'bullish' and not rsi_extreme_bearish:
                div_score = min(div_score, 15)
            score -= div_score

        # ═══ 3. OI背离 ═══
        mf = tech.get('money_flow', {})
        oi_div = mf.get('oi_price_divergence')
        if oi_div == 'bullish':
            score += 12
        elif oi_div == 'bearish':
            score -= 12

        # ═══ 4. 鲸鱼方向 ═══
        micro = tech.get('microstructure', {})
        whale = micro.get('whale_direction', 'neutral')
        if whale == 'accumulating':
            score += 15
        elif whale == 'distributing':
            score -= 15

        # ═══ 5. 散户反指（条件化：只在趋势中段有效）═══
        crowd = tech.get('crowd', {})
        contrarian = crowd.get('contrarian_signal', 'neutral')
        # 散户反指在RSI极端区域失效：
        # 超卖时散户做多可能是正确的抄底，不应反指做空
        # 超买时散户做空可能是正确的逃顶，不应反指做多
        if contrarian == 'bullish' and not rsi_extreme_bearish:
            score += 8
        elif contrarian == 'bearish' and not rsi_extreme_bullish:
            score -= 8

        # ═══ 6. Taker压力 ═══
        taker = mf.get('taker_pressure', 'neutral')
        if taker == 'buy':
            score += 8
        elif taker == 'sell':
            score -= 8

        # ═══ 7. 高时间框架偏向 ═══
        if htf == 'bullish':
            score += 10
        elif htf == 'bearish':
            score -= 10

        # ═══ 极端值硬性保护 ═══
        # RSI<=30 超卖区禁止做空。分级cap：越深越严
        if rsi_extreme_bullish:
            if rsi < 20:
                cap = -15
            elif rsi < 25:
                cap = -20 if htf == 'bearish' else -15
            else:
                cap = -25 if htf == 'bearish' else -20
            if score < cap:
                score = cap
            if div == 'bullish_div':
                score = max(score, 25)

        # RSI>=70 超买区禁止做多。分级cap：越高越严
        if rsi_extreme_bearish:
            if rsi > 80:
                cap = 15
            elif rsi > 75:
                cap = 20 if htf == 'bullish' else 15
            else:
                cap = 25 if htf == 'bullish' else 20
            if score > cap:
                score = cap
            if div == 'bearish_div':
                score = min(score, -25)

        # ═══ 4h RSI 二级保护（2026-05-19）═══
        # 根因：ZEC事故 1h RSI=64（未触发1h硬cap）但 4h RSI=73.9 超买区，仍开多→亏-135
        # Phase 2: 动态折扣（不再固定50%一刀切）
        # 70-75: 轻度衰减30%, 75-80: 中度衰减50%, >80: 重度衰减70%
        rsi_4h = trend.get('tf_4h_rsi')
        if rsi_4h is not None:
            if rsi_4h >= 70 and score > 0:
                if getattr(self, '_trend_saturation_enabled', False):
                    if rsi_4h > 80:
                        score *= 0.3
                    elif rsi_4h > 75:
                        score *= 0.5
                    else:
                        score *= 0.7
                else:
                    score *= 0.5
            elif rsi_4h <= 30 and score < 0:
                if getattr(self, '_trend_saturation_enabled', False):
                    if rsi_4h < 20:
                        score *= 0.3
                    elif rsi_4h < 25:
                        score *= 0.5
                    else:
                        score *= 0.7
                else:
                    score *= 0.5

        return score

    def _check_price_in(self, symbol: str, action: str, tech: dict) -> bool:
        """检测催化剂是否已price-in：新闻发布后4h内价格已同向移动>3%则认为已消化

        设计参考：QuantConnect Alpha Streams研究——新闻情绪在4h后衰减至噪音
        price-in判断：新闻方向 × 价格变动方向一致 且 变动幅度>3%
        """
        base = symbol.split('-')[0].upper()
        headlines = self._news_snapshot.get(base, [])
        if not headlines:
            return False

        now = time.time()
        window = 4 * 3600
        recent = [h for h in headlines if h.get('published_ts') and now - h['published_ts'] <= window]
        if not recent:
            return False

        # 用24h涨跌幅作为价格移动代理（tech已包含此数据）
        change_pct = abs(tech.get('trend', {}).get('change_24h_pct', 0))
        if change_pct < 3.0:
            return False

        # 方向一致性检查：新闻利好+价格已大涨 或 新闻利空+价格已大跌
        price_direction = 'up' if tech.get('trend', {}).get('direction') == 'bullish' else 'down'
        # 简单启发：有近期新闻 + 价格已大幅移动 = 催化剂已price-in
        self.logger.info(
            f"[Judge] {symbol} 检测到price-in: 近{len(recent)}条新闻 + 24h变动{change_pct:.1f}%，score衰减50%"
        )
        return True

    # ═══ 交易计划构建 ═══

    # trend-entry-rr-fidelity 杠杆② v1:阶梯离场比例加权 effective_rr(Option B,无概率折扣)
    _LADDER_WEIGHTS = (0.50, 0.25, 0.25)      # 对齐 executor 50/25/25 真实离场比例

    def _compute_ladder_rr(self, tp_dists, sl_dist, notional, gross_loss, total_cost):
        """按真实阶梯离场比例加权的 effective_rr(与旧口径同"目标达成"假设)。

        - tp_dists: 各 TP 档距离(占比),升序;不足 3 档则权重归一到现有档。
        - 剩余 trailing 档(第3档)的盈利距离保守封顶 min(tp_dist3, sl_dist),即至多记 +1R 锁利。
        - 不施加 P(reach tier) 概率折扣:旧 TP1-only 口径本就隐含 TP1 必达,只对新口径缩分子
          而不缩阶梯化后降低的风险分母会反向压低 R:R(v2 才做相干的概率+风险口径)。
        """
        if not tp_dists or sl_dist <= 0:
            return 1.0
        weights = list(self._LADDER_WEIGHTS[:len(tp_dists)])
        wsum = sum(weights)
        if wsum <= 0:
            return 1.0
        weights = [w / wsum for w in weights]   # 缺档归一化
        exp_profit = 0.0
        for i, dist in enumerate(tp_dists):
            d = dist
            if i == 2:  # 剩余 trailing 档:保守 +1R 锁利上限
                d = min(dist, sl_dist)
            exp_profit += weights[i] * (notional * d)
        denom = gross_loss + total_cost
        if denom <= 0:
            return 1.0
        return round((exp_profit - total_cost) / denom, 2)

    def _build_plan(self, tech: dict, action: str, price: float, confidence: int, score: float = 50) -> dict:
        levels = tech.get('levels', {})
        risk = tech.get('risk', {})
        micro = tech.get('microstructure', {})
        momentum = tech.get('momentum', {})
        trend = tech.get('trend', {})

        is_long = (action == 'open_long')

        stop_loss = self._calc_stop_loss(levels, price, is_long, trend, momentum)
        sl_dist = abs(price - stop_loss) / price

        budget = self._calc_risk_budget(tech, action, sl_dist, score)
        leverage = budget['leverage']
        size_usdt = budget['size_usdt']

        take_profit = self._calc_take_profit(levels, price, is_long, trend, momentum, stop_loss)
        entry_zone = self._calc_entry_zone(price, micro, momentum, is_long)
        order_type = self._calc_order_type(momentum, micro, score)

        tp_dist = abs(take_profit[0] - price) / price if take_profit else sl_dist
        gross_rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 1.0

        notional = budget['notional_usdt']
        gross_profit = notional * tp_dist
        gross_loss = budget['max_loss_usdt']
        total_cost = budget['total_cost_usdt']
        effective_rr = round((gross_profit - total_cost) / (gross_loss + total_cost), 2) if (gross_loss + total_cost) > 0 else 1.0

        # ═══ P2-N: 期望值（EV）计算 ═══
        # EV = p_win × net_profit − (1 − p_win) × net_loss
        # 用历史滚动胜率（不足时降级）
        p_win, p_win_source = self._get_p_win()
        net_profit = max(0.0, gross_profit - total_cost)
        net_loss = gross_loss + total_cost
        expected_value = p_win * net_profit - (1 - p_win) * net_loss

        self.logger.info(
            f"[Plan] price={price:.4f} sl={stop_loss:.4f}({sl_dist:.3f}) "
            f"tp={take_profit[0]:.4f}({tp_dist:.3f}) atr={momentum.get('atr_pct',0):.4f} "
            f"R:R={effective_rr}(gross={gross_rr}) lev={leverage}x size={size_usdt:.2f} "
            f"funding_cost={budget['funding_cost_usdt']:.3f} fee={budget['fee_cost_usdt']:.3f} "
            f"EV={expected_value:+.3f} p_win={p_win:.1%}({p_win_source})"
        )

        def price_round(x):
            from math import log10, floor
            if x <= 0:
                return x
            digits = max(2, 4 - int(floor(log10(abs(x)))) - 1)
            return round(x, digits)

        sl_rounded = price_round(stop_loss)
        sl_pct_value = abs(sl_rounded - price) / price if price > 0 else 0.0
        tp_rounded = [price_round(tp) for tp in take_profit]
        tp_pct_values = [abs(tp - price) / price for tp in tp_rounded] if price > 0 else []

        return {
            "side": "long" if is_long else "short",
            "entry_ref": price_round(price),
            "sl_pct": round(sl_pct_value, 6),
            "tp_pct": [round(p, 6) for p in tp_pct_values],
            "entry_zone": [price_round(e) for e in entry_zone],
            "stop_loss": sl_rounded,
            "take_profit": tp_rounded,
            "leverage": leverage,
            "size_usdt": size_usdt,
            "order_type": order_type,
            "risk_reward_ratio": gross_rr,
            "effective_risk_reward_ratio": effective_rr,
            "funding_cost": round(budget['funding_cost_usdt'], 3),
            "est_hold_hours": budget['est_hold_hours'],
            "max_holding_hours": 24,
            "atr_pct": momentum.get('atr_pct', 0.02),
            "expected_value": round(expected_value, 4),
            "p_win_used": round(p_win, 3),
            "p_win_source": p_win_source,
            "net_profit_usdt": round(net_profit, 3),
            "net_loss_usdt": round(net_loss, 3),
        }

    def _get_p_win(self) -> tuple:
        """获取应用 EV 公式的胜率，返回 (p_win, source)。

        RQ-01 保守化：使用 Bayesian 后验胜率替代固定 fallback。
        p_win = min(fallback, (wins + prior_wins) / (trades + prior_total))
        当样本不足时，prior 拉向保守方向（prior_wins=2, prior_total=5 → 先验40%）。
        """
        if (self._recent_win_rate is not None and
            self._total_completed_trades >= self._min_trades_for_ev_gate):
            return float(self._recent_win_rate), "rolling"

        # 小样本阶段：Bayesian 后验保守化
        wins = self._recent_wins
        trades = self._total_completed_trades
        posterior = (wins + self._ev_prior_wins) / (trades + self._ev_prior_total)
        p_win = min(float(self._fallback_win_rate), posterior)
        return p_win, "bayesian_prior"

    def _check_expected_value(self, symbol: str, plan: dict, score: float) -> bool:
        """期望值门：EV < 阈值则拒绝开仓。

        返回 True 通过 / False 拦截。

        Phase 2 EPIC D: 分桶 EV 门
        - 按 side/regime/entry_type/slot_type 分桶查找胜率
        - 样本不足时缩仓不冻结（position_scale降低，不直接拒绝强信号）
        - AC-LONGPOS-12: 稀疏 bucket（trade_count < ev_bucket_min_trades）不得抬高 p_win，
          只允许降低 p_win 或缩仓。可由 EV_BUCKET_SPARSE_ALLOW_UPLIFT 显式开关。
        """
        ev = plan.get('expected_value', 0.0)
        p_win = plan.get('p_win_used', 0.5)
        p_win_source = plan.get('p_win_source', 'fallback')

        # Phase 2: 分桶 EV
        if getattr(self, '_bucketed_ev_enabled', False):
            bucket_info = self._get_bucketed_ev_info(plan, score)
            if bucket_info:
                bucket_p_win = bucket_info['p_win']
                bucket_min = getattr(self, '_ev_bucket_min_trades', 10)
                allow_uplift = getattr(self, '_ev_bucket_sparse_allow_uplift', False)
                trade_count = bucket_info.get('trade_count', 0)
                is_sparse = trade_count < bucket_min
                plan['ev_bucket_key'] = bucket_info['bucket_key']
                plan['ev_bucket_trade_count'] = trade_count
                plan['ev_bucket_min_trades'] = bucket_min
                plan['ev_bucket_sparse'] = is_sparse
                # AC-LONGPOS-12: sparse bucket 不能把 p_win 抬到当前值之上
                if is_sparse and (not allow_uplift) and bucket_p_win > p_win:
                    plan['p_win_source'] = (
                        f"{p_win_source}+sparse_bucket_capped:{bucket_info['bucket_key']}"
                    )
                    self.logger.info(
                        f"[Judge] {symbol} sparse bucket "
                        f"({bucket_info['bucket_key']}, n={trade_count}/{bucket_min}) "
                        f"would uplift p_win {p_win:.3f}->{bucket_p_win:.3f}; capping per "
                        f"EV_BUCKET_SPARSE_ALLOW_UPLIFT=False"
                    )
                else:
                    p_win = bucket_p_win
                    p_win_source = bucket_info['source']
                    # Recalculate EV with bucketed p_win
                    net_profit = plan.get('net_profit_usdt', 0)
                    net_loss = plan.get('net_loss_usdt', 0)
                    ev = p_win * net_profit - (1 - p_win) * net_loss
                    plan['expected_value'] = round(ev, 4)
                    plan['p_win_used'] = round(p_win, 3)
                    plan['p_win_source'] = p_win_source
                # 样本不足时强信号缩仓不冻结，保持 phase2 EPIC D 既有行为。
                if is_sparse and abs(score) >= self._ev_strong_signal_threshold:
                    plan['size_usdt'] = round(plan['size_usdt'] * 0.6, 2)
                    self.logger.info(
                        f"[Judge] {symbol} bucketed EV insufficient sample, "
                        f"缩仓60% (bucket={bucket_info['bucket_key']}, n={trade_count})"
                    )
                    return True

        # 系统衰减严重时（胜率<0.4）只允许极强信号通过
        effective_win_rate = p_win if p_win_source == 'rolling' else (
            self._recent_win_rate if self._recent_win_rate is not None else p_win
        )
        if (effective_win_rate < 0.4 and abs(score) < self._ev_strong_signal_threshold):
            self.logger.warning(
                f"[Judge] {symbol} 胜率{effective_win_rate:.1%}<40% 且 "
                f"score={score:.0f}<{self._ev_strong_signal_threshold}，EV门强拒 "
                f"(source={p_win_source})"
            )
            return False

        if ev < self._ev_min_threshold:
            # 强信号豁免：score 极强 + EV 微负 (>=-0.3 USDT) 允许通过
            if abs(score) >= self._ev_strong_signal_threshold and ev >= -0.3:
                self.logger.warning(
                    f"[Judge] {symbol} EV={ev:.3f}<0 但 score={score:.0f}>="
                    f"{self._ev_strong_signal_threshold} 强信号，豁免通过"
                )
                return True
            self.logger.info(
                f"[Judge] {symbol} EV={ev:.3f}<{self._ev_min_threshold} "
                f"(p_win={p_win:.1%}/{p_win_source}, score={score:.0f})，拦截"
            )
            return False

        return True

    def _get_bucketed_ev_info(self, plan: dict, score: float) -> dict:
        """Phase 2 EPIC D: 从 Reviewer 分桶指标中查找对应桶的胜率。"""
        if not hasattr(self, '_bucketed_metrics') or not self._bucketed_metrics:
            return None
        # Side 来源优先级：plan["side"] > plan["action"] 推断 > score 符号推断
        explicit_side = plan.get('side')
        if explicit_side in ('long', 'short'):
            side = explicit_side
        else:
            action = plan.get('action', '')
            if 'short' in action:
                side = 'short'
            elif 'long' in action:
                side = 'long'
            else:
                side = 'short' if score < 0 else 'long'
            self.logger.warning(
                f"[Judge] bucketed EV: plan缺少side字段, 推断side={side} (action={action}, score={score:.0f})"
            )
        regime = self._regime_manager._effective_regime
        entry_type = plan.get('entry_type', 'unknown')
        slot_type = plan.get('slot_type', 'main')
        bucket_key = f"{side}_{regime}_{entry_type}_{slot_type}"

        bucket = self._bucketed_metrics.get(bucket_key)
        if not bucket:
            # Fallback: try side-only bucket
            bucket = self._bucketed_metrics.get(f"side_{side}")
        if not bucket:
            return None

        trade_count = bucket.get('trade_count', 0)
        bucket_min = getattr(self, '_ev_bucket_min_trades', 10)
        # 兼容旧实现的 5 阈值；若启用新阈值则按其判定 sparse
        insufficient = trade_count < bucket_min
        p_win = bucket.get('win_rate', self._fallback_win_rate)
        return {
            'p_win': p_win,
            'source': f'bucket:{bucket_key}' if not insufficient else f'bucket_sparse:{bucket_key}',
            'insufficient_sample': insufficient,
            'bucket_key': bucket_key,
            'trade_count': trade_count,
        }

    def _parse_segmented_metrics(self, segmented: dict) -> dict:
        """Parse Reviewer segmented_metrics into flat bucket lookup dict."""
        buckets = {}
        for key_type in ('metrics_by_side', 'metrics_by_regime', 'metrics_by_slot_type'):
            for k, v in segmented.get(key_type, {}).items():
                prefix = key_type.replace('metrics_by_', '')
                v = self._normalize_bucket_win_rate(v)
                if not v.get('invalid_metric'):
                    buckets[f"{prefix}_{k}"] = v
        for k, v in segmented.get('metrics_by_bucket', {}).items():
            v = self._normalize_bucket_win_rate(v)
            if not v.get('invalid_metric'):
                buckets[k] = v
        return buckets

    def _normalize_bucket_win_rate(self, bucket: dict) -> dict:
        """Ensure win_rate is in ratio format (0-1). Convert percentage if detected.
        Invalid values (>100 or <0) are marked invalid and excluded from EV."""
        wr = bucket.get('win_rate', bucket.get('win_rate_ratio', 0))
        if wr < 0:
            self.logger.error(f"[Judge] invalid bucket win_rate={wr} (<0), marking invalid")
            bucket['invalid_metric'] = True
            bucket['win_rate_ratio'] = 0
            bucket['win_rate_pct'] = 0
            return bucket
        if wr > 100:
            self.logger.error(f"[Judge] invalid bucket win_rate={wr} (>100), marking invalid")
            bucket['invalid_metric'] = True
            bucket['win_rate_ratio'] = 0
            bucket['win_rate_pct'] = 0
            return bucket
        if wr > 1.0:
            wr = wr / 100.0
            self.logger.warning(f"[Judge] bucket win_rate>{1} detected, converting to ratio: {wr:.3f}")
        bucket['win_rate'] = wr
        bucket['win_rate_ratio'] = wr
        bucket['win_rate_pct'] = round(wr * 100, 1)
        if 'gross_profit' not in bucket:
            bucket['gross_profit'] = bucket.get('total_pnl', 0) if bucket.get('total_pnl', 0) > 0 else 0
        if 'gross_loss' not in bucket:
            bucket['gross_loss'] = abs(bucket.get('total_pnl', 0)) if bucket.get('total_pnl', 0) < 0 else 0
        return bucket

    def _calc_stop_loss(self, levels: dict, price: float, is_long: bool, trend: dict = None, momentum: dict = None) -> float:
        min_sl_pct = 0.015
        max_sl_pct = 0.05
        strength = (trend or {}).get('strength', 50)
        if strength >= 80:
            min_sl_pct = 0.025
        elif strength >= 60:
            min_sl_pct = 0.02

        # ATR-based cap: SL不超过2.5倍ATR（Turtle Traders方法论）
        atr_pct = (momentum or {}).get('atr_pct', 0.02)
        atr_sl_cap = min(max_sl_pct, atr_pct * 2.5)
        effective_max = max(min_sl_pct + 0.005, atr_sl_cap)

        if is_long:
            for key in ['daily_support', 'h4_support', 'support']:
                candidates = [s for s in levels.get(key, []) if price * (1 - effective_max) < s < price * (1 - min_sl_pct)]
                if candidates:
                    return candidates[0] * 0.995
            return price * (1 - min_sl_pct)
        else:
            for key in ['daily_resistance', 'h4_resistance', 'resistance']:
                candidates = [r for r in levels.get(key, []) if price * (1 + min_sl_pct) < r < price * (1 + effective_max)]
                if candidates:
                    return candidates[0] * 1.005
            return price * (1 + min_sl_pct)

    def _calc_take_profit(self, levels: dict, price: float, is_long: bool,
                           trend: dict = None, momentum: dict = None, stop_loss: float = None) -> list:
        trend = trend or {}
        momentum = momentum or {}
        strength = trend.get('strength', 50)
        direction = trend.get('direction', 'neutral')
        atr_pct = max(momentum.get('atr_pct', 0.02), 0.01)

        # 强趋势模式：趋势强度>80且方向与开仓一致时，用ATR倍数止盈
        strong_trend = (strength >= 80 and
                        ((direction == 'bearish' and not is_long) or
                         (direction == 'bullish' and is_long)))

        rsi = momentum.get('rsi', 50)

        # 止盈下限：不能小于止损距离的1.5倍（配合R:R>=1.5硬性门槛）
        sl_dist = abs(price - stop_loss) / price if stop_loss else 0.015
        min_tp_pct = sl_dist * 1.5

        if is_long:
            if strong_trend or rsi < 20:
                tps = [price * (1 + atr_pct * m) for m in [1.5, 2.5, 3.5]]
                if abs(tps[0] - price) / price < min_tp_pct:
                    tps = [price * (1 + min_tp_pct * m) for m in [1.0, 2.0, 3.0]]
                return tps
            resistances = levels.get('resistance', [])
            wall = levels.get('orderbook_wall_above')
            min_tp_dist = price * 0.005
            tps = []
            for r in resistances[:3]:
                if r <= price + min_tp_dist:
                    continue
                if wall and r >= wall:
                    tp = wall * 0.998
                    if tp > price + min_tp_dist:
                        tps.append(tp)
                    break
                tps.append(r)
            if not tps:
                tps = [price * 1.02, price * 1.04, price * 1.06]
            # ATR保底：止盈距离不能低于1×ATR
            if tps and atr_pct > 0 and abs(tps[0] - price) / price < atr_pct:
                tps = [price * (1 + atr_pct * m) for m in [1.0, 2.0, 3.0]]
            # R:R保底：TP1 >= SL距离×1.5
            if tps and abs(tps[0] - price) / price < min_tp_pct:
                tps = [price * (1 + min_tp_pct * m) for m in [1.0, 2.0, 3.0]]
            return tps
        else:
            if strong_trend or rsi > 80:
                tps = [price * (1 - atr_pct * m) for m in [1.5, 2.5, 3.5]]
                if abs(tps[0] - price) / price < min_tp_pct:
                    tps = [price * (1 - min_tp_pct * m) for m in [1.0, 2.0, 3.0]]
                return tps
            supports = levels.get('support', [])
            wall = levels.get('orderbook_wall_below')
            min_tp_dist = price * 0.005
            tps = []
            for s in supports[:3]:
                if s >= price - min_tp_dist:
                    continue
                if wall and s <= wall:
                    tp = wall * 1.002
                    if tp < price - min_tp_dist:
                        tps.append(tp)
                    break
                tps.append(s)
            if not tps:
                tps = [price * 0.98, price * 0.96, price * 0.94]
            # ATR保底
            if tps and atr_pct > 0 and abs(tps[0] - price) / price < atr_pct:
                tps = [price * (1 - atr_pct * m) for m in [1.0, 2.0, 3.0]]
            # R:R保底
            if tps and abs(tps[0] - price) / price < min_tp_pct:
                tps = [price * (1 - min_tp_pct * m) for m in [1.0, 2.0, 3.0]]
            return tps

    # 数据回测验收的常量（21 笔真实样本，net=+23.03U vs baseline=-19.86U）。
    PULLBACK_ATR_ENTRY_TYPES = ('ma_aligned', 'momentum_probe_long')
    PULLBACK_ATR_PCT = 0.005       # 0.5% 限价回调
    PULLBACK_ATR_SL_MULT = 2.0     # SL = 2.0 × ATR
    PULLBACK_ATR_TP_R = 2.0        # TP1 = 2R
    PULLBACK_LIMIT_TIMEOUT_SEC = 1800  # 30 分钟
    PULLBACK_ZONE_HALF_TICK = 0.0005   # 限价区间 ±0.05%（窄区间 ≈ 单一限价）

    def _apply_pullback_atr_policy(self, plan: dict, tech: dict, entry_type: str) -> dict:
        """单一收口：对 ma_aligned / momentum_probe_long 主路径改写为
        「30 分钟内 0.5% 回调限价 + 2.0×ATR SL + 2R TP + 不走市价 fallback」。

        其他 entry_type（deferred_15m_confirmation/deferred_pullback*/deferred_chase）保持原 plan 不动。
        R:R floor 不达标时也保留原 plan，避免与 _select_rr_floor 兜底冲突。

        Why: 21 笔真实 closed live 仓位 + OKX 1m 真实路径回测显示，
             baseline 实际 PnL=-19.86U，应用此策略后 net=+23.03U（fills=19/21）。
        How to apply: _build_plan 末尾在 plan['entry_type'] 已知时调用一次。
        """
        if entry_type not in self.PULLBACK_ATR_ENTRY_TYPES:
            return plan
        atr_pct = float(plan.get('atr_pct') or 0.0)
        if atr_pct <= 0:
            return plan
        side = plan.get('side')
        entry_ref = float(plan.get('entry_ref') or 0.0)
        if entry_ref <= 0 or side not in ('long', 'short'):
            return plan

        sl_dist_pct = self.PULLBACK_ATR_SL_MULT * atr_pct
        tp_dist_pct = self.PULLBACK_ATR_TP_R * sl_dist_pct
        if sl_dist_pct <= 0 or sl_dist_pct >= 0.5:
            return plan

        original_tp = plan.get('take_profit') or []
        if side == 'long':
            target = entry_ref * (1 - self.PULLBACK_ATR_PCT)
            stop_loss = target * (1 - sl_dist_pct)
            tp_first = target * (1 + tp_dist_pct)
            tp_levels = [tp_first]
            for m in (1.5 * self.PULLBACK_ATR_TP_R, 2.0 * self.PULLBACK_ATR_TP_R):
                tp_levels.append(target * (1 + m * sl_dist_pct))
        else:
            target = entry_ref * (1 + self.PULLBACK_ATR_PCT)
            stop_loss = target * (1 + sl_dist_pct)
            tp_first = target * (1 - tp_dist_pct)
            tp_levels = [tp_first]
            for m in (1.5 * self.PULLBACK_ATR_TP_R, 2.0 * self.PULLBACK_ATR_TP_R):
                tp_levels.append(target * (1 - m * sl_dist_pct))

        from math import log10, floor
        def _round(x):
            if x <= 0:
                return x
            digits = max(2, 4 - int(floor(log10(abs(x)))) - 1)
            return round(x, digits)

        target_r = _round(target)
        sl_r = _round(stop_loss)
        tp_r = [_round(t) for t in tp_levels]

        zone_low = target_r * (1 - self.PULLBACK_ZONE_HALF_TICK)
        zone_high = target_r * (1 + self.PULLBACK_ZONE_HALF_TICK)
        entry_zone = [_round(zone_low), _round(zone_high)]

        # R:R floor 校验：用 _select_rr_floor 单一函数获取当前 floor
        gross_rr = tp_dist_pct / sl_dist_pct if sl_dist_pct > 0 else 0
        try:
            action_for_floor = ('open_long' if side == 'long' else 'open_short')
            min_rr, _, _ = self._select_rr_floor(action_for_floor, plan, tech, 0.0)
        except Exception:
            min_rr = 1.5
        if gross_rr < min_rr:
            return plan

        new_plan = dict(plan)
        new_plan['entry_zone'] = entry_zone
        new_plan['stop_loss'] = sl_r
        new_plan['take_profit'] = tp_r
        new_plan['sl_pct'] = round(abs(sl_r - target_r) / target_r if target_r else sl_dist_pct, 6)
        new_plan['tp_pct'] = [round(abs(t - target_r) / target_r if target_r else 0.0, 6) for t in tp_r]
        new_plan['order_type'] = 'limit'
        new_plan['limit_timeout_sec'] = self.PULLBACK_LIMIT_TIMEOUT_SEC
        new_plan['limit_no_fallback'] = True
        new_plan['risk_reward_ratio'] = round(gross_rr, 2)
        # entry_ref 保持原信号价（让 entry drift Gate 仍以原信号价为基准）
        new_plan['pullback_target'] = target_r
        new_plan['pullback_policy'] = 'pullback_atr_v1'
        new_plan['atr_sl_multiplier'] = self.PULLBACK_ATR_SL_MULT
        new_plan['atr_tp_r'] = self.PULLBACK_ATR_TP_R
        return new_plan

    def _calc_risk_budget(self, tech: dict, action: str, sl_dist: float, score: float = 50) -> dict:
        """统一风险预算：从风险约束推导杠杆和仓位

        核心公式：leverage = max_loss / (margin × sl_dist) = 0.5 / sl_dist
        固定保证金(余额10%) + 固定最大亏损(余额5%) → 杠杆由止损距离决定

        逻辑账户拆分：effective_balance_cap 设置时，% 计算用 min(real, cap)。
        实盘真实下单仍用真实账户，但风险预算被 cap 约束在小额，便于早期实测。
        """
        real_balance = self._available_balance if self._available_balance > 0 else self._max_trade_amount * 3

        # 逻辑账户拆分：仅在 cap 设置时生效
        if self._effective_balance_cap and self._effective_balance_cap > 0:
            balance = min(real_balance, self._effective_balance_cap)
        else:
            balance = real_balance

        MARGIN_PCT = 0.10
        MAX_LOSS_PCT = 0.05

        margin_usdt = min(balance * MARGIN_PCT, self._max_trade_amount)
        max_loss_usdt = balance * MAX_LOSS_PCT

        if sl_dist <= 0:
            sl_dist = 0.02

        raw_leverage = max_loss_usdt / (margin_usdt * sl_dist)
        leverage = max(1, min(20, int(raw_leverage)))

        # 弱信号杠杆上限：score<35时cap 10x（信号弱不该用高杠杆放大风险）
        if abs(score) < 35:
            leverage = min(leverage, 10)

        # 向下圆整到OKX允许值（保证max_loss不超预算）
        allowed = [1, 2, 3, 5, 10, 20]
        final_lev = allowed[0]
        for lev in allowed:
            if lev <= leverage:
                final_lev = lev
            else:
                break
        leverage = final_lev

        # size_usdt = 保证金（Executor会乘leverage得到名义价值）
        size_usdt = round(float(margin_usdt), 2)
        notional = margin_usdt * leverage
        actual_max_loss = margin_usdt * sl_dist * leverage

        money_flow = tech.get('money_flow', {})
        current_funding = money_flow.get('funding_rate', 0)

        atr_pct = tech.get('momentum', {}).get('atr_pct', 0.02)
        if atr_pct >= 0.03:
            est_hours = 4
        elif atr_pct >= 0.015:
            est_hours = 8
        else:
            est_hours = 16

        is_long = (action == 'open_long')
        # P1-J: 通过 CostModel 统一计算 funding 成本和手续费（与 executor 平仓 PnL 口径一致）
        side = 'long' if is_long else 'short'
        try:
            from utils.cost_model import get_default_cost_model
            cm = get_default_cost_model()
            funding_cost_usdt = cm.funding_cost(
                notional=notional, funding_rate=current_funding,
                hold_hours=est_hours, side=side
            )
            fee_cost_usdt = cm.round_trip_fee(notional)
        except Exception:
            # 降级：使用旧公式
            funding_rate_abs = abs(current_funding)
            if is_long:
                funding_direction_mult = 1.0 if current_funding > 0 else 0
            else:
                funding_direction_mult = 0 if current_funding > 0 else 1.0
            funding_periods = est_hours / 8
            funding_cost_usdt = notional * funding_rate_abs * funding_periods * funding_direction_mult
            fee_cost_usdt = notional * 0.002  # round-trip taker
        total_cost_usdt = max(0, funding_cost_usdt) + fee_cost_usdt

        return {
            "leverage": leverage,
            "size_usdt": size_usdt,
            "margin_usdt": margin_usdt,
            "notional_usdt": notional,
            "max_loss_usdt": actual_max_loss,
            "funding_cost_usdt": max(0, funding_cost_usdt),
            "fee_cost_usdt": fee_cost_usdt,
            "total_cost_usdt": total_cost_usdt,
            "est_hold_hours": est_hours,
        }

    def _calc_entry_zone(self, price: float, micro: dict, momentum: dict, is_long: bool = True) -> list:
        spread = micro.get('spread_pct', 0.01)
        offset = max(spread * 2, 0.03) / 100 * price
        if is_long:
            return [price - offset * 2, price - offset * 0.5]
        else:
            return [price + offset * 0.5, price + offset * 2]

    def _calc_order_type(self, momentum: dict, micro: dict, score: float = 0) -> str:
        # 高置信度(|score|>=80) 改用市价单：接受滑点换确定性，防止限价单挂单期间趋势走掉
        if abs(score) >= 80:
            return "market"
        if momentum.get('volume_anomaly') or micro.get('liquidation_intensity') == 'high':
            return "market"
        return "limit"

    # ═══ LLM决策 ═══

    async def _ask_llm(self, symbol: str, tech: dict, rule_score: float) -> dict:
        trend = tech.get('trend', {})
        levels = tech.get('levels', {})
        momentum = tech.get('momentum', {})
        mf = tech.get('money_flow', {})
        micro = tech.get('microstructure', {})
        crowd = tech.get('crowd', {})
        risk = tech.get('risk', {})
        llm_ta = tech.get('llm_analysis', {})
        price = tech.get('indicators', {}).get('price', 0)

        user_msg = f"""交易对: {symbol} | 当前价: {price:.2f} | 规则评分: {rule_score:+.0f}/100

【趋势】{trend.get('direction')} 强度={trend.get('strength')} 1h均线={trend.get('ma_alignment')} 4h={trend.get('higher_tf_bias')}
【价位】支撑={levels.get('support',[])} 阻力={levels.get('resistance',[])}
【动量】RSI={momentum.get('rsi',50):.1f} 背离={momentum.get('rsi_divergence')} 量比={momentum.get('volume_ratio',1)}
【资金】费率={mf.get('funding_rate',0):.6f}({mf.get('funding_trend')}) OI 1h={mf.get('oi_delta_1h_pct',0):+.1f}% OI背离={mf.get('oi_price_divergence')}
【微观】鲸鱼={micro.get('whale_direction')} 大单比={micro.get('big_trade_ratio',1):.1f} 爆仓={micro.get('liquidation_pressure')}({micro.get('liquidation_intensity')})
【散户】多头={crowd.get('long_ratio',0.5):.0%} 反指={crowd.get('contrarian_signal')}
【风险】杠杆风险={risk.get('leverage_risk')} 波动={risk.get('volatility_regime')} 流动性={risk.get('liquidity_score')}
【技术分析师研判】{llm_ta.get('key_insight','')}

最大交易额: {self._max_trade_amount} USDT。请做出最终决策。"""

        try:
            from agents.llm_client import JUDGE_DECISION_SCHEMA
            result = await self.ask_claude_json(JUDGE_PROMPT, user_msg, schema=JUDGE_DECISION_SCHEMA)
            if 'action' not in result:
                result['action'] = 'hold'
            if 'confidence' not in result:
                result['confidence'] = 50
            self._llm_consecutive_failures = 0
            if self._llm_degraded_alerted:
                self._llm_degraded_alerted = False
                self.logger.info("LLM恢复正常，重置degraded告警状态")
            return result
        except Exception as e:
            self.logger.warning(f"LLM决策失败({symbol})，规则降级: {e}")
            self._llm_consecutive_failures += 1
            if self._llm_consecutive_failures >= 3 and not self._llm_degraded_alerted:
                self._llm_degraded_alerted = True
                await self.publish("risk_alert", {
                    "symbol": "SYSTEM",
                    "type": "llm_degraded",
                    "severity": "warning",
                    "message": f"Judge LLM连续{self._llm_consecutive_failures}次失败，已降级为规则引擎",
                })
            return self._rule_fallback(tech, rule_score)

    def _rule_fallback(self, tech: dict, score: float) -> dict:
        if score > 25:
            action = "open_long"
            reasoning = "规则引擎：多维度看多共振"
        elif score < -25:
            action = "open_short"
            reasoning = "规则引擎：多维度看空共振"
        else:
            action = "hold"
            reasoning = "规则引擎：信号不足"

        return {
            "action": action,
            "confidence": min(50, int(abs(score))),
            "reasoning": reasoning,
            "key_factors": [f"综合评分={score:+.0f}"],
            "risk_warnings": ["LLM不可用，仅规则判断"],
        }

    async def _update_balance(self):
        """查询可用USDT余额"""
        if self._balance_adapter:
            self._available_balance = await self._balance_adapter.get_free_async()
            self.logger.info(f"余额查询成功: {self._available_balance:.2f} USDT")
            return
        try:
            import asyncio
            balance = await asyncio.to_thread(self.exchange.fetch_balance)
            self._available_balance = float(balance.get('USDT', {}).get('free', 0))
            self.logger.info(f"余额查询成功: {self._available_balance:.2f} USDT")
        except Exception as e:
            self.logger.error(f"查询余额失败: {e}")
            self._available_balance = 0.0
