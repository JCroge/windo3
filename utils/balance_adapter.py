"""P1-3: 统一余额读取，带 TTL 缓存和降级保护"""
import time
import asyncio
import logging


class BalanceAdapter:
    def __init__(self, exchange, ttl: float = 10.0, logger=None):
        self._exchange = exchange
        self._ttl = ttl
        self.logger = logger or logging.getLogger('balance_adapter')
        self._free: float = 0.0
        self._total: float = 0.0
        self._last_fetch: float = 0.0

    # ── 同步接口（executor.py 使用）──────────────────────────────

    def get_free(self) -> float:
        self._refresh_sync()
        return self._free

    def get_total(self) -> float:
        self._refresh_sync()
        return self._total

    @staticmethod
    def _parse(b: dict) -> tuple:
        """支持两种 CCXT 余额结构：b['USDT']['free'] 或 b['free']['USDT']"""
        usdt = b.get('USDT')
        if isinstance(usdt, dict) and ('free' in usdt or 'total' in usdt):
            return float(usdt.get('free') or 0), float(usdt.get('total') or 0)
        free_d = b.get('free') or {}
        total_d = b.get('total') or {}
        return float(free_d.get('USDT') or 0), float(total_d.get('USDT') or 0)

    def _refresh_sync(self):
        if time.time() - self._last_fetch < self._ttl:
            return
        try:
            self._free, self._total = self._parse(self._exchange.fetch_balance())
            self._last_fetch = time.time()
        except Exception as e:
            self.logger.warning(f"[BalanceAdapter] fetch_balance 失败，使用缓存: {e}")

    # ── 异步接口（judge.py 使用）─────────────────────────────────

    async def get_free_async(self) -> float:
        await self._refresh_async()
        return self._free

    async def get_total_async(self) -> float:
        await self._refresh_async()
        return self._total

    async def _refresh_async(self):
        if time.time() - self._last_fetch < self._ttl:
            return
        try:
            self._free, self._total = self._parse(await asyncio.to_thread(self._exchange.fetch_balance))
            self._last_fetch = time.time()
        except Exception as e:
            self.logger.warning(f"[BalanceAdapter] async fetch_balance 失败，使用缓存: {e}")

    # ── 事件驱动更新（RiskGuard 推送，无需 API 调用）────────────

    def update_from_event(self, total: float):
        self._total = total
        self._last_fetch = time.time()
