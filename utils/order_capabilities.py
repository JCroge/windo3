"""订单能力检测 + 幂等开单防护

P1-M: 集中化交易所市场元数据缓存与订单参数预检，避免冗余 API 调用
和分散在 executor 各处的限制检查。同时提供轻量级幂等防护，防止
网络抖动 / 状态机重试导致同一笔交易被双开。

设计原则：
- 启动 warmup 一次性 load_markets，运行期间命中缓存
- precheck_order 不打 API，纯本地计算，是 fast-fail 第一道关
- IdempotencyGuard 用 (symbol, side, time-bucket) 生成稳定 clord_id，
  同一窗口内重复调用返回相同 ID，可作为 OKX clOrdId 直接传入
- 该模块不直接下单，只提供决策——是否下单仍由 executor 主导
"""
import time
import hashlib
import logging
from typing import Optional


class OrderCapabilities:
    """缓存交易所市场元数据 + 提供订单参数预检。"""

    def __init__(self, exchange, logger=None):
        self.exchange = exchange
        self.logger = logger or logging.getLogger('order_caps')
        self._cache = {}  # symbol -> caps dict
        self._warmed_up = False

    def warmup(self) -> bool:
        """启动时一次性 load_markets，返回是否成功。"""
        if self._warmed_up:
            return True
        try:
            self.exchange.load_markets()
            self._warmed_up = True
            return True
        except Exception as e:
            self.logger.warning(f"OrderCapabilities warmup 失败: {e}")
            return False

    def get(self, symbol: str) -> dict:
        """返回 symbol 的能力 dict（命中缓存或拉取一次）。

        失败时返回 {} — 调用方应判空。
        """
        if symbol in self._cache:
            return self._cache[symbol]
        try:
            market = self.exchange.market(symbol)
        except Exception as e:
            self.logger.warning(f"OrderCapabilities market({symbol}) 失败: {e}")
            return {}
        limits = market.get('limits') or {}
        precision = market.get('precision') or {}
        caps = {
            'symbol': symbol,
            'base': market.get('base'),
            'quote': market.get('quote'),
            'type': market.get('type'),
            'contract_size': float(market.get('contractSize') or 1),
            'min_amount': float((limits.get('amount') or {}).get('min') or 0),
            'max_amount': float((limits.get('amount') or {}).get('max') or 0),
            'amount_precision': precision.get('amount'),
            'min_cost': float((limits.get('cost') or {}).get('min') or 0),
            'price_precision': precision.get('price'),
            'tick_size': (limits.get('price') or {}).get('min'),
        }
        self._cache[symbol] = caps
        return caps

    def precheck_order(self, symbol: str, side: str, size_usdt: float,
                       price: float, leverage: int = 1) -> tuple:
        """订单参数预检（不打 API）。

        返回 (ok: bool, reason: str, normalized: dict)
            ok=True: normalized 含 amount 和 cost（已按精度格式化）
            ok=False: reason 标识失败原因（amount_too_small / cost_too_small / no_market_meta / bad_price）
        """
        if price <= 0:
            return False, "bad_price", {}

        caps = self.get(symbol)
        if not caps:
            return False, "no_market_meta", {}

        contract_size = caps['contract_size'] or 1
        notional = size_usdt * leverage
        denom = price * contract_size
        if denom <= 0:
            return False, "bad_denom", {}

        amount = notional / denom

        try:
            amount = float(self.exchange.amount_to_precision(symbol, amount))
        except Exception:
            pass

        min_amt = caps.get('min_amount') or 0
        if min_amt and amount < min_amt:
            return False, f"amount_too_small:{amount}<{min_amt}", {'amount': amount}

        max_amt = caps.get('max_amount') or 0
        if max_amt and amount > max_amt:
            return False, f"amount_too_large:{amount}>{max_amt}", {'amount': amount}

        cost = amount * price * contract_size
        min_cost = caps.get('min_cost') or 0
        if min_cost and cost < min_cost:
            return False, f"cost_too_small:{cost:.2f}<{min_cost}", {'amount': amount, 'cost': cost}

        return True, "ok", {'amount': amount, 'cost': cost, 'contract_size': contract_size}

    def invalidate(self, symbol: str):
        """主动失效某个 symbol 的缓存（例如交易所改了规则）。"""
        self._cache.pop(symbol, None)


class IdempotencyGuard:
    """轻量级开单幂等防护：同窗口内同方向重复开单返回相同 clord_id 并阻止重发。

    用法：
        guard = IdempotencyGuard(window_sec=10)
        is_dup, prior_id = guard.is_duplicate('BTC-USDT-SWAP', 'long')
        if is_dup:
            # 拒绝重发，prior_id 是上次的 clord
            return
        clord = guard.gen_client_order_id('BTC-USDT-SWAP', 'long')
        # 下单时传 params={'clOrdId': clord}
        guard.mark('BTC-USDT-SWAP', 'long', clord)
    """

    def __init__(self, window_sec: int = 10):
        self.window_sec = window_sec
        self._recent = {}  # (symbol, side) -> (timestamp, clord_id)

    def gen_client_order_id(self, symbol: str, side: str) -> str:
        """生成 clord_id（同 window 内同 (symbol, side) 返回相同 ID，便于幂等）。

        OKX clOrdId 限制：≤32 字符，字母数字。
        """
        bucket = int(time.time() // max(1, self.window_sec))
        base = f"{symbol}_{side}_{bucket}"
        digest = hashlib.sha1(base.encode('utf-8')).hexdigest()[:24]
        # OKX 不允许特殊字符，已用 hex 字母数字
        return f"a{digest}"  # 加前缀避免纯数字开头

    def is_duplicate(self, symbol: str, side: str) -> tuple:
        """检查是否在窗口内已有同向开单。返回 (is_dup, prior_clord_id_or_None)。"""
        key = (symbol, side)
        last = self._recent.get(key)
        if last and (time.time() - last[0]) < self.window_sec:
            return True, last[1]
        return False, None

    def mark(self, symbol: str, side: str, clord_id: str):
        """记录一次开单尝试。"""
        self._recent[(symbol, side)] = (time.time(), clord_id)

    def clear(self, symbol: str = None, side: str = None):
        """清理记录（用于显式平仓后立即允许反向重开）。"""
        if symbol and side:
            self._recent.pop((symbol, side), None)
        else:
            self._recent.clear()
