"""RQ-03: 候选排名与 Top-N 裁决

Judge 在 rank_flush_delay 窗口内 buffer 所有通过质量门的开仓候选，
窗口结束后调用 rank_and_select() 按 rank_score 降序选取可用槽位数的 Top-N。
未入选的候选以 hold + ranked_out 发布。

槽位计算使用 _open_positions | _pending_open_symbols，
selected 后立即预占 pending，收到 execution_result 后确认或释放。
"""

import time


class CandidateRanker:
    # 各因子权重
    WEIGHTS = {
        'signal_score': 0.25,
        'llm_relation': 0.15,
        'htf_votes': 0.20,
        'liquidity': 0.10,
        'effective_rr': 0.15,
        'ev': 0.10,
        'entry_type': 0.05,
    }

    LOW_RR_PENALTY = 0.7  # 30% rank score penalty for low R:R candidates

    def __init__(self, max_slots: int = 3, enabled: bool = True,
                 low_rr_extra_slot: int = 0, logger=None):
        self.max_slots = max_slots
        self.enabled = enabled
        self.low_rr_extra_slot = low_rr_extra_slot
        self.logger = logger
        self._buffer = []
        self._last_flush = time.time()
        self._rejected_candidates = []

    def add_candidate(self, candidate: dict):
        """添加候选到 buffer。candidate 需包含 symbol, action, score, plan, tech, decision。"""
        self._buffer.append({
            **candidate,
            'added_at': time.time(),
        })

    def rank_and_select(self, open_positions: set, slot_occupancy: dict = None) -> tuple:
        """排序并选择。返回 (selected_list, rejected_list)。

        selected: 可以进入 live 的候选
        rejected: 被排名淘汰的候选（可进入 paper 观察）

        slot_occupancy: {main: int, low_rr_extra: int, probe_short: int} 当前各类型占用数。
        Low R:R candidates get a rank penalty and use a separate extra slot.
        Probe candidates use a dedicated probe slot (max 1).
        """
        if not self.enabled or not self._buffer:
            selected = list(self._buffer)
            self._buffer = []
            return selected, []

        # Use slot_occupancy if provided, otherwise fall back to simple count
        if slot_occupancy:
            main_used = slot_occupancy.get('main', 0)
            low_rr_used = slot_occupancy.get('low_rr_extra', 0)
            probe_used = slot_occupancy.get('probe_short', 0)
        else:
            main_used = len(open_positions)
            low_rr_used = 0
            probe_used = 0

        available_main = self.max_slots - main_used
        available_low_rr_extra = max(0, self.low_rr_extra_slot - low_rr_used)
        available_probe = max(0, 1 - probe_used)

        if available_main <= 0 and available_low_rr_extra <= 0 and available_probe <= 0:
            rejected = list(self._buffer)
            self._buffer = []
            self._rejected_candidates.extend(rejected)
            return [], rejected

        # Score all candidates
        scored = []
        for c in self._buffer:
            rank_score = self._compute_rank_score(c)
            scored.append((rank_score, c))
        scored.sort(key=lambda x: x[0], reverse=True)

        # Separate by slot type
        normal_scored = [(s, c) for s, c in scored
                         if not c.get('plan', {}).get('is_low_rr')
                         and not c.get('plan', {}).get('is_probe')]
        low_rr_scored = [(s, c) for s, c in scored if c.get('plan', {}).get('is_low_rr')]
        probe_scored = [(s, c) for s, c in scored if c.get('plan', {}).get('is_probe')]

        selected = []
        # Fill main slots: normal candidates first
        main_from_normal = normal_scored[:max(0, available_main)]
        remaining_main = max(0, available_main - len(main_from_normal))
        main_from_low_rr = low_rr_scored[:remaining_main]

        selected = [c for _, c in main_from_normal + main_from_low_rr]

        # Extra slot: only for low_rr candidates that didn't get main slot
        low_rr_remaining = low_rr_scored[remaining_main:]
        extra_selected = [c for _, c in low_rr_remaining[:available_low_rr_extra]]
        selected.extend(extra_selected)

        # Probe slot: at most 1 probe candidate
        probe_selected = [c for _, c in probe_scored[:available_probe]]
        selected.extend(probe_selected)

        # Everything else is rejected
        all_selected_ids = {id(c) for c in selected}
        rejected = [c for _, c in scored if id(c) not in all_selected_ids]

        self._buffer = []
        self._rejected_candidates.extend(rejected)

        if self.logger and len(scored) > 1:
            self.logger.info(
                f"[Ranking] {len(scored)} candidates → "
                f"selected {len(selected)} (main={len(main_from_normal)+len(main_from_low_rr)}, "
                f"extra_low_rr={len(extra_selected)}, probe={len(probe_selected)}), "
                f"rejected {len(rejected)}"
            )
            for rank_score, c in scored:
                low_rr_tag = " [low_rr]" if c.get('plan', {}).get('is_low_rr') else ""
                probe_tag = " [probe]" if c.get('plan', {}).get('is_probe') else ""
                self.logger.info(
                    f"  [{c['symbol']}] rank_score={rank_score:.2f} "
                    f"signal={c.get('score', 0):.0f} "
                    f"rr={c.get('plan', {}).get('effective_risk_reward_ratio', 0):.2f}"
                    f"{low_rr_tag}{probe_tag}"
                )

        return selected, rejected

    def _compute_rank_score(self, candidate: dict) -> float:
        """计算候选的综合排名分数 (0-100)。"""
        plan = candidate.get('plan', {}) or {}
        tech = candidate.get('tech', {}) or {}
        attribution = candidate.get('attribution', {}) or {}

        # 1. Signal score (normalize to 0-100)
        raw_score = abs(candidate.get('score', 0))
        signal_norm = min(100, raw_score * 1.5)

        # 2. LLM relation
        llm_rel = attribution.get('llm_relation', 'neutral')
        llm_scores = {'agree': 100, 'neutral': 50, 'hold': 20, 'reverse': 0}
        llm_norm = llm_scores.get(llm_rel, 50)

        # 3. HTF votes (0-3 → 0-100)
        htf_votes = attribution.get('htf_votes', 1)
        htf_norm = min(100, htf_votes * 33.3)

        # 4. Liquidity
        liq_bucket = attribution.get('liquidity_bucket', 'medium')
        liq_scores = {'high': 100, 'medium': 60, 'low': 20}
        liq_norm = liq_scores.get(liq_bucket, 60)

        # 5. Effective RR (normalize: 1.5=50, 2.0=75, 3.0=100)
        rr = plan.get('effective_risk_reward_ratio', 1.5)
        rr_norm = min(100, max(0, (rr - 1.0) * 50))

        # 6. EV (normalize: 0=50, positive=higher)
        ev = plan.get('expected_value', 0)
        ev_norm = min(100, max(0, 50 + ev * 20))

        # 7. Entry type preference
        entry_type = candidate.get('entry_type', 'standard')
        type_scores = {
            'rule_signal': 100, 'ma_aligned': 80,
            'llm_driven': 60, 'deferred_pullback': 40, 'deferred_chase': 30,
        }
        type_norm = type_scores.get(entry_type, 50)

        # Weighted sum
        total = (
            self.WEIGHTS['signal_score'] * signal_norm +
            self.WEIGHTS['llm_relation'] * llm_norm +
            self.WEIGHTS['htf_votes'] * htf_norm +
            self.WEIGHTS['liquidity'] * liq_norm +
            self.WEIGHTS['effective_rr'] * rr_norm +
            self.WEIGHTS['ev'] * ev_norm +
            self.WEIGHTS['entry_type'] * type_norm
        )

        # Low R:R penalty: ensure high R:R candidates always rank above
        if candidate.get('plan', {}).get('is_low_rr'):
            total *= self.LOW_RR_PENALTY

        return round(total, 2)

    def get_rejected_candidates(self, clear: bool = True) -> list:
        """获取被拒候选（用于 paper 观察）。"""
        rejected = list(self._rejected_candidates)
        if clear:
            self._rejected_candidates = []
        return rejected

    def has_pending(self) -> bool:
        return len(self._buffer) > 0

    def flush_stale(self, max_age: float = 5.0):
        """清除超时候选（防止 buffer 无限增长）。"""
        now = time.time()
        self._buffer = [c for c in self._buffer if now - c['added_at'] < max_age]
