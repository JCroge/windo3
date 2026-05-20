"""RQ-06: 信号原型级 cooldown

按亏损来源冷却，而非全局冷却。避免一个原型的连续亏损影响无关高质量信号。

原型分类：
- rule_signal_llm_hold: rule_signal 触发但 LLM hold
- rule_signal_htf_mismatch: rule_signal 触发但 HTF 反向
- deferred_pullback: 回调入场
- deferred_chase: 追价入场
- low_liquidity: 低流动性入场
- counter_trend: 逆趋势入场
- standard: 标准入场（无特殊标记）

冷却规则：
- 连续 2 次亏损 → 短冷却 (30min)
- 近 5 次 PF < 0.8 → 长冷却 (2h)
"""

import time
from collections import defaultdict


class ArchetypeCooldown:
    SHORT_COOLDOWN = 1800   # 30 min
    LONG_COOLDOWN = 7200    # 2 hours

    def __init__(self, enabled: bool = True, logger=None):
        self.enabled = enabled
        self.logger = logger
        self._history = defaultdict(list)
        self._cooldown_until = {}

    def classify(self, attribution: dict) -> str:
        """从归因字段推断信号原型。"""
        entry_type = attribution.get('entry_type', 'unknown')
        llm_relation = attribution.get('llm_relation', 'neutral')
        htf_votes = attribution.get('htf_votes', 1)
        liquidity_bucket = attribution.get('liquidity_bucket', 'medium')

        if entry_type == 'deferred_pullback':
            return 'deferred_pullback'
        if entry_type == 'deferred_chase':
            return 'deferred_chase'
        if entry_type in ('rule_signal', 'ma_aligned'):
            if llm_relation == 'hold':
                return 'rule_signal_llm_hold'
            if htf_votes == 0:
                return 'rule_signal_htf_mismatch'
        if liquidity_bucket == 'low':
            return 'low_liquidity'
        if htf_votes == 0 and entry_type not in ('rule_signal', 'ma_aligned'):
            return 'counter_trend'
        return 'standard'

    def record_result(self, archetype: str, pnl: float):
        """记录一笔交易结果。"""
        self._history[archetype].append({
            'pnl': pnl,
            'timestamp': time.time(),
        })
        # 只保留最近 10 笔
        if len(self._history[archetype]) > 10:
            self._history[archetype] = self._history[archetype][-10:]

        if pnl < 0:
            self._evaluate_cooldown(archetype)

    def is_cooled(self, archetype: str) -> tuple:
        """检查某原型是否在冷却中。返回 (is_cooled, remaining_seconds)。"""
        if not self.enabled:
            return False, 0
        until = self._cooldown_until.get(archetype, 0)
        remaining = until - time.time()
        if remaining > 0:
            return True, int(remaining)
        return False, 0

    def _evaluate_cooldown(self, archetype: str):
        """评估是否需要触发冷却。"""
        history = self._history[archetype]
        if len(history) < 2:
            return

        # 规则1: 连续 2 次亏损 → 短冷却
        if history[-1]['pnl'] < 0 and history[-2]['pnl'] < 0:
            self._activate_cooldown(archetype, self.SHORT_COOLDOWN, 'consecutive_2_losses')
            return

        # 规则2: 近 5 次 PF < 0.8 → 长冷却
        recent_5 = history[-5:]
        if len(recent_5) >= 5:
            wins = sum(r['pnl'] for r in recent_5 if r['pnl'] > 0)
            losses = abs(sum(r['pnl'] for r in recent_5 if r['pnl'] < 0))
            pf = wins / losses if losses > 0 else 999
            if pf < 0.8:
                self._activate_cooldown(archetype, self.LONG_COOLDOWN, f'pf_5={pf:.2f}<0.8')

    def _activate_cooldown(self, archetype: str, duration: int, reason: str):
        """激活冷却。"""
        self._cooldown_until[archetype] = time.time() + duration
        if self.logger:
            self.logger.warning(
                f"[ArchetypeCooldown] {archetype} 进入冷却 {duration}s, 原因: {reason}"
            )

    def get_status(self) -> dict:
        """返回所有原型的冷却状态（用于日志/报告）。"""
        now = time.time()
        status = {}
        for archetype, until in self._cooldown_until.items():
            remaining = until - now
            if remaining > 0:
                status[archetype] = int(remaining)
        return status
