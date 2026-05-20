"""RQ-03: 候选评分（当前仅用于归因，Top-N 裁决尚未启用）

多候选同时出现时，计算 rank_score 写入 attribution 供复盘分析。
rank_and_select() 已实现但未被 Judge 调用——当前系统仍是先到先占槽。
后续如需启用 Top-N，Judge 应在短窗口内 buffer 候选后调用 rank_and_select()。
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

    def __init__(self, max_slots: int = 3, enabled: bool = True, logger=None):
        self.max_slots = max_slots
        self.enabled = enabled
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

    def rank_and_select(self, open_positions: set) -> tuple:
        """排序并选择。返回 (selected_list, rejected_list)。

        selected: 可以进入 live 的候选
        rejected: 被排名淘汰的候选（可进入 paper 观察）
        """
        if not self.enabled or not self._buffer:
            selected = list(self._buffer)
            self._buffer = []
            return selected, []

        available_slots = self.max_slots - len(open_positions)
        if available_slots <= 0:
            rejected = list(self._buffer)
            self._buffer = []
            self._rejected_candidates.extend(rejected)
            return [], rejected

        # 计算综合得分
        scored = []
        for c in self._buffer:
            rank_score = self._compute_rank_score(c)
            scored.append((rank_score, c))

        # 降序排列
        scored.sort(key=lambda x: x[0], reverse=True)

        selected = [c for _, c in scored[:available_slots]]
        rejected = [c for _, c in scored[available_slots:]]

        self._buffer = []
        self._rejected_candidates.extend(rejected)

        if self.logger and len(scored) > 1:
            self.logger.info(
                f"[Ranking] {len(scored)} candidates → "
                f"selected {len(selected)}, rejected {len(rejected)}"
            )
            for rank_score, c in scored:
                self.logger.info(
                    f"  [{c['symbol']}] rank_score={rank_score:.2f} "
                    f"signal={c.get('score', 0):.0f} "
                    f"rr={c.get('plan', {}).get('effective_risk_reward_ratio', 0):.2f}"
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
