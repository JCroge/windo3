#!/usr/bin/env python3
"""合约执行器 - 基于ccxt的统一接口"""

import ccxt
import hashlib
import json
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional
from risk_manager import RiskManager
from utils.logger import setup_logger

# 持仓同步瞬时错误重试（fetch_positions 偶发 OKX 网络/超时抖动）
_POS_SYNC_RETRY_ATTEMPTS = 3
_POS_SYNC_RETRY_BACKOFFS = (0.5, 1.0)  # 第1/2次失败后退避秒数
_TACTICAL_TERMINAL_ORDER_STATUSES = frozenset({
    "canceled",
    "cancelled",
    "closed",
    "filled",
    "rejected",
    "expired",
})

# ---------------------------------------------------------------------------
# Entry Drift Hybrid Policy — threshold constants
# ---------------------------------------------------------------------------
ENTRY_DRIFT_ACCEPT_PCT = 0.005       # ≤0.5 %  → accept as-is
ENTRY_DRIFT_SMALL_PCT = 0.02         # ≤2 %    → small band (recalc, usually pass)
ENTRY_DRIFT_LARGE_PCT = 0.05         # ≤5 %    → medium band (recalc, tighter floor)
                                     # >5 %    → abandon band
ENTRY_DRIFT_MEDIUM_FLOOR_BUMP = 0.20  # +20 % R:R floor bump in medium band


@dataclass(frozen=True)
class DriftDecision:
    """Immutable result of classify_entry_drift().  Callers must not mutate."""
    band: Literal['accept', 'small', 'medium', 'abandon']
    drift_pct: float
    decision: Literal['accept', 'recalc_pass', 'recalc_fail', 'abandon']
    reason: Optional[str]
    new_plan: Optional[dict]
    rr_actual: Optional[float]
    rr_floor_used: Optional[float]


# OKX 拒单错误码：与持仓状态相关，必须做交易所状态复核
OKX_POSITION_REJECT_CODES = ('51169', '51205', '51112', '51333')
PROTECTION_HALT_REASONS = {"sl_algo_unresolved", "migrate_missing_sl"}


def _is_okx_position_reject(err_msg: str) -> bool:
    if not err_msg:
        return False
    return any(code in err_msg for code in OKX_POSITION_REJECT_CODES)


class ContractExecutor:
    """合约执行器"""

    def __init__(self, exchange_id: str = 'binance',
                 api_key: str = None,
                 secret: str = None,
                 password: str = None,
                 testnet: bool = True,
                 leverage: int = 1,
                 positions_file: Optional[str] = None,
                 risk_state_file: Optional[str] = None,
                 ledger_events_file: Optional[str] = None,
                 ledger_lifecycle_file: Optional[str] = None):
        """
        Args:
            exchange_id: 交易所ID (binance/okx)
            api_key: API密钥
            secret: API密钥
            password: API密码（OKX需要）
            testnet: 是否使用测试网
            leverage: 杠杆倍数（默认1倍，不使用杠杆）
            positions_file: 持仓持久化文件路径；None 时按 STATE_NAMESPACE 自动派生
                            (live → data/positions.json, testnet → data/testnet_positions.json,
                             paper → data/paper_positions.json)
            risk_state_file / ledger_*: sidecar 等隔离进程可显式注入,
                                      避免污染 Main live 状态文件。
        """
        self.logger = setup_logger('executor')
        self.exchange_id = exchange_id
        self.testnet = testnet
        self.leverage = leverage
        # FR-008: 通过 state_paths 解析命名空间
        from utils.state_paths import get_state_paths
        sp = get_state_paths()
        self.positions_file = positions_file or sp.positions

        # 初始化交易所
        exchange_class = getattr(ccxt, exchange_id)
        config = {
            'apiKey': api_key,
            'secret': secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'}  # 使用永续合约
        }

        # OKX需要password
        if exchange_id == 'okx' and password:
            config['password'] = password

        self.exchange = exchange_class(config)

        if testnet:
            self.exchange.set_sandbox_mode(True)
            self.logger.info(f"使用 {exchange_id} 测试网")

        # OKX posMode 探测：live 失败 fail-closed
        self._okx_pos_mode: Optional[str] = None
        self._okx_pos_mode_source: str = "n/a"
        if exchange_id == 'okx':
            self._detect_okx_pos_mode()

        # 风控管理器（统一从 config_loader 读，避免硬编码默认值）
        try:
            from utils.config_loader import load_config
            _cfg = load_config(strict_live_check=False)
            max_amount = _cfg.get('max_trade_amount', 10.0)
            max_dd = _cfg.get('max_drawdown_pct', 20.0)
            max_daily = abs(_cfg.get('daily_pnl_hard_stop', -50.0))
            _cap = _cfg.get('effective_balance_cap')
            _baseline_mode = _cfg.get('drawdown_baseline_mode', 'session_start')
            # 暴露已加载 config 给 sync_positions 双确认等读取（fail-safe 默认）
            self._config = dict(_cfg)
        except Exception as e:
            self.logger.warning(f"config_loader 加载失败，使用 env 兜底（HARD_LIMITS clamp）: {e}")
            # P2-17: env 兜底也必须经 HARD_LIMITS clamp，杜绝风险限额 fail-open 到未约束值。
            from utils.config_loader import clamp_to_hard_limits
            _fb = clamp_to_hard_limits({
                'max_trade_amount': float(os.getenv('MAX_TRADE_AMOUNT', 10)),
                'max_drawdown_pct': float(os.getenv('MAX_DRAWDOWN_PCT', 20)),
                'daily_pnl_hard_stop': -abs(float(os.getenv('MAX_DAILY_LOSS', 50))),
                'effective_balance_cap': (float(os.getenv('EFFECTIVE_BALANCE_CAP', 0)) or None),
            })
            max_amount = _fb['max_trade_amount']
            max_dd = _fb['max_drawdown_pct']
            max_daily = abs(_fb['daily_pnl_hard_stop'])
            _cap = _fb['effective_balance_cap']
            _baseline_mode = os.getenv('DRAWDOWN_BASELINE_MODE', 'session_start')
            self._config = {}   # config 加载失败时双确认走默认 2
        self.risk_manager = RiskManager(
            max_trade_amount=max_amount,
            max_drawdown_pct=max_dd,
            max_daily_loss=max_daily,
            state_file=risk_state_file or sp.risk_state,
            effective_balance_cap=_cap,
            baseline_mode=_baseline_mode,
        )

        # 持仓记录
        self.positions = {}
        self._load_positions()

        # 幽灵持仓补录双确认计数器（key=symbol → 连续见到的 sync tick 数）
        self._pending_resync = {}
        # protection-unknown 告警去重状态（key=symbol → 上次告警 reason）
        self._last_protection_alert = {}

        # 止损检查连续失败计数器（key=symbol）
        self._sl_check_failures = {}
        self._sl_max_failures = 3  # 连续失败N次后强制平仓
        self._last_sl_update = {}  # {symbol: timestamp} SL更新节流

        # FR-06: per-symbol exit lock,串行化所有 close/reduce/partial_tp 路径,
        # 防止 partial_tp_1 / risk_alert reduce / local_stop close 并发下出双单。
        # 详见 docs/partial_tp_lifecycle_prd.md FR-04/FR-06。
        self._exit_lock_mu = threading.Lock()
        self._exit_locks: Dict[str, dict] = {}

        # Entry drift gate: buffered risk alerts for agent layer to drain & publish
        self._pending_drift_alerts: list[dict] = []

        # P1-M: 订单能力缓存 + 幂等防护
        try:
            from utils.order_capabilities import OrderCapabilities, IdempotencyGuard
            self.caps = OrderCapabilities(self.exchange, self.logger)
            self.caps.warmup()
            self.idempotency = IdempotencyGuard(window_sec=10)
        except Exception as e:
            self.logger.warning(f"OrderCapabilities/IdempotencyGuard 初始化失败（降级）: {e}")
            self.caps = None
            self.idempotency = None

        # P1-3: 统一余额读取
        try:
            from utils.balance_adapter import BalanceAdapter
            self.balance_adapter = BalanceAdapter(self.exchange, ttl=10.0, logger=self.logger)
        except Exception as e:
            self.logger.warning(f"BalanceAdapter 初始化失败（降级）: {e}")
            self.balance_adapter = None

        # 实盘账本：真实成交 PnL 记录
        try:
            from utils.live_ledger import LiveLedger
            self.ledger = LiveLedger(
                self.exchange,
                events_path=ledger_events_file or sp.live_order_events,
                lifecycle_path=ledger_lifecycle_file or sp.live_position_lifecycle,
                logger=self.logger,
            )
            # 启动时从 ledger 同步当日 PnL 到 risk_manager
            self.risk_manager.sync_from_ledger(self.ledger)
        except Exception as e:
            self.logger.warning(f"LiveLedger 初始化失败（降级）: {e}")
            self.ledger = None

        # 启动时初始化 session 回撤基准
        try:
            real_total = self.get_balance()
            if real_total > 0:
                self.risk_manager.initialize_session(real_total, _cap)
        except Exception as e:
            self.logger.warning(f"initialize_session 失败（降级）: {e}")

        self.logger.info(f"杠杆设置: {leverage}x")

    def _detect_okx_pos_mode(self) -> None:
        """启动时探测 OKX 账户 posMode，并 fail-closed 处理失败。

        - live (testnet=False)：必须从 GET /api/v5/account/config 拿到合法值，否则禁止开新仓。
        - testnet/paper：允许 OKX_POS_MODE_OVERRIDE env 覆盖；若 API 探测失败但有 override，
          使用 override 并在日志明确标记非真实返回。
        """
        override = os.getenv('OKX_POS_MODE_OVERRIDE', '').strip().lower() or None
        if override and override not in ('net_mode', 'long_short_mode'):
            self.logger.warning(f"[OKX posMode] override 值非法（忽略）: {override}")
            override = None

        try:
            raw = self.exchange.private_get_account_config()
            data = (raw or {}).get('data') or []
            mode = (data[0].get('posMode') if data else '') or ''
            mode = mode.strip().lower()
            if mode in ('net_mode', 'long_short_mode'):
                self._okx_pos_mode = mode
                self._okx_pos_mode_source = 'okx_api'
                self.logger.info(f"[OKX posMode] 探测成功: {mode} (testnet={self.testnet})")
                return
            self.logger.error(f"[OKX posMode] 非预期返回: {raw}")
        except Exception as e:
            self.logger.error(f"[OKX posMode] private_get_account_config 失败: {e}")

        # 探测失败的降级路径
        if self.testnet:
            if override:
                self._okx_pos_mode = override
                self._okx_pos_mode_source = 'env_override_testnet'
                self.logger.warning(
                    f"[OKX posMode] testnet 使用 env override={override}（不是交易所真实返回）"
                )
            else:
                # testnet 默认 net_mode（仅用于本地/CI 跑通），日志标记
                self._okx_pos_mode = 'net_mode'
                self._okx_pos_mode_source = 'default_testnet'
                self.logger.warning(
                    "[OKX posMode] testnet 未拿到真实 posMode，默认 net_mode（不是交易所真实返回）"
                )
        else:
            # live：fail-closed，禁止开新仓
            self._okx_pos_mode = None
            self._okx_pos_mode_source = 'unknown_live_fail_closed'
            self.logger.critical(
                "[OKX posMode] live 探测失败，FAIL-CLOSED：禁止开新仓直至人工介入"
            )

    def _require_okx_pos_mode(self) -> Optional[str]:
        """OKX 路径取 posMode；非 OKX 返回 None。"""
        if self.exchange_id != 'okx':
            return None
        return self._okx_pos_mode

    def can_open_new_okx(self) -> bool:
        """OKX：posMode 已知才允许开新仓；非 OKX 总是允许。"""
        if self.exchange_id != 'okx':
            return True
        return self._okx_pos_mode in ('net_mode', 'long_short_mode')

    # ------------------------------------------------------------------
    # OKX 参数构造器（唯一允许写 reduceOnly/posSide 的入口）
    # ------------------------------------------------------------------
    def _okx_pos_side_for(self, side: str) -> str:
        """side('long'/'short') → OKX posSide，按当前账户模式。"""
        if self._okx_pos_mode == 'long_short_mode':
            return 'long' if side == 'long' else 'short'
        return 'net'

    def _build_okx_open_params(self, side: str, *, clord_id: Optional[str] = None,
                               attach_algo: Optional[list] = None) -> dict:
        """开仓/加仓参数：不传 reduceOnly。"""
        params: dict = {'posSide': self._okx_pos_side_for(side)}
        if clord_id:
            params['clOrdId'] = clord_id
        if attach_algo:
            params['attachAlgoOrds'] = attach_algo
        return params

    def _build_okx_close_params(self, position: dict, *, clord_id: Optional[str] = None) -> dict:
        """减仓/平仓参数：
        - net_mode: posSide=net + reduceOnly=True
        - long_short_mode: posSide=被保护方向，不传 reduceOnly
        """
        side = position.get('side', 'long')
        params: dict = {'posSide': self._okx_pos_side_for(side)}
        if self._okx_pos_mode == 'net_mode':
            params['reduceOnly'] = True
        if clord_id:
            params['clOrdId'] = clord_id
        return params

    def _build_okx_algo_params(self, position: dict, *, sl_trigger=None, sl_ord_px='-1',
                                tp_trigger=None, tp_ord_px='-1') -> dict:
        """独立 SL/TP algo 参数：
        - 反向 side
        - posSide: net_mode → net；long_short_mode → 被保护仓位方向
        - 不传 reduceOnly
        """
        side = position.get('side', 'long')
        algo_side = 'sell' if side == 'long' else 'buy'
        params: dict = {
            'side': algo_side,
            'posSide': self._okx_pos_side_for(side),
        }
        if sl_trigger is not None:
            params['slTriggerPx'] = str(sl_trigger)
            params['slOrdPx'] = str(sl_ord_px)
        if tp_trigger is not None:
            params['tpTriggerPx'] = str(tp_trigger)
            params['tpOrdPx'] = str(tp_ord_px)
        return params

    def _build_okx_attach_algo(self, stop_loss: Optional[float],
                                take_profit: Optional[float] = None,
                                *, clord_id: Optional[str] = None) -> Optional[list]:
        """开仓附带保护单的 attachAlgoOrds 列表（仅 SL，不写 reduceOnly）。

        分批止盈生命周期收敛后，开仓不再附带交易所 TP；TP 由本地 partial TP
        owner 负责。take_profit 参数保留只是为了兼容旧调用点，但会被忽略。
        clord_id 透传 OKX `attachAlgoClOrdId`,成交后用于回查 algoId 并保存到
        `position['sl_algo_id']`。详见 docs/partial_tp_lifecycle_prd.md FR-01/FR-02。
        """
        if not stop_loss:
            return None
        algo: dict = {
            'slTriggerPx': str(stop_loss),
            'slOrdPx': '-1',
        }
        if clord_id:
            algo['attachAlgoClOrdId'] = clord_id
        return [algo]

    @staticmethod
    def _make_sl_clord_id(symbol: str) -> str:
        """[DEPRECATED] 历史兼容标识器,新挂单 MUST 使用 _make_owner_tag_clord_id。

        保留原因:
          - cleanup 路径 (_cleanup_sl_algo 等) 按 position['sl_algo_clord_id']
            做 exact 字符串匹配,存量 positions.json 中的历史 sl... 前缀仍能被
            识别为本系统所有,避免误清扫。
          - _is_owner_clord_id 只对 owner-tag prefix (ca+ns+bot) 做匹配,
            历史 sl... 前缀不会通过该函数,但 exact 匹配兜底保留 ownership。
          - 预计 1-2 个月后跑全量 positions.json 审计确认无遗留再删除。

        FR-3B 兼容: 历史 sl... 前缀只能通过 exact sl_algo_clord_id 匹配证明 owner,
        不能用 'sl' 前缀做泛化 sweep。
        """
        base = symbol.replace('-', '').replace('/', '').replace(':', '').upper()[:8]
        return f"sl{base}{uuid.uuid4().hex[:18]}"

    @staticmethod
    def _resolve_owner_tag() -> tuple:
        """FR-3B owner tag: 返回 (namespace, bot_instance) 短串,只含字母数字。

        algoClOrdId = ca + namespace + bot_instance + base + random,
        OKX 限制字母数字共 32 chars。namespace 来自 STATE_NAMESPACE,
        bot_instance 来自 BOT_INSTANCE_ID,缺省空串。
        """
        ns = (os.getenv('STATE_NAMESPACE') or '').strip()
        if not ns:
            ns = 'live' if os.getenv('USE_TESTNET', '').lower() != 'true' else 'testnet'
        bot = (os.getenv('BOT_INSTANCE_ID') or '').strip()
        # 只保留字母数字
        ns_clean = ''.join(c for c in ns if c.isalnum())[:6]
        bot_clean = ''.join(c for c in bot if c.isalnum())[:6]
        return ns_clean, bot_clean

    @classmethod
    def _make_owner_tag_clord_id(cls, symbol: str) -> str:
        """生成带 owner 标识的 SL algoClOrdId。

        格式: ca + ns + bot + base(<=6) + random(<=14),OKX 字母数字限制 32 chars。
        """
        ns, bot = cls._resolve_owner_tag()
        base = ''.join(c for c in symbol if c.isalnum()).upper()[:6]
        prefix = f"ca{ns}{bot}{base}"
        rand_len = max(8, 32 - len(prefix))
        return prefix + uuid.uuid4().hex[:rand_len]

    @classmethod
    def _is_owner_clord_id(cls, clord_id: Optional[str]) -> bool:
        """判定 algoClOrdId 是否归属当前实例(owner-prefix 匹配)。"""
        if not clord_id:
            return False
        ns, bot = cls._resolve_owner_tag()
        if not ns and not bot:
            return False
        prefix = f"ca{ns}{bot}"
        return clord_id.startswith(prefix)

    @classmethod
    def _is_foreign_owner_clord_id(cls, clord_id: Optional[str]) -> bool:
        """Owner-tagged clOrdId that does not belong to this executor instance."""
        if not clord_id:
            return False
        clord = str(clord_id)
        return clord.startswith("ca") and not cls._is_owner_clord_id(clord)

    @classmethod
    def _is_tactical_v2_clord_id(cls, clord_id: Optional[str]) -> bool:
        """Recognize this instance's deterministic Tactical V2 command identity."""
        if not clord_id:
            return False
        ns, bot = cls._resolve_owner_tag()
        prefix = f"ca{ns}{bot}v2"
        value = str(clord_id)
        return (
            len(value) == 32
            and value.isalnum()
            and value.startswith(prefix)
            and len(value) > len(prefix)
            and value[len(prefix)] in {"e", "t", "s", "c"}
        )

    @classmethod
    def make_tactical_clord_id(cls, intent_id: str, purpose: str) -> str:
        """Derive a stable OKX-safe owner identity for one Tactical command."""
        normalized_intent = str(intent_id or "").strip()
        normalized_purpose = str(purpose or "").strip().lower()
        if not normalized_intent:
            raise ValueError("intent_id is required")
        purpose_codes = {
            "entry": "e",
            "tp": "t",
            "sl": "s",
            "close": "c",
        }
        if normalized_purpose not in purpose_codes:
            raise ValueError("unsupported Tactical client-id purpose")
        namespace, bot = cls._resolve_owner_tag()
        prefix = f"ca{namespace}{bot}v2{purpose_codes[normalized_purpose]}"
        digest = hashlib.sha256(
            f"{normalized_intent}:{normalized_purpose}".encode("utf-8")
        ).hexdigest()
        return (prefix + digest[:max(1, 32 - len(prefix))])[:32]

    def submit_tactical_entry(self, intent: Any, *, order_type: str) -> dict:
        """Submit one fixed-size Tactical entry without Main sizing or drift policy."""
        if self.exchange_id != "okx":
            raise RuntimeError("Tactical V2 live entry currently requires OKX")
        if getattr(self, "_okx_pos_mode", None) not in {"net_mode", "long_short_mode"}:
            raise RuntimeError("OKX position mode is unknown")

        normalized_type = str(order_type or "").strip().lower()
        if normalized_type not in {"market", "limit"}:
            raise ValueError("order_type must be market or limit")
        margin_usdt = float(getattr(intent, "margin_usdt", 0))
        if not math.isclose(margin_usdt, 100.0, rel_tol=0, abs_tol=1e-9):
            raise ValueError("Tactical V2 margin_usdt must be exactly 100")
        leverage = int(getattr(intent, "leverage", 0))
        if not 1 <= leverage <= 5:
            raise ValueError("Tactical V2 leverage must be between 1 and 5")
        side = str(getattr(intent, "side", "")).lower()
        if side not in {"long", "short"}:
            raise ValueError("Tactical V2 side must be long or short")

        symbol = self._normalize_symbol(str(getattr(intent, "symbol", "")))
        ticker = self.exchange.fetch_ticker(symbol) or {}
        if normalized_type == "limit":
            price = float(getattr(intent, "entry_ref"))
        elif side == "long":
            price = float(ticker.get("ask") or ticker.get("last") or 0)
        else:
            price = float(ticker.get("bid") or ticker.get("last") or 0)
        if not math.isfinite(price) or price <= 0:
            raise RuntimeError("executable entry price is unavailable")

        free_balance = (
            self.balance_adapter.get_free()
            if getattr(self, "balance_adapter", None) is not None
            else float((self.exchange.fetch_balance().get("USDT") or {}).get("free", 0))
        )
        if not math.isfinite(float(free_balance)) or float(free_balance) < margin_usdt:
            raise RuntimeError("insufficient free balance for Tactical V2 margin")

        self.exchange.set_leverage(leverage, symbol)
        market = self.exchange.market(symbol)
        contract_size = float(market.get("contractSize", 1) or 1)
        quantity = float(self.exchange.amount_to_precision(
            symbol,
            margin_usdt * leverage / (price * contract_size),
        ))
        minimum = float(
            ((market.get("limits") or {}).get("amount") or {}).get("min", 0) or 0
        )
        if not math.isfinite(quantity) or quantity <= 0 or (minimum and quantity < minimum):
            raise RuntimeError("Tactical V2 order quantity is below exchange minimum")

        entry_client_id = self.make_tactical_clord_id(intent.intent_id, "entry")
        tp_client_id = self.make_tactical_clord_id(intent.intent_id, "tp")
        sl_client_id = self.make_tactical_clord_id(intent.intent_id, "sl")
        attached = [
            {
                "tpTriggerPx": str(float(getattr(intent, "take_profit"))),
                "tpOrdPx": "-1",
                "attachAlgoClOrdId": tp_client_id,
            },
            {
                "slTriggerPx": str(float(getattr(intent, "stop_loss"))),
                "slOrdPx": "-1",
                "attachAlgoClOrdId": sl_client_id,
            },
        ]
        params = self._build_okx_open_params(
            side,
            clord_id=entry_client_id,
            attach_algo=attached,
        )
        order_side = "buy" if side == "long" else "sell"
        recovered = None
        try:
            order = self.exchange.create_order(
                symbol=symbol,
                type=normalized_type,
                side=order_side,
                amount=quantity,
                price=price if normalized_type == "limit" else None,
                params=params,
            )
        except Exception:
            query = self.query_tactical_entry(intent)
            if query.get("query_state") != "found":
                raise
            recovered = query["observation"]
            order = {
                "id": recovered.get("order_id"),
                "status": recovered.get("status", "unknown"),
            }
        result = {
            "order_id": (order or {}).get("id"),
            "status": (order or {}).get("status", "unknown"),
            "symbol": symbol,
            "side": side,
            "order_type": normalized_type,
            "limit_price": price if normalized_type == "limit" else None,
            "requested_qty": quantity,
            "margin_usdt": margin_usdt,
            "leverage": leverage,
            "entry_client_id": entry_client_id,
            "tp_client_id": tp_client_id,
            "sl_client_id": sl_client_id,
        }
        if recovered is not None:
            result["recovered_after_submit_error"] = True
            result["filled_qty"] = recovered.get("filled_qty", 0.0)
            result["remaining_qty"] = recovered.get("remaining_qty", quantity)
        return result

    def query_tactical_entry(self, intent: Any) -> dict:
        """Return found/not-found/error evidence for one deterministic entry id."""
        symbol = self._normalize_symbol(str(getattr(intent, "symbol", "")))
        expected = self.make_tactical_clord_id(intent.intent_id, "entry")
        successful_sources = []
        errors = []
        exact_fetcher = getattr(self.exchange, "private_get_trade_order", None)
        if self.exchange_id == "okx" and callable(exact_fetcher):
            try:
                response = exact_fetcher({
                    "instId": symbol,
                    "clOrdId": expected,
                }) or {}
                if not isinstance(response, dict):
                    raise TypeError("private order response must be a mapping")
                exact_rows = response.get("data") or []
                if not isinstance(exact_rows, list):
                    raise TypeError("private order data must be a list")
                successful_sources.append("private_get_trade_order")
            except Exception as exc:
                exact_rows = []
                errors.append({
                    "source": "private_get_trade_order",
                    "error": str(exc),
                })
            for info in exact_rows or []:
                if not isinstance(info, dict) or info.get("clOrdId") != expected:
                    continue
                status = str(info.get("state") or "unknown").lower()
                filled = float(info.get("accFillSz") or 0)
                try:
                    remaining = max(0.0, float(info.get("sz") or 0) - filled)
                except (TypeError, ValueError):
                    remaining = 0.0
                if status in _TACTICAL_TERMINAL_ORDER_STATUSES:
                    remaining = 0.0
                average = info.get("avgPx") or info.get("fillPx")
                return {
                    "query_state": "found",
                    "observation": {
                        "order_id": info.get("ordId"),
                        "client_order_id": expected,
                        "status": status,
                        "filled_qty": filled,
                        "remaining_qty": remaining,
                        "average_price": (
                            float(average) if average not in (None, "") else None
                        ),
                    },
                    "successful_sources": successful_sources,
                    "errors": errors,
                }
        orders = []
        for method_name in ("fetch_open_orders", "fetch_orders"):
            fetcher = getattr(self.exchange, method_name, None)
            if not callable(fetcher):
                continue
            try:
                rows = fetcher(symbol) or []
                if not isinstance(rows, list):
                    raise TypeError(f"{method_name} response must be a list")
                successful_sources.append(method_name)
            except Exception as exc:
                errors.append({"source": method_name, "error": str(exc)})
                continue
            orders.extend(rows)
        seen = set()
        for order in orders:
            if not isinstance(order, dict):
                continue
            info = order.get("info") or {}
            client_id = (
                order.get("clientOrderId")
                or order.get("clOrdId")
                or info.get("clOrdId")
            )
            if client_id != expected:
                continue
            order_id = order.get("id") or info.get("ordId")
            if order_id in seen:
                continue
            seen.add(order_id)
            status = str(
                order.get("status") or info.get("state") or "unknown"
            ).lower()
            filled = order.get("filled", info.get("accFillSz", 0))
            remaining = order.get("remaining")
            if remaining is None:
                try:
                    remaining = max(0.0, float(info.get("sz", 0)) - float(filled or 0))
                except (TypeError, ValueError):
                    remaining = 0
            if status in _TACTICAL_TERMINAL_ORDER_STATUSES:
                remaining = 0.0
            average = order.get("average", info.get("avgPx"))
            return {
                "query_state": "found",
                "observation": {
                    "order_id": order_id,
                    "client_order_id": expected,
                    "status": status,
                    "filled_qty": float(filled or 0),
                    "remaining_qty": float(remaining or 0),
                    "average_price": (
                        float(average) if average not in (None, "") else None
                    ),
                },
                "successful_sources": successful_sources,
                "errors": errors,
            }
        return {
            "query_state": "not_found" if successful_sources else "query_error",
            "observation": None,
            "successful_sources": successful_sources,
            "errors": errors,
        }

    def query_tactical_close(self, intent: Any) -> Optional[dict]:
        """Find the deterministic V2 close command across open and history views."""
        symbol = self._normalize_symbol(str(getattr(intent, "symbol", "")))
        expected = self.make_tactical_clord_id(intent.intent_id, "close")
        orders = []
        for method_name in ("fetch_open_orders", "fetch_orders"):
            fetcher = getattr(self.exchange, method_name, None)
            if not callable(fetcher):
                continue
            try:
                rows = fetcher(symbol) or []
            except Exception:
                continue
            if isinstance(rows, list):
                orders.extend(rows)
        for order in orders:
            if not isinstance(order, dict):
                continue
            info = order.get("info") or {}
            client_id = (
                order.get("clientOrderId")
                or order.get("clOrdId")
                or info.get("clOrdId")
            )
            if client_id != expected:
                continue
            filled = order.get("filled", info.get("accFillSz", 0))
            remaining = order.get("remaining")
            if remaining is None:
                try:
                    remaining = max(0.0, float(info.get("sz", 0)) - float(filled or 0))
                except (TypeError, ValueError):
                    remaining = 0.0
            return {
                "order_id": order.get("id") or info.get("ordId"),
                "client_order_id": expected,
                "status": str(order.get("status") or info.get("state") or "unknown").lower(),
                "filled_qty": float(filled or 0),
                "remaining_qty": float(remaining or 0),
            }
        return None

    def cancel_tactical_entry(self, intent: Any) -> dict:
        query = self.query_tactical_entry(intent)
        if query.get("query_state") != "found":
            return {
                "proven": False,
                "reason": (
                    "entry_query_error"
                    if query.get("query_state") == "query_error"
                    else "entry_not_found"
                ),
                "query": query,
            }
        observation = query["observation"]
        if observation["remaining_qty"] <= 0:
            return {
                "proven": True,
                "reason": "no_remainder",
                "order_id": observation["order_id"],
                "filled_qty": observation.get("filled_qty", 0.0),
                "average_price": observation.get("average_price"),
            }
        symbol = self._normalize_symbol(str(getattr(intent, "symbol", "")))
        cancel_error = None
        try:
            self.exchange.cancel_order(observation["order_id"], symbol)
        except Exception as exc:
            cancel_error = f"{type(exc).__name__}: {exc}"
            self.logger.warning(
                f"[Tactical V2] {symbol} entry cancel returned {cancel_error}; "
                "rechecking deterministic order identity"
            )
        confirmation = self.query_tactical_entry(intent)
        if confirmation.get("query_state") != "found":
            result = {
                "proven": False,
                "reason": (
                    "cancel_query_error"
                    if confirmation.get("query_state") == "query_error"
                    else "cancel_state_unknown"
                ),
                "order_id": observation["order_id"],
                "query": confirmation,
            }
            if cancel_error is not None:
                result["cancel_error"] = cancel_error
            return result
        confirmed = confirmation["observation"]
        if (
            confirmed["remaining_qty"] <= 0
            and confirmed["status"] in _TACTICAL_TERMINAL_ORDER_STATUSES
        ):
            result = {
                "proven": True,
                "reason": "cancel_confirmed",
                "order_id": observation["order_id"],
                "filled_qty": confirmed["filled_qty"],
                "average_price": confirmed.get("average_price"),
            }
            if cancel_error is not None:
                result["cancel_error"] = cancel_error
            return result
        result = {
            "proven": False,
            "reason": "cancel_unconfirmed",
            "order_id": observation["order_id"],
            "remaining_qty": confirmed["remaining_qty"],
        }
        if cancel_error is not None:
            result["cancel_error"] = cancel_error
        return result

    def verify_tactical_protection(self, intent: Any, *, filled_qty: float) -> dict:
        """Prove exact owner, trigger and filled-quantity coverage for TP and SL."""
        quantity = float(filled_qty)
        if not math.isfinite(quantity) or quantity <= 0:
            return self._incomplete_tactical_proof("invalid_filled_quantity")
        symbol = self._normalize_symbol(str(getattr(intent, "symbol", "")))
        expected_tp_id = self.make_tactical_clord_id(intent.intent_id, "tp")
        expected_sl_id = self.make_tactical_clord_id(intent.intent_id, "sl")
        expected_ids = {expected_tp_id, expected_sl_id}
        expected_tp = float(getattr(intent, "take_profit"))
        expected_sl = float(getattr(intent, "stop_loss"))
        tp_algos = []
        sl_algos = []
        representation = "separate"
        saw_ownership_mismatch = False
        saw_quantity_mismatch = False
        saw_price_mismatch = False

        for row in self._list_pending_algos(symbol):
            client_id = row.get("algoClOrdId")
            tp_raw = row.get("tp_trigger")
            sl_raw = row.get("sl_trigger")
            has_tp = tp_raw not in (None, "", "0")
            has_sl = sl_raw not in (None, "", "0")
            if not has_tp and not has_sl:
                continue
            if client_id not in expected_ids:
                if self._trigger_matches(tp_raw, expected_tp) or self._trigger_matches(
                    sl_raw, expected_sl
                ):
                    saw_ownership_mismatch = True
                continue
            row_qty = row.get("quantity")
            try:
                quantity_matches = math.isclose(
                    float(row_qty), quantity, rel_tol=1e-9, abs_tol=1e-9
                )
            except (TypeError, ValueError):
                quantity_matches = False
            if not quantity_matches:
                saw_quantity_mismatch = True
                continue
            algo_id = row.get("algoId")
            if not algo_id:
                saw_ownership_mismatch = True
                continue
            if has_tp:
                if self._trigger_matches(tp_raw, expected_tp):
                    tp_algos.append(str(algo_id))
                else:
                    saw_price_mismatch = True
            if has_sl:
                if self._trigger_matches(sl_raw, expected_sl):
                    sl_algos.append(str(algo_id))
                else:
                    saw_price_mismatch = True
            if has_tp and has_sl:
                representation = "combined_oco"

        tp_algos = list(dict.fromkeys(tp_algos))
        sl_algos = list(dict.fromkeys(sl_algos))
        if tp_algos and sl_algos:
            return {
                "complete": True,
                "reason": "complete",
                "representation": representation,
                "protected_qty": quantity,
                "tp_algo_ids": tp_algos,
                "sl_algo_ids": sl_algos,
                "entry_client_id": self.make_tactical_clord_id(intent.intent_id, "entry"),
                "tp_client_id": expected_tp_id,
                "sl_client_id": expected_sl_id,
            }
        if saw_ownership_mismatch:
            reason = "ownership_mismatch"
        elif saw_quantity_mismatch:
            reason = "quantity_mismatch"
        elif saw_price_mismatch:
            reason = "price_mismatch"
        elif not tp_algos:
            reason = "missing_tp"
        else:
            reason = "missing_sl"
        proof = self._incomplete_tactical_proof(reason)
        proof["tp_algo_ids"] = tp_algos
        proof["sl_algo_ids"] = sl_algos
        return proof

    def cancel_tactical_protection(self, intent: Any) -> dict:
        """Cancel only algos whose client id exactly matches this Tactical intent."""
        symbol = self._normalize_symbol(str(getattr(intent, "symbol", "")))
        expected_ids = {
            self.make_tactical_clord_id(intent.intent_id, "tp"),
            self.make_tactical_clord_id(intent.intent_id, "sl"),
        }
        cancelled = []
        preserved = []
        for row in self._list_pending_algos(symbol):
            algo_id = row.get("algoId")
            if not algo_id:
                continue
            if row.get("algoClOrdId") not in expected_ids:
                preserved.append(str(algo_id))
                continue
            if self._cancel_algo_by_id(symbol, str(algo_id)):
                cancelled.append(str(algo_id))
        return {
            "cancelled_algo_ids": list(dict.fromkeys(cancelled)),
            "preserved_algo_ids": list(dict.fromkeys(preserved)),
        }

    def close_tactical_position(
        self,
        intent: Any,
        *,
        filled_qty: float,
        ownership_proof: str,
        reason: str = "protection_integrity",
    ) -> dict:
        """Serialize and idempotently close only this proven V2 exposure."""
        expected_entry = self.make_tactical_clord_id(intent.intent_id, "entry")
        if ownership_proof != expected_entry or not self._is_owner_clord_id(ownership_proof):
            raise RuntimeError("Tactical position ownership is not proven")
        symbol = self._normalize_symbol(str(getattr(intent, "symbol", "")))
        close_client_id = self.make_tactical_clord_id(intent.intent_id, "close")
        acquire, holder = self._try_acquire_exit_lock(
            symbol,
            "tactical_v2_close",
            close_client_id,
        )
        if acquire == "locked":
            return {
                "status": "exit_locked",
                "order_id": None,
                "client_order_id": close_client_id,
                "closed_qty": 0.0,
                "reason": reason,
                "lock_holder": holder,
            }
        if acquire == "reentrant":
            existing = self.query_tactical_close(intent)
            if existing is not None:
                return {
                    **existing,
                    "closed_qty": existing.get("filled_qty", 0.0),
                    "reason": reason,
                    "recovered_existing_close": True,
                }
            return {
                "status": "close_in_progress",
                "order_id": None,
                "client_order_id": close_client_id,
                "closed_qty": 0.0,
                "reason": reason,
            }

        try:
            existing = self.query_tactical_close(intent)
            if existing is not None:
                return {
                    **existing,
                    "closed_qty": existing.get("filled_qty", 0.0),
                    "reason": reason,
                    "recovered_existing_close": True,
                }

            exchange_position = self._fetch_okx_position_state(
                symbol,
                raise_on_error=True,
            )
            if exchange_position is None:
                self.cancel_tactical_protection(intent)
                return {
                    "status": "already_flat",
                    "order_id": None,
                    "client_order_id": close_client_id,
                    "closed_qty": 0.0,
                    "reason": reason,
                }
            if exchange_position.get("side") != getattr(intent, "side"):
                raise RuntimeError("Tactical exchange position direction mismatch")

            cleanup = self.cancel_tactical_protection(intent)
            exchange_position = self._fetch_okx_position_state(
                symbol,
                raise_on_error=True,
            )
            if exchange_position is None:
                return {
                    "status": "already_flat",
                    "order_id": None,
                    "client_order_id": close_client_id,
                    "closed_qty": 0.0,
                    "reason": reason,
                    "protective_cleanup": cleanup,
                }
            if exchange_position.get("side") != getattr(intent, "side"):
                raise RuntimeError("Tactical exchange position direction mismatch")

            requested = float(filled_qty)
            available = float(exchange_position.get("available_contracts", 0))
            quantity = min(requested, available)
            if not math.isfinite(quantity) or quantity <= 0:
                raise RuntimeError("Tactical close quantity is not proven")
            quantity = float(self.exchange.amount_to_precision(symbol, quantity))
            if not math.isfinite(quantity) or quantity <= 0:
                raise RuntimeError("Tactical close quantity rounds to zero")
            position = {
                "side": getattr(intent, "side"),
                "strategy_owner": "tactical_v2",
                "intent_id": intent.intent_id,
            }
            params = self._build_okx_close_params(position, clord_id=close_client_id)
            order = self.exchange.create_order(
                symbol=symbol,
                type="market",
                side="sell" if intent.side == "long" else "buy",
                amount=quantity,
                price=None,
                params=params,
            )
            risk_forced = str(reason).startswith("risk_forced:")
            return {
                "status": (order or {}).get("status", "submitted"),
                "order_id": (order or {}).get("id"),
                "client_order_id": close_client_id,
                "closed_qty": quantity,
                "reason": reason,
                "strategy_owner": "tactical_v2",
                "intent_id": intent.intent_id,
                "is_risk_forced": risk_forced,
                "tactical_cost_gate": "fail" if risk_forced else getattr(
                    intent,
                    "tactical_cost_gate",
                    None,
                ),
                "protective_cleanup": cleanup,
                "attribution": {
                    "strategy_owner": "tactical_v2",
                    "exit_profile": "tactical_v2",
                    "close_reason": reason,
                    "is_risk_forced": risk_forced,
                },
            }
        finally:
            self._release_exit_lock(symbol, close_client_id)

    @staticmethod
    def _trigger_matches(raw: Any, expected: float) -> bool:
        try:
            observed = float(raw)
        except (TypeError, ValueError):
            return False
        return math.isfinite(observed) and math.isclose(
            observed,
            float(expected),
            rel_tol=1e-8,
            abs_tol=max(abs(float(expected)) * 1e-8, 1e-12),
        )

    @staticmethod
    def _incomplete_tactical_proof(reason: str) -> dict:
        return {
            "complete": False,
            "reason": reason,
            "representation": "incomplete",
            "protected_qty": 0.0,
            "tp_algo_ids": [],
            "sl_algo_ids": [],
        }

    def _load_sidecar_owner_registry(self):
        try:
            from utils.shadow_tactical_live import ShadowTacticalOwnerRegistry, SidecarPaths
            path = os.getenv("SHADOW_TACTICAL_OWNER_REGISTRY") or SidecarPaths().owners
            return ShadowTacticalOwnerRegistry(path)
        except Exception as e:
            self.logger.warning(f"[SidecarOwner] load failed: {e}")
            return None

    def _is_sidecar_owned_algo_clord_id(self, clord_id: Optional[str]) -> bool:
        if not clord_id:
            return False
        owners = self._load_sidecar_owner_registry()
        if owners is None:
            return False
        try:
            for row in owners.load().get("owners", {}).values():
                if row.get("status") == "open" and row.get("sl_algo_clord_id") == clord_id:
                    return True
        except Exception as e:
            self.logger.warning(f"[SidecarOwner] owner lookup failed: {e}")
        return False

    def _sidecar_symbol_exchange_state(self, symbol: str) -> str:
        owners = self._load_sidecar_owner_registry()
        if owners is None:
            return "unknown"

        sides = ("long", "short")
        try:
            has_owner = any(owners.matches_position(symbol, side) for side in sides)
        except Exception as e:
            self.logger.warning(f"[SidecarOwner] symbol state lookup failed: {e}")
            return "unknown"
        if not has_owner:
            return "none"

        try:
            ex_pos = self._fetch_okx_position_state(symbol, raise_on_error=True)
        except Exception as e:
            self.logger.warning(f"[SidecarOwner] exchange state lookup failed: {e}")
            return "unknown"
        return "present" if ex_pos is not None else "flat"

    def _resolve_attached_sl_algo_id(self, symbol: str,
                                       clord_id: str) -> Optional[str]:
        """在 OKX pending algo 列表中按 algoClOrdId 找回真实 algoId。

        返回 algoId 或 None。失败由调用方负责标记 protection_state。
        本函数只读,不会改本地 position 状态。
        """
        if not clord_id:
            return None
        try:
            fetcher = getattr(self.exchange, 'private_get_trade_orders_algo_pending', None)
            if fetcher is None:
                # ccxt 版本不支持时降级到 fetch_open_orders + ordType=conditional
                try:
                    orders = self.exchange.fetch_open_orders(
                        symbol, params={'ordType': 'conditional'},
                    ) or []
                    for o in orders:
                        info = (o or {}).get('info') or {}
                        if info.get('algoClOrdId') == clord_id:
                            return info.get('algoId') or o.get('id')
                except Exception as e:
                    self.logger.warning(f"[SL Resolve] fetch_open_orders 降级失败: {e}")
                return None
            resp = fetcher({'ordType': 'conditional'})
            data = resp.get('data') if isinstance(resp, dict) else None
            for row in (data or []):
                if row.get('algoClOrdId') == clord_id:
                    return row.get('algoId')
            return None
        except Exception as e:
            self.logger.warning(f"[SL Resolve] {symbol} 查询 algo 失败: {e}")
            return None

    def _verify_attached_sl_after_fill(self, symbol: str, clord_id: str,
                                       *, attempts: int = 3,
                                       sleep_sec: float = 0.5) -> Optional[str]:
        if not clord_id:
            return None
        attempts = max(1, int(attempts or 1))
        for idx in range(attempts):
            algo_id = self._resolve_attached_sl_algo_id(symbol, clord_id)
            if algo_id:
                return algo_id

            for algo in self._list_pending_algos(symbol):
                if algo.get("algoClOrdId") != clord_id:
                    continue
                has_sl = algo.get("sl_trigger") not in (None, "", "0")
                if algo.get("algoId") and has_sl:
                    return algo.get("algoId")

            if idx < attempts - 1 and sleep_sec > 0:
                time.sleep(sleep_sec)
        return None

    def _list_pending_algos(self, symbol: str) -> list:
        """列出 OKX 指定 symbol 的 pending algo orders。

        返回归一化的 dict 列表,每条含 algoId / algoClOrdId / ordType / sl_trigger /
        tp_trigger / side / posSide 等关键字段。仅 OKX 路径有效;其他交易所返回空。

        同时拉 conditional 与 oco 两类:
          - conditional 是新版独立 SL/TP algo
          - oco 是旧版 _build_okx_attach_algo 同时挂 SL+TP 的一体单(2026-05-27 前)
            重启迁移必须能识别 OCO 才能把它转成纯 SL 由本地接管 TP。
        """
        if self.exchange_id != 'okx':
            return []
        rows: list = []
        try:
            fetcher = getattr(
                self.exchange, 'private_get_trade_orders_algo_pending', None,
            )
            if fetcher is not None:
                for ord_type in ('conditional', 'oco'):
                    try:
                        resp = fetcher({'ordType': ord_type})
                        chunk = resp.get('data') if isinstance(resp, dict) else []
                        rows.extend(chunk or [])
                    except Exception as e:
                        self.logger.warning(
                            f"[Migrate] {symbol} 拉 pending algo (ordType={ord_type}) 失败: {e}"
                        )
            else:
                for ord_type in ('conditional', 'oco'):
                    try:
                        orders = self.exchange.fetch_open_orders(
                            symbol, params={'ordType': ord_type},
                        ) or []
                        rows.extend([(o or {}).get('info') or {} for o in orders])
                    except Exception as e:
                        self.logger.warning(
                            f"[Migrate] {symbol} fetch_open_orders (ordType={ord_type}) 失败: {e}"
                        )
        except Exception as e:
            self.logger.warning(f"[Migrate] {symbol} 拉 pending algo 失败: {e}")
            return []

        inst_id = symbol.replace('/', '-').replace(':USDT', '')
        out: list = []
        seen_ids: set = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_inst = row.get('instId') or row.get('inst_id')
            if row_inst and row_inst != inst_id:
                continue
            algo_id = row.get('algoId')
            if algo_id and algo_id in seen_ids:
                continue
            if algo_id:
                seen_ids.add(algo_id)
            out.append({
                'algoId': algo_id,
                'algoClOrdId': row.get('algoClOrdId') or row.get('attachAlgoClOrdId'),
                'ordType': row.get('ordType'),
                'side': row.get('side'),
                'posSide': row.get('posSide'),
                'sl_trigger': row.get('slTriggerPx'),
                'tp_trigger': row.get('tpTriggerPx'),
                'quantity': row.get('sz') or row.get('qty') or row.get('closeFraction'),
                'instId': row_inst or inst_id,
                'state': row.get('state'),
            })
        return out

    def _cancel_algo_by_id(self, symbol: str, algo_id: str) -> bool:
        """按 algoId 撤单。OKX 走 ccxt 标准 cancel_orders + trigger=True。"""
        if not algo_id:
            return True
        if self.exchange_id != 'okx':
            try:
                self.exchange.cancel_order(algo_id, symbol)
                return True
            except Exception as e:
                self.logger.warning(f"[Migrate] {symbol} 撤 {algo_id} 失败: {e}")
                return False
        try:
            # 走 ccxt 标准 cancel_orders + trigger=True，内部会按 OKX 期望格式
            # marshal 成 array body 调 cancel-algos。直接 private_post 传 dict/list
            # 都会被 OKX 拒成 50002 (Incorrect json data format)。
            self.exchange.cancel_orders(
                [algo_id], symbol, params={'trigger': True},
            )
            return True
        except Exception as e:
            self.logger.warning(f"[Migrate] {symbol} 撤 algo {algo_id} 失败: {e}")
            return False

    def _migrate_okx_algos_for_symbol(self, symbol: str) -> dict:
        """FR-07: 启动期/sync 时清理本 symbol 的 OKX 存量 algo。

        步骤:
          1. 列 pending algo (含 conditional + oco)
          2. 本地 Main 仓位的纯 TP algo 一律撤掉
             (本系统 exit_owner=local_partial_tp_exchange_sl);本地无仓位时先
             判断 sidecar ownership/exchange state,避免移除 sidecar 风险保护
          3. OCO algo (旧版 SL+TP 一体单) 视为 SL,但必须 _replace_protective_sl
             转成纯 conditional SL,这样本地 partial TP 才能独立运行
          4. 纯 SL algo 尝试归属本地 position;归属成功 → 写 sl_algo_id,
             protection_state=protected;无法归属(无对应仓位 / 多个 SL / 方向冲突)
             → halt symbol 并撤掉残留
          5. 本地有仓位但 pending 中无 SL → live halt,testnet 重挂

        返回 dict 摘要,包含 cancelled_tp / matched_sl / orphan_sl /
        missing_sl / halted / oco_replaced / foreign_algos /
        sidecar_protected_algos。
        """
        summary = {
            'symbol': symbol,
            'cancelled_tp': 0,
            'matched_sl': None,
            'orphan_sl': 0,
            'missing_sl': False,
            'halted': False,
            'oco_replaced': 0,
            'foreign_algos': 0,
            'sidecar_protected_algos': 0,
        }
        if self.exchange_id != 'okx':
            return summary

        algos = self._list_pending_algos(symbol)
        position = self.positions.get(symbol)
        if position and position.get('strategy_owner') == 'tactical_v2':
            summary['tactical_v2_preserved_algos'] = len(algos)
            self.logger.info(
                f"[Migrate] {symbol} preserve {len(algos)} Tactical V2 algos; "
                "protection ownership belongs to TacticalV2Controller"
            )
            return summary

        tp_only_algos: list = []
        sl_algos: list = []
        oco_algos: list = []
        for algo in algos:
            algo_id = algo.get('algoId')
            if not algo_id:
                continue
            tp_trigger = algo.get('tp_trigger')
            sl_trigger = algo.get('sl_trigger')
            has_tp = tp_trigger not in (None, '', '0')
            has_sl = sl_trigger not in (None, '', '0')
            algo_clord = algo.get('algoClOrdId')
            if (has_tp or has_sl) and self._is_tactical_v2_clord_id(algo_clord):
                summary['tactical_v2_preserved_algos'] = (
                    summary.get('tactical_v2_preserved_algos', 0) + 1
                )
                self.logger.info(
                    f"[Migrate] {symbol} preserve Tactical V2 algo "
                    f"{algo_id} clord={algo_clord}"
                )
                continue
            if (has_tp or has_sl) and (
                self._is_sidecar_owned_algo_clord_id(algo_clord)
                or self._is_foreign_owner_clord_id(algo_clord)
            ):
                summary['foreign_algos'] += 1
                self.logger.info(
                    f"[Migrate] {symbol} preserve foreign/sidecar algo "
                    f"{algo_id} clord={algo_clord}"
                )
                continue
            if has_tp and not has_sl:
                if position is None:
                    tp_only_algos.append(algo)
                    continue
                # 本地 Main 仓位:纯 TP algo,exit_owner 是本地 → 必须撤
                if self._cancel_algo_by_id(symbol, algo_id):
                    summary['cancelled_tp'] += 1
                    self.logger.info(
                        f"[Migrate] {symbol} 取消存量 TP algo {algo_id}"
                    )
                continue
            if has_sl and has_tp:
                # OCO 一体单(旧版 _build_okx_attach_algo): SL 部分要保留,但 TP
                # 必须由本地 partial TP 接管,无法部分撤,只能整撤后重挂纯 SL
                oco_algos.append(algo)
                continue
            if has_sl:
                sl_algos.append(algo)

        if position is None:
            sidecar_state = self._sidecar_symbol_exchange_state(symbol)
            if sidecar_state in ("present", "unknown"):
                for algo in tp_only_algos + sl_algos + oco_algos:
                    summary["sidecar_protected_algos"] += 1
                    self.logger.warning(
                        f"[Migrate] {symbol} preserve ambiguous protection "
                        f"{algo['algoId']} for sidecar-owned exposure "
                        f"(exchange_state={sidecar_state}, ordType={algo.get('ordType')})"
                    )
                return summary

            for algo in tp_only_algos:
                if self._cancel_algo_by_id(symbol, algo['algoId']):
                    summary['cancelled_tp'] += 1
                    self.logger.info(
                        f"[Migrate] {symbol} 取消存量 TP algo {algo['algoId']}"
                    )
            for algo in sl_algos + oco_algos:
                if self._cancel_algo_by_id(symbol, algo['algoId']):
                    summary['orphan_sl'] += 1
                    self.logger.info(
                        f"[Migrate] {symbol} 无本地仓位,撤残留 algo "
                        f"{algo['algoId']} (ordType={algo.get('ordType')})"
                    )
            return summary

        # 本地有仓位:OCO 一律转成纯 SL conditional;然后归属唯一 SL
        if oco_algos:
            if len(oco_algos) > 1 or sl_algos:
                self.logger.error(
                    f"[Migrate] {symbol} 发现 {len(oco_algos)} 条 OCO + "
                    f"{len(sl_algos)} 条 SL,无法归属,全撤并 halt"
                )
                for algo in oco_algos + sl_algos:
                    self._cancel_algo_by_id(symbol, algo['algoId'])
                position['sl_algo_id'] = None
                position['sl_algo_clord_id'] = None
                position['sl_sync_state'] = 'failed'
                position['protection_state'] = 'unknown'
                self._halt_symbol(symbol, reason='migrate_multiple_sl')
                summary['halted'] = True
                self._save_positions()
                return summary

            algo = oco_algos[0]
            side = position.get('side')
            algo_side = (algo.get('side') or '').lower()
            expected_side = 'sell' if side == 'long' else 'buy'
            if algo_side and algo_side != expected_side:
                self.logger.error(
                    f"[Migrate] {symbol} OCO algo side={algo_side} 与本地 "
                    f"side={side} 不匹配,撤单 + halt"
                )
                self._cancel_algo_by_id(symbol, algo['algoId'])
                position['sl_algo_id'] = None
                position['sl_algo_clord_id'] = None
                position['sl_sync_state'] = 'failed'
                position['protection_state'] = 'unknown'
                self._halt_symbol(symbol, reason='migrate_sl_side_conflict')
                summary['halted'] = True
                self._save_positions()
                return summary

            try:
                sl_trigger = float(algo.get('sl_trigger') or 0)
            except (TypeError, ValueError):
                sl_trigger = 0.0
            target_sl = sl_trigger if sl_trigger > 0 else float(position.get('stop_loss') or 0)
            if target_sl <= 0:
                self.logger.error(
                    f"[Migrate] {symbol} OCO algo 无有效 slTriggerPx,撤单 + halt"
                )
                self._cancel_algo_by_id(symbol, algo['algoId'])
                position['sl_algo_id'] = None
                position['sl_algo_clord_id'] = None
                position['sl_sync_state'] = 'failed'
                position['protection_state'] = 'unknown'
                self._halt_symbol(symbol, reason='migrate_missing_sl')
                summary['halted'] = True
                self._save_positions()
                return summary

            # 占位字段,让 _replace_protective_sl 走 cancel(走 cancel_orders+trigger)
            position['sl_algo_id'] = algo['algoId']
            position['sl_order_id'] = algo['algoId']
            ok = self._replace_protective_sl(symbol, position, target_sl)
            if ok:
                summary['oco_replaced'] += 1
                summary['matched_sl'] = position.get('sl_algo_id')
                self.logger.info(
                    f"[Migrate] {symbol} OCO {algo['algoId']} 转成纯 SL "
                    f"{position.get('sl_algo_id')} @ {target_sl}"
                )
                self._save_positions()
                halt_info = getattr(self, "_halted_symbols", {}).get(symbol)
                halt_reason = (halt_info or {}).get("reason", "")
                if halt_reason:
                    self._maybe_auto_clear_protection_halt(
                        symbol,
                        halt_reason,
                        source="self_heal:protection_resolved",
                    )
            else:
                # _replace_protective_sl 失败时已 halt + 写 protection_state
                summary['halted'] = True
                self._save_positions()
            return summary

        if not sl_algos:
            summary['missing_sl'] = True
            position['sl_algo_id'] = None
            position['sl_algo_clord_id'] = None
            position['sl_sync_state'] = 'failed'
            position['protection_state'] = 'unknown'
            # 去重告警 + 幂等 halt（live halt / testnet 不 halt，语义不变）。
            self._alert_protection_unknown(symbol)
            if not self.testnet:
                summary['halted'] = True
            self._save_positions()
            return summary

        if len(sl_algos) > 1:
            self.logger.error(
                f"[Migrate] {symbol} 发现 {len(sl_algos)} 条 SL algo,"
                f"无法归属,全撤并 halt"
            )
            for algo in sl_algos:
                self._cancel_algo_by_id(symbol, algo['algoId'])
            position['sl_algo_id'] = None
            position['sl_algo_clord_id'] = None
            position['sl_sync_state'] = 'failed'
            position['protection_state'] = 'unknown'
            self._halt_symbol(symbol, reason='migrate_multiple_sl')
            summary['halted'] = True
            self._save_positions()
            return summary

        algo = sl_algos[0]
        # 方向校验: long 仓位的 SL algo side 应为 sell (close long)
        side = position.get('side')
        algo_side = (algo.get('side') or '').lower()
        expected_side = 'sell' if side == 'long' else 'buy'
        if algo_side and algo_side != expected_side:
            self.logger.error(
                f"[Migrate] {symbol} SL algo side={algo_side} 与本地 "
                f"side={side} 不匹配,撤单 + halt"
            )
            self._cancel_algo_by_id(symbol, algo['algoId'])
            position['sl_algo_id'] = None
            position['sl_algo_clord_id'] = None
            position['sl_sync_state'] = 'failed'
            position['protection_state'] = 'unknown'
            self._halt_symbol(symbol, reason='migrate_sl_side_conflict')
            summary['halted'] = True
            self._save_positions()
            return summary

        position['sl_algo_id'] = algo['algoId']
        position['sl_order_id'] = algo['algoId']
        position['sl_algo_clord_id'] = algo.get('algoClOrdId')
        position['sl_sync_state'] = 'active'
        position['protection_state'] = 'protected'
        # 重新归属保护后清去重状态: 若日后再丢 SL 须能重新告警。
        if hasattr(self, '_last_protection_alert'):
            self._last_protection_alert.pop(symbol, None)
        try:
            sl_trigger = float(algo.get('sl_trigger') or 0)
            if sl_trigger > 0:
                position['stop_loss'] = sl_trigger
        except (TypeError, ValueError):
            pass
        summary['matched_sl'] = algo['algoId']
        self._save_positions()
        halt_info = getattr(self, "_halted_symbols", {}).get(symbol)
        halt_reason = (halt_info or {}).get("reason", "")
        if halt_reason:
            self._maybe_auto_clear_protection_halt(
                symbol, halt_reason, source="self_heal:protection_resolved"
            )
        self.logger.info(
            f"[Migrate] {symbol} SL algo {algo['algoId']} 归属本地仓位,"
            f"protection_state=protected"
        )
        return summary

    def _migrate_all_symbols_algos(self) -> dict:
        """对所有相关 symbol(本地仓位 ∪ 有 pending algo)调一次迁移。

        - OKX 路径才生效;非 OKX 直接返回空字典
        - 调用方在 sync_positions 末尾使用
        - 返回 symbol → migrate summary,便于审计/测试
        """
        if self.exchange_id != 'okx':
            return {}
        # 收集 symbol 候选: 本地全部 + 全局 pending algo 中出现的 instId
        # 同时拉 conditional + oco,否则旧版 OCO 一体单的 symbol 不在本地仓位时
        # 会被漏掉
        candidates = set(self.positions.keys())
        try:
            fetcher = getattr(
                self.exchange, 'private_get_trade_orders_algo_pending', None,
            )
            if fetcher is not None:
                for ord_type in ('conditional', 'oco'):
                    try:
                        resp = fetcher({'ordType': ord_type})
                        rows = resp.get('data') if isinstance(resp, dict) else []
                        for row in (rows or []):
                            inst = (row or {}).get('instId')
                            if inst:
                                candidates.add(inst)
                    except Exception as e:
                        self.logger.warning(
                            f"[Migrate] 列全局 pending algo (ordType={ord_type}) 失败: {e}"
                        )
        except Exception as e:
            self.logger.warning(f"[Migrate] 列全局 pending algo 失败: {e}")

        results: dict = {}
        for sym in candidates:
            try:
                results[sym] = self._migrate_okx_algos_for_symbol(sym)
            except Exception as e:
                self.logger.warning(f"[Migrate] {sym} 迁移异常: {e}")
        return results

    # ------------------------------------------------------------------
    # OKX 仓位归一化（用于减/平仓前的真实仓位复核）
    # ------------------------------------------------------------------
    def _normalize_okx_position(self, raw_pos: dict) -> Optional[dict]:
        """把 ccxt fetch_positions() 单条返回归一化为 OKXPositionState。"""
        if not raw_pos:
            return None
        info = raw_pos.get('info') or {}
        contracts = raw_pos.get('contracts') or 0
        try:
            contracts_f = abs(float(contracts))
        except (TypeError, ValueError):
            contracts_f = 0.0
        if contracts_f <= 0:
            try:
                contracts_f = abs(float(info.get('pos') or 0))
            except (TypeError, ValueError):
                contracts_f = 0.0
        if contracts_f <= 0:
            return None

        # available_contracts：优先 info.availPos
        avail = info.get('availPos')
        try:
            avail_f = abs(float(avail)) if avail not in (None, '') else contracts_f
        except (TypeError, ValueError):
            avail_f = contracts_f
        if avail_f <= 0:
            avail_f = contracts_f

        side = raw_pos.get('side')
        if side not in ('long', 'short'):
            try:
                pos_signed = float(info.get('pos') or 0)
            except (TypeError, ValueError):
                pos_signed = 0.0
            side = 'long' if pos_signed >= 0 else 'short'

        pos_side = (info.get('posSide') or '').lower() or self._okx_pos_side_for(side)

        try:
            entry = float(raw_pos.get('entryPrice') or info.get('avgPx') or 0)
        except (TypeError, ValueError):
            entry = 0.0
        try:
            lev = int(float(raw_pos.get('leverage') or info.get('lever') or 1))
        except (TypeError, ValueError):
            lev = 1

        raw_sym = raw_pos.get('symbol') or info.get('instId') or ''
        if '/' in raw_sym and ':' in raw_sym:
            base = raw_sym.split('/')[0]
            unified_sym = f"{base}-USDT-SWAP"
        else:
            unified_sym = raw_sym

        return {
            'symbol': unified_sym,
            'side': side,
            'pos_side': pos_side,
            'contracts': contracts_f,
            'available_contracts': avail_f,
            'entry_price': entry,
            'leverage': lev or 1,
            'inst_id': info.get('instId') or unified_sym,
        }

    def _fetch_okx_position_state(
        self, symbol: str, raise_on_error: bool = False
    ) -> Optional[dict]:
        """从 OKX 拉取指定 symbol 的归一化仓位（找不到返回 None）。"""
        if self.exchange_id != 'okx':
            return None
        try:
            try:
                positions = self.exchange.fetch_positions([symbol])
            except Exception:
                positions = self.exchange.fetch_positions()
        except Exception as e:
            self.logger.warning(f"[OKX 仓位复核] fetch_positions 失败: {e}")
            if raise_on_error:
                raise
            return None

        for raw in positions or []:
            norm = self._normalize_okx_position(raw)
            if not norm:
                continue
            if norm['symbol'] == symbol or norm['inst_id'] == symbol:
                return norm
            # 兼容 ccxt unified symbol（BTC/USDT:USDT）和内部 BTC-USDT-SWAP
            if symbol.endswith('-SWAP'):
                base = symbol.split('-')[0]
                if norm['symbol'].startswith(f"{base}-USDT"):
                    return norm
        return None

    # ------------------------------------------------------------------
    # OKX 拒单复核：51169/51205/51112/51333
    # ------------------------------------------------------------------
    def _handle_okx_close_reject(self, symbol: str, error_msg: str,
                                  *, action: str) -> dict:
        """收到 close/reduce 拒单时的状态复核。

        Returns dict:
          - status: 'already_flat' / 'external_closed' / 'still_open' / 'direction_conflict' / 'unknown'
          - position: 归一化仓位（still_open / direction_conflict 时存在）
          - exchange_silent: True 表示交易所未返回数据
        """
        result = {'status': 'unknown', 'position': None, 'exchange_silent': False}
        if self.exchange_id != 'okx':
            return result

        local = self.positions.get(symbol)
        ex_pos = self._fetch_okx_position_state(symbol)

        if ex_pos is None:
            # 交易所确认无仓
            if local:
                self._mark_external_closed(symbol, reason=f"reject_{action}", error_msg=error_msg)
                result['status'] = 'external_closed'
            else:
                result['status'] = 'already_flat'
            return result

        result['position'] = ex_pos
        # 交易所仍有仓位
        if local and local.get('side') and ex_pos['side'] != local['side']:
            result['status'] = 'direction_conflict'
            self.logger.error(
                f"[OKX 拒单复核] {symbol} 方向冲突: 本地{local.get('side')} vs 交易所{ex_pos['side']}; "
                f"err={error_msg}; 暂停自动 close/reduce，等待人工"
            )
            self._halt_symbol(symbol, reason=f"direction_conflict_{action}")
        else:
            result['status'] = 'still_open'
            self.logger.error(
                f"[OKX 拒单复核] {symbol} 交易所仍有仓位 contracts={ex_pos['contracts']} "
                f"available={ex_pos['available_contracts']}; err={error_msg}; 暂停自动 close/reduce"
            )
            self._halt_symbol(symbol, reason=f"reject_{action}")
        return result

    def _mark_external_closed(self, symbol: str, *, reason: str, error_msg: str = '') -> None:
        """交易所确认无仓：清理本地、记录 external_closed，让上层走通知路径。"""
        if symbol not in self.positions:
            return
        if not hasattr(self, '_removed_positions_data'):
            self._removed_positions_data = []
        if not hasattr(self, '_last_removed_symbols'):
            self._last_removed_symbols = []
        pos_data = self.positions[symbol].copy()
        pos_data['symbol'] = symbol
        pos_data['_external_close_reason'] = reason
        if error_msg:
            pos_data['_external_close_error'] = error_msg
        self._removed_positions_data.append(pos_data)
        self._last_removed_symbols.append(symbol)
        del self.positions[symbol]
        self._save_positions()
        if not hasattr(self, '_close_cooldown'):
            self._close_cooldown = {}
        self._close_cooldown[symbol] = time.time() + 60
        if self.idempotency:
            for s in ('long', 'short'):
                try:
                    self.idempotency.clear(symbol, s)
                except Exception:
                    pass
        self.logger.warning(f"[OKX 拒单复核] {symbol} 交易所确认无仓，本地清理 ({reason})")

    def _halt_symbol(self, symbol: str, *, reason: str) -> None:
        """对单 symbol 触发执行级 halt，避免 51169/51205 后继续无限重提。"""
        if not hasattr(self, '_halted_symbols'):
            self._halted_symbols = {}
        self._halted_symbols[symbol] = {
            'reason': reason,
            'halted_at': time.time(),
        }
        try:
            from utils.halt_state import get_halt_state
            get_halt_state().halt(reason=f"okx_{reason}:{symbol}", triggered_by="executor")
        except Exception:
            pass

    def _alert_protection_unknown(self, symbol: str) -> bool:
        """protection-unknown 告警去重: 仅状态变化时记 ERROR + halt。返回是否首次告警。

        防同 symbol+reason 连续多个 sync tick 重复刷 ERROR 与重复 halt。
        """
        if not hasattr(self, '_last_protection_alert'):
            self._last_protection_alert = {}
        if self._last_protection_alert.get(symbol) == 'migrate_missing_sl':
            return False                            # 同因已告警, 去重静默
        self.logger.error(
            f"[Migrate] {symbol} 本地有仓位但交易所无 SL algo,protection_state→unknown"
        )
        self._last_protection_alert[symbol] = 'migrate_missing_sl'
        # testnet 不 halt（与原 [Migrate] 分支语义一致）；live 才 halt，且幂等。
        if not getattr(self, 'testnet', False) and not self.is_symbol_halted(symbol):
            self._halt_symbol(symbol, reason='migrate_missing_sl')
        return True

    def is_symbol_halted(self, symbol: str) -> bool:
        return symbol in getattr(self, '_halted_symbols', {})

    def clear_symbol_halt(self, symbol: Optional[str] = None,
                           *, source: str = "unknown") -> int:
        """清除 per-symbol halt 残留。

        Args:
            symbol: 指定 symbol 仅清该项；None 清全部。
            source: 触发清理的上下文（如 "telegram" / "_handle_resume" / "force_resume"），
                    写入 audit log 用于事后查证。

        Returns:
            清掉的项数（用于审计日志）。
        """
        halted = getattr(self, '_halted_symbols', None)
        if not halted:
            return 0
        if symbol is None:
            n = len(halted)
            cleared_keys = list(halted.keys())
            halted.clear()
            if n > 0:
                self.logger.info(
                    f"[ClearSymbolHalt] source={source} cleared {n} per-symbol halt(s): {cleared_keys}"
                )
            return n
        if symbol in halted:
            reason = halted[symbol].get('reason', '')
            del halted[symbol]
            self.logger.info(
                f"[ClearSymbolHalt] source={source} cleared {symbol} (reason={reason})"
            )
            return 1
        return 0

    def _is_protection_halt_reason(self, reason: str) -> bool:
        return reason in PROTECTION_HALT_REASONS

    def _global_halt_reason_for(self, symbol: str, reason: str) -> str:
        return f"okx_{reason}:{symbol}"

    def _position_protection_unresolved(self, position: Optional[dict]) -> bool:
        if not position:
            return False
        return (position.get("protection_state") or "unknown") != "protected"

    def _find_other_unresolved_protection_halt(self, symbol: str) -> Optional[tuple]:
        halted = getattr(self, "_halted_symbols", {}) or {}
        for other_symbol, info in sorted(halted.items()):
            if other_symbol == symbol:
                continue
            reason = (info or {}).get("reason", "")
            if not self._is_protection_halt_reason(reason):
                continue
            if self._position_protection_unresolved(self.positions.get(other_symbol)):
                return other_symbol, reason
        return None

    def _maybe_auto_clear_protection_halt(
        self, symbol: str, reason: str, *, source: str
    ) -> bool:
        if not self._is_protection_halt_reason(reason):
            return False
        pos = self.positions.get(symbol)
        if self._position_protection_unresolved(pos):
            return False
        try:
            from utils.halt_state import get_halt_state
            expected = self._global_halt_reason_for(symbol, reason)
            halt_state = get_halt_state()
            other = self._find_other_unresolved_protection_halt(symbol)
            if other:
                other_symbol, other_reason = other
                if not halt_state.halted or halt_state.reason != expected:
                    return False
                halt_state.halt(
                    reason=self._global_halt_reason_for(other_symbol, other_reason),
                    triggered_by=source,
                )
                self.clear_symbol_halt(symbol, source=source)
                self.logger.info(
                    f"[SelfHeal] {symbol} protection halt cleared locally; "
                    f"global halt remains for {other_symbol} "
                    f"(reason={other_reason}, source={source})"
                )
                return False
            cleared = halt_state.auto_clear_if_reason(expected, cleared_by=source)
        except Exception as e:
            self.logger.warning(
                f"[SelfHeal] {symbol} protection halt auto-clear failed: {e}"
            )
            return False
        if not cleared:
            return False
        self.clear_symbol_halt(symbol, source=source)
        self.logger.info(
            f"[SelfHeal] {symbol} protection halt cleared "
            f"(reason={reason}, source={source})"
        )
        return True

    def get_halted_symbols(self) -> Dict[str, dict]:
        """返回 _halted_symbols 顶层浅拷贝快照。

        调用方 MUST NOT 修改返回 dict 的 value（内部 dict 引用复用）。
        """
        return dict(getattr(self, '_halted_symbols', {}))

    # ------------------------------------------------------------------
    # FR-06: per-symbol exit lock
    # ------------------------------------------------------------------
    def _try_acquire_exit_lock(self, symbol: str, kind: str,
                                action_id: Optional[str]) -> tuple:
        """串行化 close/reduce/partial_tp/risk_alert 等同 symbol 退出动作。

        返回:
            ('acquired', None)        - 调用方持锁,必须在 finally 中释放。
            ('reentrant', cur_state)  - 同 action_id 重入,调用方应当幂等返回。
            ('locked', cur_state)     - 不同动作占用,调用方应记录 exit_locked 并放弃。
        """
        if not hasattr(self, '_exit_lock_mu') or self._exit_lock_mu is None:
            self._exit_lock_mu = threading.Lock()
        if not hasattr(self, '_exit_locks') or self._exit_locks is None:
            self._exit_locks = {}
        with self._exit_lock_mu:
            cur = self._exit_locks.get(symbol)
            if cur is None:
                self._exit_locks[symbol] = {
                    'kind': kind,
                    'action_id': action_id,
                    'started_at': time.time(),
                }
                return ('acquired', None)
            if action_id and cur.get('action_id') == action_id:
                return ('reentrant', dict(cur))
            return ('locked', dict(cur))

    def _release_exit_lock(self, symbol: str, action_id: Optional[str]) -> None:
        """仅释放属于自己 action_id 的锁,避免误释放并发锁。"""
        if not hasattr(self, '_exit_lock_mu') or self._exit_lock_mu is None:
            return
        if not hasattr(self, '_exit_locks') or self._exit_locks is None:
            return
        with self._exit_lock_mu:
            cur = self._exit_locks.get(symbol)
            if cur is not None and cur.get('action_id') == action_id:
                del self._exit_locks[symbol]

    def get_balance(self) -> float:
        """获取USDT余额（total，含持仓保证金，用于回撤计算）"""
        if self.balance_adapter:
            return self.balance_adapter.get_total()
        try:
            balance = self.exchange.fetch_balance()
            return balance['USDT']['total']
        except Exception as e:
            self.logger.error(f"获取余额失败: {e}")
            return 0.0

    def open_long(self, symbol: str, amount_usdt: float) -> Optional[Dict]:
        """开多仓"""
        return self._open_position(symbol, 'long', amount_usdt)

    def open_short(self, symbol: str, amount_usdt: float) -> Optional[Dict]:
        """开空仓"""
        return self._open_position(symbol, 'short', amount_usdt)

    def _normalize_symbol(self, symbol: str) -> str:
        """确保使用SWAP格式"""
        if not symbol.endswith('-SWAP') and '-USDT' in symbol:
            return symbol + '-SWAP'
        return symbol

    def _open_position(self, symbol: str, side: str, amount_usdt: float) -> Optional[Dict]:
        """开仓"""
        symbol = self._normalize_symbol(symbol)
        # OKX：posMode 未知则禁止开新仓
        if self.exchange_id == 'okx' and not self.can_open_new_okx():
            self.logger.error(f"[OKX posMode] 未知，禁止开新仓: {symbol}")
            return None
        # 单 symbol halt：拒单复核进入 halt 后不再继续提交
        if self.is_symbol_halted(symbol):
            self.logger.warning(f"[Halt] {symbol} 已 halt，拒绝开新仓")
            return None
        # P1-M: 幂等防护——10s 内同 (symbol, side) 重复请求直接拒
        if self.idempotency:
            is_dup, prior = self.idempotency.is_duplicate(symbol, side)
            if is_dup:
                self.logger.warning(f"幂等拒绝: {symbol} {side} 10s 内已有开单请求 (prior={prior})")
                return None
        try:
            # 风控检查
            balance = self.get_balance()
            can_trade, msg = self.risk_manager.check_can_trade(balance)
            if not can_trade:
                self.logger.warning(f"风控拒绝: {msg}")
                return None

            # 获取当前价格
            ticker = self.exchange.fetch_ticker(symbol)
            current_price = ticker['last']

            # 计算仓位（保证金）
            position_size = self.risk_manager.calculate_position_size(balance)
            position_size = min(position_size, amount_usdt)

            # 设置杠杆
            try:
                self.exchange.set_leverage(self.leverage, symbol)
                self.logger.info(f"设置杠杆: {self.leverage}x")
            except Exception as e:
                self.logger.warning(f"设置杠杆失败（可能已设置）: {e}")

            # P1-2: 订单参数预检
            if self.caps:
                ok, reason, _ = self.caps.precheck_order(
                    symbol=symbol, side='buy' if side == 'long' else 'sell',
                    size_usdt=position_size, price=current_price, leverage=self.leverage
                )
                if not ok:
                    self.logger.warning(f"[precheck] {symbol} 开仓拒绝: {reason}")
                    return None

            # 计算数量（合约张数）：名义价值 / (价格 × 合约面值)
            market = self.exchange.market(symbol)
            contract_size = market.get('contractSize', 1)
            notional = position_size * self.leverage
            amount = notional / (current_price * contract_size)
            amount = float(self.exchange.amount_to_precision(symbol, amount))
            if amount < market.get('limits', {}).get('amount', {}).get('min', 1):
                self.logger.warning(f"下单数量{amount}低于最小限制，放弃")
                return None

            # 创建合约订单
            order_side = 'buy' if side == 'long' else 'sell'
            # P1-M: 为 OKX 附加 clOrdId 实现交易所端幂等
            clord_id = None
            if self.idempotency and self.exchange_id == 'okx':
                clord_id = self.idempotency.gen_client_order_id(symbol, side)
            order_params = self._build_open_order_params(side, clord_id=clord_id)
            order = self.exchange.create_order(
                symbol=symbol,
                type='market',
                side=order_side,
                amount=amount,
                params=order_params
            )
            if self.idempotency and clord_id:
                self.idempotency.mark(symbol, side, clord_id)

            # 真实成交价：通过 ledger 查询 fetch_order 获取
            fill_price = current_price
            if self.ledger:
                try:
                    ledger_event = self.ledger.record_open(
                        order_id=order['id'], symbol=symbol, side=side,
                        amount_usdt=position_size, leverage=self.leverage,
                        estimated_price=current_price
                    )
                    fill_price = ledger_event['fill_price']
                except Exception as e:
                    self.logger.warning(f"[Ledger] 开仓记录失败（降级用ticker）: {e}")

            # 计算止损止盈
            stop_loss = self.risk_manager.calculate_stop_loss(fill_price, side)
            take_profit = self.risk_manager.calculate_take_profit(fill_price, side)

            # 在交易所设置 SL 条件单（OKX 走独立 algo；非 OKX 走旧路径）
            sl_clord_id = self._make_owner_tag_clord_id(symbol) if self.exchange_id == 'okx' and stop_loss else None
            sl_order_id = self._place_protective_sl(
                symbol=symbol, side=side, stop_price=stop_loss, amount=amount,
                clord_id=sl_clord_id,
            )

            # 记录持仓
            position = {
                'symbol': symbol,
                'side': side,
                'entry_price': fill_price,
                'amount': amount,
                'amount_usdt': position_size,
                'leverage': self.leverage,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'order_id': order['id'],
                'sl_order_id': sl_order_id,
                # FR-02: 保护单生命周期字段。legacy open_position 走独立 SL algo,
                # 成功时 sl_order_id 即为 OKX algoId,可视为已 protected。
                'exit_owner': 'local_partial_tp_exchange_sl',
                'sl_algo_id': sl_order_id if self.exchange_id == 'okx' else None,
                'sl_algo_clord_id': sl_clord_id,
                'sl_sync_state': 'active' if sl_order_id else 'failed',
                'protection_state': 'protected' if sl_order_id else 'unprotected',
                'open_time': time.time(),
            }
            self.positions[symbol] = position
            self._save_positions()

            self.logger.info(f"开仓成功: {side} {symbol}, 价格: {fill_price}, 数量: {amount}, 杠杆: {self.leverage}x")
            return position

        except Exception as e:
            self.logger.error(f"开仓失败: {e}")
            return None

    def _build_open_order_params(self, side: str, *, clord_id: Optional[str] = None,
                                 attach_algo: Optional[list] = None) -> dict:
        """非 OKX 走原 reduceOnly=False；OKX 走构造器。"""
        if self.exchange_id == 'okx':
            return self._build_okx_open_params(side, clord_id=clord_id, attach_algo=attach_algo)
        params: dict = {'reduceOnly': False}
        if attach_algo:
            params['attachAlgoOrds'] = attach_algo
        if clord_id:
            params['clOrdId'] = clord_id
        return params

    def _recompute_plan_for_drift(self, plan: dict, new_entry: float,
                                  drift_band: str) -> Optional[dict]:
        """按 plan.sl_pct / tp_pct 同比例平移 SL/TP 到 new_entry。

        medium band floor 加成 +0.20。R:R 复检不过返回 None。
        不修改原 plan（deepcopy）。
        """
        import copy
        new_plan = copy.deepcopy(plan)
        side = plan.get('side')
        sl_pct = plan.get('sl_pct')
        tp_pct = plan.get('tp_pct') or []
        if not sl_pct or not tp_pct or new_entry <= 0:
            return None

        if side == 'long':
            new_sl = new_entry * (1 - sl_pct)
            new_tp = [new_entry * (1 + p) for p in tp_pct]
        else:
            new_sl = new_entry * (1 + sl_pct)
            new_tp = [new_entry * (1 - p) for p in tp_pct]

        sl_dist = sl_pct
        tp_dist = abs(new_tp[0] - new_entry) / new_entry
        rr_actual = tp_dist / sl_dist if sl_dist > 0 else 0.0

        base_floor = (plan.get('attribution') or {}).get('rr_floor', 2.0)
        bump = ENTRY_DRIFT_MEDIUM_FLOOR_BUMP if drift_band == 'medium' else 0.0
        floor_used = base_floor + bump

        if rr_actual < floor_used:
            return None

        new_plan['stop_loss'] = new_sl
        new_plan['take_profit'] = new_tp
        new_plan['recompute_reason'] = f'drift_{drift_band}'
        new_plan['original_entry_ref'] = plan.get('entry_ref')
        new_plan['recomputed_entry'] = new_entry
        new_plan['recomputed_sl'] = new_sl
        new_plan['recomputed_tp'] = new_tp
        new_plan['rr_floor_used'] = floor_used
        new_plan['rr_actual_after_recompute'] = rr_actual
        return new_plan

    def _enqueue_drift_alert(self, alert_type: str, **fields) -> None:
        """Buffer a drift-related risk alert for the agent layer to drain & publish."""
        alert = {
            'type': alert_type,
            'timestamp': time.time(),
            'source': fields.pop('source', 'executor'),
            **fields,
        }
        self._pending_drift_alerts.append(alert)

    def _record_drift_decision_event(self, symbol: str, side: str,
                                     decision: 'DriftDecision', gate: str) -> None:
        """Record drift decision to live order events jsonl (full impl in Task 8)."""
        if self.ledger:
            try:
                self.ledger.record_entry_drift_decision(
                    symbol=symbol, side=side, gate=gate,
                    band=decision.band, drift_pct=decision.drift_pct,
                    decision=decision.decision, reason=decision.reason,
                    rr_actual=decision.rr_actual,
                    rr_floor_used=decision.rr_floor_used,
                )
            except (AttributeError, Exception) as e:
                self.logger.warning(f"[Drift Event] record failed: {e}")

    def _classify_entry_drift(self, plan: dict, live_price: float) -> 'DriftDecision':
        """Drift gate single source of truth.

        Bands (boundary inclusive on the lower side of the next band):
          drift <= 0.005        → accept
          0.005 < drift <= 0.02 → small (recompute, floor unchanged)
          0.02  < drift <= 0.05 → medium (recompute, floor + 0.20)
          drift > 0.05          → abandon (reason=drift_too_large)
        Plan missing entry_ref/sl_pct/tp_pct → fail-safe accept (drift_pct=0.0)
        + risk_alert.plan_missing_entry_ref enqueued.
        """
        entry_ref = plan.get('entry_ref')
        sl_pct = plan.get('sl_pct')
        tp_pct = plan.get('tp_pct')
        if not entry_ref or not sl_pct or not tp_pct or live_price <= 0:
            self._enqueue_drift_alert(
                'plan_missing_entry_ref',
                symbol=plan.get('symbol'),
                has_entry_ref=bool(entry_ref),
                has_sl_pct=bool(sl_pct),
                has_tp_pct=bool(tp_pct),
            )
            return DriftDecision(
                band='accept', drift_pct=0.0, decision='accept',
                reason=None, new_plan=None, rr_actual=None, rr_floor_used=None,
            )

        drift = abs(live_price - entry_ref) / entry_ref

        if drift <= ENTRY_DRIFT_ACCEPT_PCT:
            return DriftDecision(
                band='accept', drift_pct=drift, decision='accept',
                reason=None, new_plan=None, rr_actual=None, rr_floor_used=None,
            )

        if drift > ENTRY_DRIFT_LARGE_PCT:
            return DriftDecision(
                band='abandon', drift_pct=drift, decision='abandon',
                reason='drift_too_large',
                new_plan=None, rr_actual=None, rr_floor_used=None,
            )

        band = 'small' if drift <= ENTRY_DRIFT_SMALL_PCT else 'medium'
        new_plan = self._recompute_plan_for_drift(plan, live_price, band)
        if new_plan is None:
            base_floor = (plan.get('attribution') or {}).get('rr_floor', 2.0)
            floor_used = base_floor + (ENTRY_DRIFT_MEDIUM_FLOOR_BUMP if band == 'medium' else 0.0)
            return DriftDecision(
                band=band, drift_pct=drift, decision='recalc_fail',
                reason='drift_rr_floor_fail',
                new_plan=None, rr_actual=None, rr_floor_used=floor_used,
            )
        return DriftDecision(
            band=band, drift_pct=drift, decision='recalc_pass',
            reason=None, new_plan=new_plan,
            rr_actual=new_plan['rr_actual_after_recompute'],
            rr_floor_used=new_plan['rr_floor_used'],
        )

    def _build_close_order_params(self, position: dict, *, clord_id: Optional[str] = None) -> dict:
        """非 OKX 走原 reduceOnly=True；OKX 走构造器。"""
        if self.exchange_id == 'okx':
            return self._build_okx_close_params(position, clord_id=clord_id)
        params: dict = {'reduceOnly': True}
        if clord_id:
            params['clOrdId'] = clord_id
        return params

    def _place_protective_sl(self, *, symbol: str, side: str, stop_price: float,
                              amount: float,
                              clord_id: Optional[str] = None) -> Optional[str]:
        """挂独立保护单 SL：
        - OKX：走 algo (slTriggerPx + posSide)，反向 side，不传 reduceOnly
        - 其他：走原 stopLossPrice + reduceOnly=True

        clord_id 仅 OKX 生效,透传 algoClOrdId,便于失败时回查 algoId。
        """
        if not stop_price or amount <= 0:
            return None
        if self.exchange_id == 'okx':
            try:
                params = self._build_okx_algo_params(
                    {'side': side},
                    sl_trigger=stop_price, sl_ord_px='-1',
                )
                algo_side = params.pop('side')
                if clord_id:
                    params['algoClOrdId'] = clord_id
                algo_order = self.exchange.create_order(
                    symbol=symbol,
                    type='conditional',
                    side=algo_side,
                    amount=amount,
                    params=params,
                )
                self.logger.info(f"[OKX algo] SL 条件单设置成功: {symbol} @ {stop_price}")
                return algo_order.get('id')
            except Exception as e:
                self.logger.warning(f"[OKX algo] SL 设置失败（本地兜底）: {e}")
                return None
        try:
            sl_side = 'sell' if side == 'long' else 'buy'
            sl_order = self.exchange.create_order(
                symbol=symbol, type='market', side=sl_side, amount=amount,
                params={'reduceOnly': True, 'stopLossPrice': stop_price},
            )
            self.logger.info(f"SL 条件单设置成功: {stop_price:.6f}")
            return sl_order.get('id')
        except Exception as e:
            self.logger.warning(f"设置SL条件单失败（本地兜底）: {e}")
            return None

    def _cleanup_protective_orders_on_close(self, symbol: str, position: dict) -> Dict[str, Any]:
        """FR-3B: close path 唯一保护单清理点,owner-bound。

        只取消以下 algo:
          - 本地 position 记录的 sl_algo_id / sl_order_id
          - algoClOrdId 精确等于本地 sl_algo_clord_id
          - lifecycle/ledger 中记录为本系统创建的 algo id
          - 新 owner 前缀(ca+namespace+bot_instance)匹配的 algoClOrdId
        历史 'sl' 前缀只能通过 exact sl_algo_clord_id 识别 owner,不做泛化 sweep。

        返回结构化结果(FR-3B):
          {
            'ok': bool,                 # 整体清理成功
            'symbol': str,
            'state': cleaned/none/failed/foreign_algos_present/unknown,
            'known_cancel_ok': bool,
            'cancelled_algo_ids': [...],
            'owned_algo_ids': [...],
            'foreign_algo_ids': [...],
            'unknown_algo_count': int,
            'warnings': [...],
            'halt_required': bool,
            'timestamp': float,
          }
        """
        result: Dict[str, Any] = {
            'ok': True,
            'symbol': symbol,
            'operation': 'cleanup_protective_orders_on_close',
            'state': 'none',
            'known_cancel_ok': True,
            'cancelled_algo_ids': [],
            'owned_algo_ids': [],
            'foreign_algo_ids': [],
            'unknown_algo_count': 0,
            'warnings': [],
            'halt_required': False,
            'timestamp': time.time(),
        }

        known_sl_algo = position.get('sl_algo_id') or position.get('sl_order_id')
        known_clord = position.get('sl_algo_clord_id')
        known_ids = set()
        if known_sl_algo:
            known_ids.add(str(known_sl_algo))

        had_known = bool(known_sl_algo)
        cancel_ok = True
        if had_known:
            cancel_ok = self._cancel_protective_sl(symbol, position)
            if cancel_ok:
                result['cancelled_algo_ids'].append(str(known_sl_algo))
                result['owned_algo_ids'].append(str(known_sl_algo))
        result['known_cancel_ok'] = cancel_ok if had_known else True

        if self.exchange_id != 'okx':
            if not had_known:
                result['state'] = 'none'
            elif not cancel_ok:
                result['state'] = 'failed'
                result['ok'] = False
            else:
                result['state'] = 'cleaned'
            return result

        # OKX: 列剩余 pending algo,做 owner 判定
        try:
            algos = self._list_pending_algos(symbol)
        except Exception as e:
            self.logger.warning(f"[Cleanup] {symbol} 列 pending algos 失败: {e}")
            result['warnings'].append(f'list_pending_algos_failed: {e}')
            if had_known:
                result['state'] = 'cleaned' if cancel_ok else 'failed'
                result['ok'] = cancel_ok
            else:
                result['state'] = 'unknown'
                result['ok'] = False
            return result

        sweep_failed = False
        for algo in algos or []:
            algo_id = algo.get('algoId')
            if not algo_id:
                continue
            if algo_id in known_ids:
                continue  # 已在 _cancel_protective_sl 处理
            algo_clord = algo.get('algoClOrdId') or ''

            # owner 判定: 三层
            # 1) algoClOrdId exact == 本地 sl_algo_clord_id
            # 2) lifecycle/ledger 已知 algo id (暂用 known_ids;后续可扩展)
            # 3) 新 owner prefix 匹配
            is_owned = False
            if known_clord and algo_clord == str(known_clord):
                is_owned = True
            elif self._is_owner_clord_id(algo_clord):
                is_owned = True

            if is_owned:
                try:
                    self.exchange.cancel_orders(
                        [algo_id], symbol, params={'trigger': True},
                    )
                    result['cancelled_algo_ids'].append(str(algo_id))
                    result['owned_algo_ids'].append(str(algo_id))
                    self.logger.info(
                        f"[Cleanup] {symbol} 撤 owner algo {algo_id} clord={algo_clord}"
                    )
                except Exception as e:
                    self.logger.warning(
                        f"[Cleanup] {symbol} 撤 owner algo {algo_id} 失败: {e}"
                    )
                    sweep_failed = True
                    result['warnings'].append(f'cancel_owned_failed:{algo_id}:{e}')
            else:
                # foreign / unknown: 不撤,只记录 + 告警
                if algo_clord:
                    result['foreign_algo_ids'].append(str(algo_id))
                    self.logger.warning(
                        f"[Cleanup] {symbol} foreign algo {algo_id} clord={algo_clord} 不撤"
                    )
                else:
                    result['unknown_algo_count'] += 1
                    self.logger.warning(
                        f"[Cleanup] {symbol} unknown algo {algo_id}(无 clord)不撤"
                    )

        # 终态判定
        if not had_known and not algos:
            result['state'] = 'none'
        elif sweep_failed or (had_known and not cancel_ok):
            result['state'] = 'failed'
            result['ok'] = False
        elif result['foreign_algo_ids'] or result['unknown_algo_count'] > 0:
            result['state'] = 'foreign_algos_present'
            result['ok'] = False
            result['halt_required'] = True
            result['warnings'].append('foreign_algo_not_cancelled')
        else:
            result['state'] = 'cleaned'

        return result

    def _cancel_protective_sl(self, symbol: str, position: dict) -> bool:
        """撤当前持仓的保护单 SL。

        - OKX 优先用 algoId 走 algo 撤单;否则降级 cancel_order(trigger=True)
        - 非 OKX 走原 cancel_order
        - 已不存在视为成功(撤无所谓)
        """
        algo_id = position.get('sl_algo_id') or position.get('sl_order_id')
        if not algo_id:
            return True
        if self.exchange_id == 'okx':
            try:
                # ccxt 标准入口，内部按 OKX 期望格式 marshal 成 array body。
                # 直接用 private_post_trade_cancel_algos 传 dict/list 都会被 OKX 拒成 50002
                # (Incorrect json data format)，必须走 cancel_orders + trigger=True。
                self.exchange.cancel_orders(
                    [algo_id], symbol, params={'trigger': True},
                )
                return True
            except Exception as e:
                self.logger.warning(
                    f"[SL Cancel] {symbol} OKX 撤 algo {algo_id} 失败: {e}"
                )
                return False
        try:
            self.exchange.cancel_order(algo_id, symbol)
            return True
        except Exception as e:
            self.logger.warning(f"[SL Cancel] {symbol} 撤单失败: {e}")
            return False

    def move_protective_sl(self, symbol: str, new_sl: float, *,
                            reason: str = 'unspecified',
                            action_id: Optional[str] = None) -> dict:
        """FR-001 公开入口: Agent 层移动 SL 必须通过此方法。

        - 调用方只传目标 SL 和归因 reason,不得直接读写本地 stop_loss
        - 只有交易所保护单替换成功后,才会同步更新本地 position['stop_loss']
            和 sl_algo_id/sl_algo_clord_id/sl_sync_state/protection_state
        - 替换失败时,本地 stop_loss 保留旧值;sl_sync_state/protection_state
            由 _replace_protective_sl 写为 failed/unknown,live OKX 还会触发
            _halt_symbol(reason='sl_cancel_failed' 或 'sl_replace_failed')

        返回 ProtectiveSLResult dict (FR-001/8.1)。调用方可用 result['ok']
        判定;失败时不可在本地另行覆盖 stop_loss。

        action_id 为可选幂等键,当前仅记录在结果里;后续如需 dedupe 由本方法
        持有 symbol 级 token。
        """
        result = {
            'ok': False,
            'symbol': symbol,
            'operation': 'move_protective_sl',
            'reason': reason,
            'old_sl_algo_id': None,
            'new_sl_algo_id': None,
            'old_stop_loss': None,
            'new_stop_loss': None,
            'cancel_ok': False,
            'place_ok': False,
            'sl_sync_state': 'unknown',
            'protection_state': 'unknown',
            'halt_required': False,
            'action_id': action_id,
            'timestamp': time.time(),
        }
        position = self.positions.get(symbol)
        if not position:
            result['reason'] = 'position_missing'
            return result
        if position.get('strategy_owner') == 'tactical_v2':
            result['reason'] = 'strategy_owner_isolated'
            return result
        if new_sl is None or new_sl <= 0:
            result['reason'] = 'invalid_new_sl'
            return result
        old_sl_algo_id = position.get('sl_algo_id') or position.get('sl_order_id')
        old_stop_loss = position.get('stop_loss')
        result['old_sl_algo_id'] = old_sl_algo_id
        result['old_stop_loss'] = old_stop_loss

        ok = self._replace_protective_sl(symbol, position, new_sl)
        result['ok'] = ok
        result['cancel_ok'] = ok or position.get('last_protection_error') != 'sl_cancel_failed'
        result['place_ok'] = ok
        result['sl_sync_state'] = position.get('sl_sync_state', 'unknown')
        result['protection_state'] = position.get('protection_state', 'unknown')
        result['new_sl_algo_id'] = position.get('sl_algo_id')

        if ok:
            position['stop_loss'] = new_sl
            position['last_protection_update_reason'] = reason
            result['new_stop_loss'] = new_sl
            self._save_positions()
            self.logger.info(
                f"[SL Move] {symbol} {old_stop_loss} → {new_sl} reason={reason}"
            )
            return result

        # 失败: 不改本地 stop_loss;_replace_protective_sl 已写入失败状态字段
        result['halt_required'] = (
            self.exchange_id == 'okx' and not self.testnet
        )
        result['reason'] = position.get('last_protection_error') or reason
        # 失败时也持久化保护字段,便于重启诊断
        try:
            self._save_positions()
        except Exception:
            pass
        self.logger.error(
            f"[SL Move] {symbol} 移动失败 old={old_stop_loss} target={new_sl} "
            f"reason={reason} state={result['protection_state']}"
        )
        return result

    def _replace_protective_sl(self, symbol: str, position: dict,
                                 new_sl: float) -> bool:
        """FR-04 单一入口: 撤旧 SL → 挂新 SL → 更新保护字段。

        所有 SL 价位变更(BE move / TP1 lock / ATR trail / add_position 重挂)
        必须经由此函数,使 position['sl_algo_id'] 始终对应交易所唯一保护单。

        FR-002 fail-closed:
          - 旧 SL 撤单失败时禁止下新 SL,避免交易所同时存在两条保护单
          - 撤旧失败保留旧 sl_algo_id(旧保护单仍然有效),写
            sl_sync_state=failed/protection_state=unknown/last_protection_error
          - live OKX 触发 _halt_symbol(reason='sl_cancel_failed')
          - 撤旧成功但新 SL 挂单失败 → 清空 sl_algo_id,标 protection_state=unknown,
            live OKX halt(reason='sl_replace_failed')
        """
        if new_sl is None or new_sl <= 0:
            return False
        side = position.get('side')
        amount = position.get('amount')
        if not side or not amount or amount <= 0:
            self.logger.warning(
                f"[SL Replace] {symbol} side/amount 缺失,跳过: side={side} amount={amount}"
            )
            return False

        old_sl_algo_id = position.get('sl_algo_id') or position.get('sl_order_id')
        cancel_ok = self._cancel_protective_sl(symbol, position)
        if not cancel_ok:
            # FR-002 AC-P0-004/005/006: 撤旧失败立即返回,不挂新 SL
            position['sl_sync_state'] = 'failed'
            position['protection_state'] = 'unknown'
            position['last_protection_error'] = 'sl_cancel_failed'
            self.logger.error(
                f"[SL Replace] {symbol} 撤旧 SL {old_sl_algo_id} 失败,"
                f"放弃挂新 SL,protection_state=unknown"
            )
            if self.exchange_id == 'okx' and not self.testnet:
                self._halt_symbol(symbol, reason='sl_cancel_failed')
            return False

        new_clord = self._make_owner_tag_clord_id(symbol) if self.exchange_id == 'okx' else None
        new_id = self._place_protective_sl(
            symbol=symbol, side=side, stop_price=new_sl, amount=amount,
            clord_id=new_clord,
        )
        if new_id:
            position['sl_order_id'] = new_id
            position['sl_algo_id'] = new_id if self.exchange_id == 'okx' else None
            position['sl_algo_clord_id'] = new_clord
            position['sl_sync_state'] = 'active'
            position['protection_state'] = 'protected'
            position.pop('last_protection_error', None)
            return True

        position['sl_order_id'] = None
        position['sl_algo_id'] = None
        position['sl_algo_clord_id'] = None
        position['sl_sync_state'] = 'failed'
        position['protection_state'] = 'unknown'
        position['last_protection_error'] = 'sl_place_failed'
        self.logger.error(
            f"[SL Replace] {symbol} 新 SL 挂单失败,protection_state=unknown"
        )
        if self.exchange_id == 'okx' and not self.testnet:
            self._halt_symbol(symbol, reason='sl_replace_failed')
        return False

    def close_position(self, symbol: str,
                       action_id: Optional[str] = None,
                       action_kind: str = 'close') -> Optional[Dict]:
        """平仓

        action_id / action_kind: FR-06 exit lock 串行化标识。
        默认每次调用生成新的 action_id;同一 action_id 重入返回 None(幂等)。
        """
        if symbol not in self.positions:
            self.logger.warning(f"没有持仓: {symbol}")
            return None
        if self.positions[symbol].get('strategy_owner') == 'tactical_v2':
            self.logger.warning(
                f"[OwnerIsolation] {symbol} Tactical V2 拒绝 generic close_position"
            )
            return None

        if action_id is None:
            action_id = f"{action_kind}-{symbol}-{uuid.uuid4().hex[:8]}"
        acquire, holder = self._try_acquire_exit_lock(symbol, action_kind, action_id)
        if acquire == 'locked':
            self.logger.warning(
                f"[ExitLock] {symbol} {action_kind} 被 {holder.get('kind')} "
                f"({holder.get('action_id')}) 占用,exit_locked 拒绝"
            )
            return None
        if acquire == 'reentrant':
            self.logger.info(f"[ExitLock] {symbol} {action_kind} 重入,幂等返回")
            return None

        try:
            position = self.positions[symbol]

            # 获取当前价格（作为估算兜底）
            ticker = self.exchange.fetch_ticker(symbol)
            exit_price = ticker['last']

            # OKX：close 前以交易所真实仓位为准，限制 amount<=available_contracts
            close_amount = position['amount']
            if self.exchange_id == 'okx':
                ex_pos = self._fetch_okx_position_state(symbol)
                if ex_pos is None:
                    # 交易所已无仓：本地清理 + already_flat
                    self._mark_external_closed(symbol, reason='close_already_flat')
                    return None
                if ex_pos['side'] != position['side']:
                    self.logger.error(
                        f"[OKX close] {symbol} 方向冲突: 本地{position['side']} vs 交易所{ex_pos['side']}, 暂停"
                    )
                    self._halt_symbol(symbol, reason='direction_conflict_close')
                    return None
                if close_amount > ex_pos['available_contracts']:
                    self.logger.warning(
                        f"[OKX close] {symbol} 数量超出可平 {close_amount} > {ex_pos['available_contracts']}, 收敛"
                    )
                    close_amount = ex_pos['available_contracts']
                # 通过 amount_to_precision 处理精度
                try:
                    close_amount = float(self.exchange.amount_to_precision(symbol, close_amount))
                except Exception:
                    pass

            order_side = 'sell' if position['side'] == 'long' else 'buy'
            close_params = self._build_close_order_params(position)
            order = self.exchange.create_order(
                symbol=symbol,
                type='market',
                side=order_side,
                amount=close_amount,
                params=close_params,
            )

            # FR-3B: close path 唯一保护单清理点,owner-bound
            #   返回结构化 dict: state / cancelled_algo_ids / foreign_algo_ids / halt_required
            #   foreign/unknown algo 不撤,halt_required=True 阻断同 symbol 新开仓
            cleanup_result = self._cleanup_protective_orders_on_close(symbol, position)
            cleanup_state = cleanup_result.get('state', 'unknown')

            # 真实成交 PnL：通过 ledger 查询 fetch_order 获取
            leverage = position.get('leverage', 1)
            if self.ledger:
                try:
                    ledger_event = self.ledger.record_close(
                        order_id=order['id'], symbol=symbol, side=position['side'],
                        entry_price=position['entry_price'],
                        amount_usdt=position['amount_usdt'], leverage=leverage,
                        estimated_price=exit_price, close_type="close"
                    )
                    pnl = ledger_event['realized_pnl']
                    exit_price = ledger_event['fill_price']
                except Exception as e:
                    self.logger.warning(f"[Ledger] 平仓记录失败（降级用CostModel）: {e}")
                    pnl = self._estimate_close_pnl_local(position, exit_price, leverage)
            else:
                pnl = self._estimate_close_pnl_local(position, exit_price, leverage)

            # 记录盈亏
            self.risk_manager.record_trade(pnl)

            result = {
                'symbol': symbol,
                'side': position['side'],
                'entry_price': position['entry_price'],
                'exit_price': exit_price,
                'leverage': leverage,
                'pnl': pnl,
                'pnl_pct': pnl / position['amount_usdt'] * 100,
                'attribution': position.get('attribution', {}),
                'entry_type': position.get('entry_type', 'unknown'),
                'entry_request_id': position.get('request_id', ''),
                'protective_cleanup_state': cleanup_state,
                'protective_cleanup': cleanup_result,
                'foreign_algo_ids': cleanup_result.get('foreign_algo_ids', []),
                'cleanup_warnings': cleanup_result.get('warnings', []),
            }

            # FR-3B: foreign/unknown algo 残留 → halt symbol 阻断新开仓
            if cleanup_result.get('halt_required') and self.exchange_id == 'okx' and not self.testnet:
                self._halt_symbol(symbol, reason='foreign_algos_present')
                result['halt_required'] = True

            # 删除持仓
            del self.positions[symbol]
            self._save_positions()
            # 冷却：防止sync在API延迟期间重新发现该持仓
            if not hasattr(self, '_close_cooldown'):
                self._close_cooldown = {}
            self._close_cooldown[symbol] = time.time() + 60
            # P1-M: 清理幂等窗口，允许立即反向开仓
            if self.idempotency:
                self.idempotency.clear(symbol, position['side'])

            self.logger.info(f"平仓成功: {symbol}, 盈亏: {pnl:.2f} USDT ({result['pnl_pct']:.2f}%)")
            return result

        except Exception as e:
            error_msg = str(e)
            # OKX 拒单复核：51169/51205/51112/51333 必须状态复核
            if self.exchange_id == 'okx' and _is_okx_position_reject(error_msg):
                review = self._handle_okx_close_reject(symbol, error_msg, action='close')
                self.logger.warning(f"[OKX close 拒单复核] {symbol} → {review['status']}")
                return None
            self.logger.error(f"平仓失败: {e}")
            return None
        finally:
            self._release_exit_lock(symbol, action_id)

    def _estimate_close_pnl_local(self, position: dict, exit_price: float, leverage: int) -> float:
        """CostModel 估算 PnL（ledger 不可用时的降级路径）"""
        try:
            from utils.cost_model import get_default_cost_model
            cm = get_default_cost_model()
            pnl_breakdown = cm.realized_pnl(
                side=position['side'],
                entry_price=position['entry_price'],
                exit_price=exit_price,
                amount_usdt=position['amount_usdt'],
                leverage=leverage,
                funding_rate=0,
                hold_hours=0,
            )
            return pnl_breakdown['net_pnl']
        except Exception:
            if position['side'] == 'long':
                pnl = (exit_price - position['entry_price']) / position['entry_price'] * position['amount_usdt'] * leverage
            else:
                pnl = (position['entry_price'] - exit_price) / position['entry_price'] * position['amount_usdt'] * leverage
            pnl -= position['amount_usdt'] * leverage * 0.002
            return pnl

    def check_stop_loss_take_profit(self, symbol: str) -> Optional[str]:
        """检查止损止盈 — 多源价格获取 + 连续失败强制平仓"""
        if symbol not in self.positions:
            self._sl_check_failures.pop(symbol, None)
            return None

        position = self.positions[symbol]
        if 'stop_loss' not in position or 'take_profit' not in position:
            return None

        current_price = self._fetch_price_robust(symbol)

        if current_price is None:
            count = self._sl_check_failures.get(symbol, 0) + 1
            self._sl_check_failures[symbol] = count
            self.logger.warning(
                f"止损检查: {symbol} 价格获取失败 (连续{count}次)"
            )
            if count >= self._sl_max_failures:
                self.logger.error(
                    f"止损检查: {symbol} 连续{count}次失败，强制平仓保护资金"
                )
                return 'price_fetch_failed'
            return None

        self._sl_check_failures[symbol] = 0

        return self._evaluate_local_exit_trigger(symbol, position, current_price)

    def _evaluate_local_exit_trigger(
        self,
        symbol: str,
        position: dict,
        current_price: float,
    ) -> Optional[str]:
        """Evaluate local TP/SL/trailing exits once a fresh price is known."""
        if position.get('strategy_owner') == 'tactical_v2':
            return None
        # 更新最高/最低价（用于trailing计算）
        if position['side'] == 'long':
            position['highest_price'] = max(position.get('highest_price', current_price), current_price)
        else:
            position['lowest_price'] = min(position.get('lowest_price', current_price), current_price)

        # 分批止盈 + 移动止损逻辑
        trailing_result = self._update_trailing(symbol, position, current_price)
        if trailing_result:
            return trailing_result

        # 常规SL/TP检查
        if position['side'] == 'long' and current_price <= position['stop_loss']:
            return 'stop_loss'
        if position['side'] == 'short' and current_price >= position['stop_loss']:
            return 'stop_loss'

        # legacy scalar take_profit 仅在无 take_profit_levels 时生效。
        # 有 levels 时 TP 由 _update_trailing() 的 partial_tp_1/2 + trailing 全权管理,
        # 否则 TP1 触发减仓后下一轮仍会因 position['take_profit']==TP1 命中此处而全平。
        # 详见 docs/partial_tp_lifecycle_prd.md FR-03。
        tp_levels = position.get('take_profit_levels') or []
        if not tp_levels:
            if position['side'] == 'long' and current_price >= position['take_profit']:
                return 'take_profit'
            if position['side'] == 'short' and current_price <= position['take_profit']:
                return 'take_profit'

        return None

    def _update_trailing(self, symbol: str, position: dict, price: float) -> Optional[str]:
        """分批止盈 + 移动止损（Break-Even + Trailing）

        返回 'partial_tp_1'/'partial_tp_2' 触发分批平仓，None 表示无动作
        SL更新直接修改position dict（棘轮，只向有利方向移动）
        """
        if position.get('strategy_owner') == 'tactical_v2':
            return None
        side = position['side']
        # Invariant: take_profit must mirror take_profit_levels[0]
        tp_levels_check = position.get('take_profit_levels') or []
        tp_scalar_check = position.get('take_profit')
        if tp_levels_check and tp_scalar_check is not None and tp_scalar_check != tp_levels_check[0]:
            self.logger.error(
                f"[TP Invariant] {symbol} breach: take_profit={tp_scalar_check} "
                f"!= take_profit_levels[0]={tp_levels_check[0]}; halting symbol"
            )
            self._halt_symbol(symbol, reason='tp_invariant_breach')
            self._enqueue_drift_alert(
                'tp_invariant_breach',
                symbol=symbol,
                take_profit=tp_scalar_check,
                take_profit_levels_first=tp_levels_check[0],
            )
            return None
        entry = position['entry_price']
        original_sl = position.get('original_sl', position['stop_loss'])
        R = abs(entry - original_sl) / entry  # 1R = 止损距离
        if R <= 0:
            return None

        tp_levels = position.get('take_profit_levels', [])
        tp_filled = position.get('tp_filled', 0)
        atr_pct = position.get('atr_pct', 0.02)
        is_long = (side == 'long')

        # 当前浮盈（以R为单位）
        if is_long:
            profit_r = (price - entry) / entry / R
        else:
            profit_r = (entry - price) / entry / R

        # --- Tactical exit profile: local TP/protection lifecycle, exchange SL remains authoritative ---
        if position.get('track') == 'tactical':
            cfg = getattr(self, '_config', {}) or {}
            now = time.time()
            thesis_state = str(position.get('tactical_thesis_state') or '').lower()

            if thesis_state == 'invalidated' or position.get('tactical_thesis_invalidated'):
                position['tactical_close_reason'] = 'tactical_invalidated'
                position['tactical_close_detail'] = (
                    position.get('tactical_thesis_reason')
                    or position.get('tactical_invalidation_reason')
                    or 'thesis_invalidated'
                )
                return 'tactical_invalidated'

            min_progress_r = float(cfg.get('tactical_min_progress_r', 0.15))
            if profit_r >= min_progress_r:
                position['tactical_last_progress_time'] = now
                position['tactical_best_profit_r'] = round(
                    max(float(position.get('tactical_best_profit_r', 0) or 0), profit_r), 4
                )

            if thesis_state == 'weakened' or position.get('tactical_thesis_weakened'):
                no_progress_minutes = (
                    position.get('tactical_weakened_no_progress_minutes')
                    or cfg.get('tactical_weakened_no_progress_min_minutes', 30)
                )
                last_progress = (
                    position.get('tactical_last_progress_time')
                    or position.get('open_time')
                    or now
                )
                if (profit_r < min_progress_r
                        and now - last_progress >= float(no_progress_minutes) * 60):
                    position['tactical_close_reason'] = 'tactical_weakened_no_progress'
                    position['tactical_close_detail'] = (
                        position.get('tactical_thesis_reason')
                        or f"profit_r={profit_r:.2f}<min_progress_r={min_progress_r:.2f}"
                    )
                    return 'tactical_weakened_no_progress'

            max_hold = (
                position.get('tactical_max_hold_minutes')
                or cfg.get('tactical_max_hold_minutes', 90)
            )
            if max_hold and now - position.get('open_time', now) >= max_hold * 60:
                position['tactical_close_reason'] = 'tactical_max_hold'
                return 'tactical_max_hold'

            if tp_filled == 0 and tp_levels:
                tp1 = tp_levels[0]
                if (is_long and price >= tp1) or (not is_long and price <= tp1):
                    position['tactical_close_reason'] = 'tactical_tp1'
                    self.logger.info(f"[Tactical] {symbol} TP1 命中 {tp1},等待 reduce 确认")
                    return 'tactical_tp1'
            if tp_filled == 1 and len(tp_levels) >= 2:
                tp2 = tp_levels[1]
                if (is_long and price >= tp2) or (not is_long and price <= tp2):
                    position['tactical_close_reason'] = 'tactical_tp2'
                    self.logger.info(f"[Tactical] {symbol} TP2 命中 {tp2},等待 reduce 确认")
                    return 'partial_tp_2'
            return None

        # --- Low RR 槽提前 trailing（不等 TP1）---
        if position.get('slot_type') == 'low_rr_extra' and tp_filled == 0:
            cfg = getattr(self, '_config', {}) or {}
            trail_start = cfg.get('low_rr_trail_start_r', 0.5)
            trail_dist = cfg.get('low_rr_trail_dist_r', 0.3)

            # TP1 仍保留作为触发条件
            if tp_levels:
                tp1 = tp_levels[0]
                if (is_long and price >= tp1) or (not is_long and price <= tp1):
                    self.logger.info(f"[Trailing] {symbol} TP1 命中 {tp1},等待 reduce 确认")
                    return 'partial_tp_1'

            if profit_r >= trail_start:
                trail_dist_abs = R * trail_dist * entry
                if is_long:
                    new_sl = position['highest_price'] - trail_dist_abs
                    if new_sl > position['stop_loss']:
                        self._move_sl(symbol, position, new_sl)
                else:
                    new_sl = position['lowest_price'] + trail_dist_abs
                    if new_sl < position['stop_loss']:
                        self._move_sl(symbol, position, new_sl)
            return None

        # --- 分批止盈 ---
        # 注意:本函数只返回触发信号,不再 mutate tp_filled / SL。
        # tp_filled 和锁利 SL 必须在 reduce_position() 真实成交确认后才推进,
        # 否则 reduce 失败会让本地停留在"已减仓"假象。
        # 详见 docs/partial_tp_lifecycle_prd.md FR-03/FR-04/FR-05。
        if tp_filled == 0 and tp_levels:
            tp1 = tp_levels[0]
            if (is_long and price >= tp1) or (not is_long and price <= tp1):
                self.logger.info(f"[Trailing] {symbol} TP1 命中 {tp1},等待 reduce 确认")
                return 'partial_tp_1'

        # TP2:价格到达tp_levels[1] → 再平25%
        if tp_filled == 1 and len(tp_levels) >= 2:
            tp2 = tp_levels[1]
            if (is_long and price >= tp2) or (not is_long and price <= tp2):
                self.logger.info(f"[Trailing] {symbol} TP2 命中 {tp2},等待 reduce 确认")
                return 'partial_tp_2'

        # --- RQ-05: 盈利保护梯度 ---
        fee_pct = 0.002  # 开平各0.1%

        # +0.8R: SL移到入场价+手续费缓冲（保本）
        if profit_r >= 0.8 and tp_filled == 0:
            be_sl = entry * (1 + fee_pct) if is_long else entry * (1 - fee_pct)
            current_sl = position['stop_loss']
            if (is_long and be_sl > current_sl) or (not is_long and be_sl < current_sl):
                self._move_sl(symbol, position, be_sl)
                self.logger.info(f"[ProfitProtect] {symbol} +0.8R 保本线激活，SL→{be_sl:.4f}")

        # +1.0R: SL移到 entry + 0.3R（锁定部分利润）
        if profit_r >= 1.0 and tp_filled == 0:
            lock_sl = entry * (1 + R * 0.3) if is_long else entry * (1 - R * 0.3)
            current_sl = position['stop_loss']
            if (is_long and lock_sl > current_sl) or (not is_long and lock_sl < current_sl):
                self._move_sl(symbol, position, lock_sl)
                self.logger.info(f"[ProfitProtect] {symbol} +1.0R 锁利，SL→{lock_sl:.4f}")

        # --- Trailing Stop（TP1触发后激活）---
        if tp_filled >= 1:
            trailing_dist = max(atr_pct, R * 0.5)
            if atr_pct > 0.03:  # 高波动标的放宽
                trailing_dist = max(atr_pct * 1.2, R * 0.7)

            if is_long:
                new_sl = position['highest_price'] * (1 - trailing_dist)
                if new_sl > position['stop_loss']:
                    self._move_sl(symbol, position, new_sl)
            else:
                new_sl = position['lowest_price'] * (1 + trailing_dist)
                if new_sl < position['stop_loss']:
                    self._move_sl(symbol, position, new_sl)

        return None

    def _move_sl(self, symbol: str, position: dict, new_sl: float):
        """更新SL：修改本地dict + 节流更新交易所条件单（变动>0.3%且间隔>30s）

        FR-04: 通过 _replace_protective_sl 单一入口同步交易所保护单,
        sl_algo_id / sl_algo_clord_id / protection_state 由该函数负责。
        若当前无交易所保护单(sl_algo_id 为空),不受节流限制,必须立即重挂。
        """
        old_sl = position['stop_loss']
        change_pct = abs(new_sl - old_sl) / old_sl if old_sl > 0 else 1.0
        position['stop_loss'] = new_sl

        now = time.time()
        last_update = self._last_sl_update.get(symbol, 0)
        no_protection = not (position.get('sl_algo_id') or position.get('sl_order_id'))
        throttle_ok = change_pct >= 0.003 and (now - last_update) >= 30
        if no_protection or throttle_ok:
            self._replace_protective_sl(symbol, position, new_sl)
            self._last_sl_update[symbol] = now
            self._save_positions()  # 持久化移动后的SL，防止重启丢失

    def _fetch_price_robust(self, symbol: str) -> Optional[float]:
        """多源价格获取：ticker → orderbook mid → 短暂重试"""
        # 方法1: fetch_ticker
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            if ticker and ticker.get('last'):
                return float(ticker['last'])
        except Exception:
            pass

        # 方法2: orderbook中间价
        try:
            ob = self.exchange.fetch_order_book(symbol, limit=5)
            if ob.get('asks') and ob.get('bids'):
                best_ask = ob['asks'][0][0]
                best_bid = ob['bids'][0][0]
                return (best_ask + best_bid) / 2
        except Exception:
            pass

        # 方法3: 等1秒重试ticker
        time.sleep(1)
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            if ticker and ticker.get('last'):
                return float(ticker['last'])
        except Exception:
            pass

        return None

    def _load_positions(self):
        """加载持仓记录"""
        if os.path.exists(self.positions_file):
            try:
                with open(self.positions_file, 'r') as f:
                    raw = json.load(f)
                # 过滤掉缺少止损/止盈字段的残缺持仓，避免重启后崩溃
                self.positions = {
                    k: v for k, v in raw.items()
                    if 'stop_loss' in v and 'take_profit' in v
                }
                skipped = len(raw) - len(self.positions)
                if skipped:
                    self.logger.warning(f"跳过{skipped}个残缺持仓记录（缺少止损/止盈）")
                self.logger.info(f"加载持仓记录: {len(self.positions)}个")
            except Exception as e:
                self.logger.warning(f"加载持仓失败: {e}")

    def _save_positions(self):
        """保存持仓记录（原子写入，进程崩溃不会损坏文件）"""
        try:
            from utils.atomic_io import atomic_write_json
            atomic_write_json(self.positions_file, self.positions)
        except Exception as e:
            self.logger.error(f"保存持仓失败: {e}")

    def _set_position_tp(self, position: dict, tp_first: float,
                         tp_levels: list) -> None:
        """Single sink for TP fields. Enforces:
           position['take_profit'] == position['take_profit_levels'][0]"""
        assert tp_levels, "tp_levels must be non-empty"
        assert tp_first == tp_levels[0], (
            f"tp_first {tp_first} must equal tp_levels[0] {tp_levels[0]}"
        )
        position['take_profit'] = tp_first
        position['take_profit_levels'] = list(tp_levels)

    def get_position(self, symbol: str) -> Optional[Dict]:
        """获取持仓信息"""
        return self.positions.get(symbol)

    def get_all_positions(self) -> Dict:
        """获取所有持仓"""
        return self.positions.copy()

    def _normalize_sidecar_take_profit(self, value) -> list[float]:
        if value in (None, "", [], {}):
            return []
        raw_levels = value if isinstance(value, (list, tuple)) else [value]
        levels = []
        for level in raw_levels:
            try:
                normalized = float(level)
            except (TypeError, ValueError):
                return []
            if not math.isfinite(normalized) or normalized <= 0:
                return []
            levels.append(normalized)
        return levels

    def _normalize_positive_float(self, value) -> Optional[float]:
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(normalized) or normalized <= 0:
            return None
        return normalized

    def _normalize_positive_float_list(self, value) -> list[float]:
        raw_values = value if isinstance(value, (list, tuple)) else [value]
        levels = []
        for raw in raw_values:
            normalized = self._normalize_positive_float(raw)
            if normalized is None:
                return []
            levels.append(normalized)
        return levels

    def _is_missing_sidecar_drift_anchor(self, value) -> bool:
        return value in (None, "", [], {})

    def _build_sidecar_drift_plan(self, plan: dict) -> Optional[dict]:
        entry_ref = self._normalize_positive_float(
            plan.get("entry_ref") or plan.get("entry_price")
        )
        stop_loss = self._normalize_positive_float(plan.get("stop_loss"))
        take_profit = self._normalize_sidecar_take_profit(plan.get("take_profit"))
        if not entry_ref or not stop_loss or not take_profit:
            return None

        raw_sl_pct = plan.get("sl_pct")
        if self._is_missing_sidecar_drift_anchor(raw_sl_pct):
            sl_pct = abs(entry_ref - stop_loss) / entry_ref
        else:
            sl_pct = self._normalize_positive_float(raw_sl_pct)
            if sl_pct is None:
                return None
        if not math.isfinite(sl_pct) or sl_pct <= 0:
            return None

        raw_tp_pct = plan.get("tp_pct")
        if self._is_missing_sidecar_drift_anchor(raw_tp_pct):
            tp_pct = [abs(take_profit[0] - entry_ref) / entry_ref]
        else:
            tp_pct = self._normalize_positive_float_list(raw_tp_pct)
            if not tp_pct:
                return None
        if any(not math.isfinite(level) or level <= 0 for level in tp_pct):
            return None

        return {
            "symbol": plan.get("symbol"),
            "side": plan.get("side"),
            "entry_ref": entry_ref,
            "sl_pct": sl_pct,
            "tp_pct": tp_pct,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "attribution": dict(plan.get("gate_metadata") or {}),
        }

    def _check_sidecar_entry_drift(self, plan: dict, live_price: float) -> tuple[bool, dict]:
        live_price = self._normalize_positive_float(live_price)
        if live_price is None:
            self._enqueue_drift_alert(
                "sidecar_entry_drift_missing_anchor",
                symbol=plan.get("symbol"),
                side=plan.get("side"),
                source="sidecar",
                shadow_id=plan.get("shadow_id"),
            )
            return False, {"decision": "missing_anchor"}
        drift_plan = self._build_sidecar_drift_plan(plan)
        if drift_plan is None:
            self._enqueue_drift_alert(
                "sidecar_entry_drift_missing_anchor",
                symbol=plan.get("symbol"),
                side=plan.get("side"),
                source="sidecar",
                shadow_id=plan.get("shadow_id"),
            )
            return False, {"decision": "missing_anchor"}

        decision = self._classify_entry_drift(drift_plan, live_price)
        reason = decision.reason
        if decision.decision == "recalc_pass":
            reason = "sidecar_recalc_required"
        metadata = {
            "band": decision.band,
            "drift_pct": decision.drift_pct,
            "decision": decision.decision,
            "reason": reason,
        }
        if decision.decision != "accept":
            self._enqueue_drift_alert(
                "sidecar_entry_drift_rejected",
                symbol=plan.get("symbol"),
                side=plan.get("side"),
                drift_pct=decision.drift_pct,
                decision=decision.decision,
                reason=reason,
                source="sidecar",
                shadow_id=plan.get("shadow_id"),
            )
            return False, metadata
        return True, metadata

    def open_sidecar_plan(self, plan: dict, *, size_usdt: Optional[float] = None) -> Optional[Dict]:
        """Open a Shadow Tactical sidecar plan with mechanical checks only."""
        from utils.shadow_tactical_live import canonical_sidecar_symbols

        canonical = canonical_sidecar_symbols(plan["symbol"])
        internal_symbol = plan.get("internal_symbol") or canonical["internal_symbol"]
        symbol = plan.get("exchange_symbol") or canonical["exchange_symbol"]
        side = plan["side"]
        if self.is_symbol_halted(symbol):
            self.logger.warning(f"[Sidecar] {symbol} halted, reject open")
            return None
        if self.exchange_id == "okx" and getattr(self, "_okx_pos_mode", None) not in (
            "net_mode",
            "long_short_mode",
        ):
            self.logger.error(f"[Sidecar] {symbol} OKX posMode unknown, reject open")
            return None

        balance = self.get_balance()
        can_trade, msg = self.risk_manager.check_can_trade(balance)
        if not can_trade:
            self.logger.warning(f"[Sidecar] risk reject: {msg}")
            return None

        leverage = int(plan.get("leverage") or self.leverage)
        requested_size = float(size_usdt or self.risk_manager.max_trade_amount)
        capped_size = min(requested_size, self.risk_manager.max_trade_amount)
        free_balance = (
            self.balance_adapter.get_free()
            if self.balance_adapter
            else self.exchange.fetch_balance()["USDT"]["free"]
        )
        if free_balance < capped_size * 1.1:
            self.logger.warning(
                f"[Sidecar] free balance too low: {free_balance:.2f} < {capped_size * 1.1:.2f}"
            )
            return None

        ticker = self.exchange.fetch_ticker(symbol)
        current_price = float(ticker["last"])
        drift_ok, drift_metadata = self._check_sidecar_entry_drift(plan, current_price)
        if not drift_ok:
            return None

        stop_loss = float(plan["stop_loss"])
        take_profit = self._normalize_sidecar_take_profit(plan.get("take_profit"))
        if side == "long" and stop_loss >= current_price:
            self.logger.error(f"[Sidecar] invalid long SL {stop_loss} >= {current_price}")
            return None
        if side == "short" and stop_loss <= current_price:
            self.logger.error(f"[Sidecar] invalid short SL {stop_loss} <= {current_price}")
            return None
        if not take_profit:
            self.logger.error("[Sidecar] missing take_profit")
            return None

        try:
            self.exchange.set_leverage(leverage, symbol)
        except Exception as e:
            self.logger.warning(f"[Sidecar] set leverage failed: {e}")

        if not self._check_slippage(symbol, capped_size, current_price):
            return None

        order_side = "buy" if side == "long" else "sell"
        if self.caps:
            ok, reason, _ = self.caps.precheck_order(
                symbol=symbol,
                side=order_side,
                size_usdt=capped_size,
                price=current_price,
                leverage=leverage,
            )
            if not ok:
                self.logger.warning(f"[Sidecar] precheck reject: {reason}")
                return None

        market = self.exchange.market(symbol)
        contract_size = float(market.get("contractSize", 1) or 1)
        amount = float(
            self.exchange.amount_to_precision(
                symbol,
                capped_size * leverage / (current_price * contract_size),
            )
        )
        min_amount = market.get("limits", {}).get("amount", {}).get("min", 0)
        if min_amount and amount < min_amount:
            self.logger.warning(
                f"[Sidecar] amount {amount:.8f} below exchange min {min_amount}"
            )
            return None

        sl_clord_id = (
            self._make_owner_tag_clord_id(symbol)
            if self.exchange_id == "okx" and stop_loss
            else None
        )
        tp_sl_params = self._build_tp_sl_params(
            side,
            stop_loss,
            take_profit[0],
            sl_clord_id=sl_clord_id,
        )
        attach_algo = self._build_attach_algo_from_tp_sl(tp_sl_params)
        params = self._build_open_order_params(
            side,
            clord_id=plan.get("entry_clord_id"),
            attach_algo=attach_algo,
        )
        order = self.exchange.create_order(
            symbol=symbol,
            type="market",
            side=order_side,
            amount=amount,
            params=params,
        )

        sl_algo_id = None
        sl_sync_state = "pending"
        protection_state = "unprotected"
        if self.exchange_id == "okx" and sl_clord_id:
            sl_algo_id = self._verify_attached_sl_after_fill(symbol, sl_clord_id)
            if not sl_algo_id:
                self._halt_symbol(symbol, reason="sidecar_sl_unverified")
                return None
            sl_sync_state = "active"
            protection_state = "protected"

        position = {
            "symbol": symbol,
            "internal_symbol": internal_symbol,
            "exchange_symbol": symbol,
            "side": side,
            "entry_price": current_price,
            "amount": amount,
            "amount_usdt": capped_size,
            "leverage": leverage,
            "stop_loss": stop_loss,
            "take_profit": take_profit[0],
            "take_profit_levels": take_profit,
            "sl_order_id": sl_algo_id,
            "exit_owner": "sidecar_tactical_exchange_sl",
            "sl_algo_id": sl_algo_id,
            "sl_algo_clord_id": sl_clord_id,
            "sl_sync_state": sl_sync_state,
            "protection_state": protection_state,
            "shadow_id": plan.get("shadow_id"),
            "sidecar_source": plan.get("sidecar_source", "shadow_tactical_live"),
            "track": plan.get("track", "tactical"),
            "exit_profile": plan.get("exit_profile", "tactical_v1"),
            "tactical_source": plan.get("tactical_source", ""),
            "tactical_max_hold_minutes": plan.get("tactical_max_hold_minutes"),
            "entry_ref": plan.get("entry_ref") or plan.get("entry_price"),
            "gate_metadata": {
                **dict(plan.get("gate_metadata") or {}),
                "entry_drift": drift_metadata,
            },
            "entry_order_id": order.get("id") if order else None,
            "entry_clord_id": plan.get("entry_clord_id"),
            "open_time": time.time(),
        }
        self.positions[symbol] = position
        self._save_positions()

        if self.ledger and order:
            try:
                self.ledger.record_open(
                    order_id=order["id"],
                    symbol=symbol,
                    side=side,
                    amount_usdt=capped_size,
                    leverage=leverage,
                    estimated_price=current_price,
                )
            except Exception as e:
                self.logger.warning(f"[Sidecar] ledger open record failed: {e}")

        return position

    def open_position_with_plan(self, symbol: str, side: str, plan: dict) -> Optional[Dict]:
        """基于Judge plan的智能开仓"""
        symbol = self._normalize_symbol(symbol)
        # OKX：posMode 未知禁止开新仓
        if self.exchange_id == 'okx' and not self.can_open_new_okx():
            self.logger.error(f"[OKX posMode] 未知，禁止智能开仓: {symbol}")
            return None
        if self.is_symbol_halted(symbol):
            self.logger.warning(f"[Halt] {symbol} 已 halt，拒绝智能开仓")
            return None
        # P1-M: 幂等防护——10s 内同 (symbol, side) 重复请求直接拒
        if self.idempotency:
            is_dup, prior = self.idempotency.is_duplicate(symbol, side)
            if is_dup:
                self.logger.warning(f"幂等拒绝(plan): {symbol} {side} 10s 内已有开单请求 (prior={prior})")
                return None
        try:
            balance = self.get_balance()
            can_trade, msg = self.risk_manager.check_can_trade(balance)
            if not can_trade:
                self.logger.warning(f"风控拒绝: {msg}")
                return None

            leverage = plan.get('leverage', self.leverage)
            size_usdt = plan.get('size_usdt', self.risk_manager.max_trade_amount)
            size_usdt = min(size_usdt, self.risk_manager.max_trade_amount)
            required_margin = size_usdt
            free_balance = self.balance_adapter.get_free() if self.balance_adapter else self.exchange.fetch_balance()['USDT']['free']
            if free_balance < required_margin * 1.1:
                self.logger.warning(f"可用余额不足: free={free_balance:.2f} < 需要{required_margin:.2f}")
                return None

            # P1-M: 通过风控检查后立即 mark 幂等窗口（即使后续下单失败，10s 内也不重试）
            clord_id = None
            if self.idempotency:
                clord_id = self.idempotency.gen_client_order_id(symbol, side)
                self.idempotency.mark(symbol, side, clord_id)

            order_type = plan.get('order_type', 'market')
            entry_zone = plan.get('entry_zone', {})
            stop_loss = plan.get('stop_loss')
            take_profit = plan.get('take_profit', [])

            try:
                self.exchange.set_leverage(leverage, symbol)
            except Exception as e:
                self.logger.warning(f"设置杠杆失败: {e}")

            ticker = self.exchange.fetch_ticker(symbol)
            current_price = ticker['last']

            # === Gate 1: Drift Classification ===
            import copy
            orig_plan_for_gate2 = copy.deepcopy(plan)
            drift_decision = self._classify_entry_drift(plan, current_price)
            self._record_drift_decision_event(symbol, side, drift_decision, gate='gate_1')

            if drift_decision.decision == 'abandon':
                self.logger.warning(
                    f"[Drift Gate 1] {symbol} abandon drift={drift_decision.drift_pct*100:.2f}%; "
                    f"plan.entry_ref={plan.get('entry_ref')} live={current_price}"
                )
                self._enqueue_drift_alert(
                    'entry_drift_abandoned', symbol=symbol, side=side,
                    drift_pct=drift_decision.drift_pct,
                    plan_entry_ref=plan.get('entry_ref'), live_price=current_price,
                    gate='gate_1',
                )
                return None
            if drift_decision.decision == 'recalc_fail':
                self.logger.warning(
                    f"[Drift Gate 1] {symbol} recalc_fail R:R={drift_decision.rr_actual} "
                    f"floor={drift_decision.rr_floor_used}"
                )
                self._enqueue_drift_alert(
                    'entry_drift_rr_fail', symbol=symbol, side=side,
                    drift_pct=drift_decision.drift_pct,
                    rr_actual=drift_decision.rr_actual,
                    rr_floor_used=drift_decision.rr_floor_used,
                    gate='gate_1',
                )
                return None
            if drift_decision.decision == 'recalc_pass':
                plan = drift_decision.new_plan
                current_price = drift_decision.new_plan['recomputed_entry']
                stop_loss = plan['stop_loss']
                take_profit = plan['take_profit']
                self.logger.info(
                    f"[Drift Gate 1] {symbol} {drift_decision.band} recalc_pass "
                    f"new_entry={current_price} new_SL={stop_loss} new_TP[0]={take_profit[0]}"
                )

            # 预计算止盈止损价格（开仓时一并提交）
            if not stop_loss:
                stop_loss = self.risk_manager.calculate_stop_loss(current_price, side)
            tp_first = take_profit[0] if take_profit else self.risk_manager.calculate_take_profit(current_price, side)

            # Invariant: drift gate guarantees SL on correct side. Breach = upstream bug.
            if side == 'short' and stop_loss is not None and stop_loss <= current_price:
                self.logger.error(
                    f"[SL Invariant] {symbol} short SL={stop_loss} <= entry={current_price}; halting"
                )
                self._halt_symbol(symbol, reason='sl_invariant_breach')
                self._enqueue_drift_alert(
                    'sl_invariant_breach', symbol=symbol, side=side,
                    stop_loss=stop_loss, entry=current_price,
                )
                return None
            elif side == 'long' and stop_loss is not None and stop_loss >= current_price:
                self.logger.error(
                    f"[SL Invariant] {symbol} long SL={stop_loss} >= entry={current_price}; halting"
                )
                self._halt_symbol(symbol, reason='sl_invariant_breach')
                self._enqueue_drift_alert(
                    'sl_invariant_breach', symbol=symbol, side=side,
                    stop_loss=stop_loss, entry=current_price,
                )
                return None

            # 构建附带TP/SL的下单参数
            sl_clord_id = self._make_owner_tag_clord_id(symbol) if self.exchange_id == 'okx' and stop_loss else None
            tp_sl_params = self._build_tp_sl_params(side, stop_loss, tp_first, sl_clord_id=sl_clord_id)

            if order_type == 'limit' and entry_zone:
                filled = self._execute_limit_order(
                    symbol, side, size_usdt, current_price, entry_zone, leverage,
                    tp_sl_params, clord_id, orig_plan=orig_plan_for_gate2,
                    timeout_sec=plan.get('limit_timeout_sec', 30),
                    no_fallback=plan.get('limit_no_fallback', False),
                )
                if filled is None:
                    return None
                amount, fill_price, limit_order_id = filled
                if self.ledger:
                    try:
                        ledger_event = self.ledger.record_open(
                            order_id=limit_order_id, symbol=symbol, side=side,
                            amount_usdt=size_usdt, leverage=leverage,
                            estimated_price=fill_price
                        )
                        fill_price = ledger_event['fill_price']
                    except Exception as e:
                        self.logger.warning(f"[Ledger] limit开仓记录失败: {e}")
            else:
                if not self._check_slippage(symbol, size_usdt, current_price):
                    self.logger.info(f"滑点过大，降级为限价单")
                    if entry_zone:
                        filled = self._execute_limit_order(
                            symbol, side, size_usdt, current_price, entry_zone, leverage,
                            tp_sl_params, clord_id, orig_plan=orig_plan_for_gate2,
                            timeout_sec=plan.get('limit_timeout_sec', 30),
                            no_fallback=plan.get('limit_no_fallback', False),
                        )
                        if filled is None:
                            return None
                        amount, fill_price, limit_order_id = filled
                        if self.ledger:
                            try:
                                ledger_event = self.ledger.record_open(
                                    order_id=limit_order_id, symbol=symbol, side=side,
                                    amount_usdt=size_usdt, leverage=leverage,
                                    estimated_price=fill_price
                                )
                                fill_price = ledger_event['fill_price']
                            except Exception as e:
                                self.logger.warning(f"[Ledger] limit降级开仓记录失败: {e}")
                    else:
                        return None
                else:
                    # P1-2: 订单参数预检
                    if self.caps:
                        ok, reason, _ = self.caps.precheck_order(
                            symbol=symbol, side='buy' if side == 'long' else 'sell',
                            size_usdt=size_usdt, price=current_price, leverage=leverage
                        )
                        if not ok:
                            self.logger.warning(f"[precheck] {symbol} plan开仓拒绝: {reason}")
                            return None

                    contract_value = size_usdt * leverage
                    market = self.exchange.market(symbol)
                    contract_size = float(market.get('contractSize', 1) or 1)
                    amount = float(self.exchange.amount_to_precision(
                        symbol, contract_value / (current_price * contract_size)
                    ))

                    min_amount = market.get('limits', {}).get('amount', {}).get('min', 0)
                    if min_amount and amount < min_amount:
                        self.logger.warning(f"订单数量{amount:.4f}低于最小值{min_amount}，放弃交易")
                        return None

                    order_side = 'buy' if side == 'long' else 'sell'
                    attach_algo = self._build_attach_algo_from_tp_sl(tp_sl_params)
                    params = self._build_open_order_params(
                        side, clord_id=clord_id, attach_algo=attach_algo,
                    )
                    plan_order = self.exchange.create_order(
                        symbol=symbol, type='market', side=order_side,
                        amount=amount, params=params
                    )
                    fill_price = current_price

                    # 真实成交价：通过 ledger 查询
                    if self.ledger and plan_order:
                        try:
                            ledger_event = self.ledger.record_open(
                                order_id=plan_order['id'], symbol=symbol, side=side,
                                amount_usdt=size_usdt, leverage=leverage,
                                estimated_price=current_price
                            )
                            fill_price = ledger_event['fill_price']
                        except Exception as e:
                            self.logger.warning(f"[Ledger] plan开仓记录失败（降级用ticker）: {e}")

            # 成交后用实际成交价修正止盈止损（如果偏差较大）
            if abs(fill_price - current_price) / current_price > 0.002:
                stop_loss = self.risk_manager.calculate_stop_loss(fill_price, side) if not plan.get('stop_loss') else stop_loss
                tp_first = take_profit[0] if take_profit else self.risk_manager.calculate_take_profit(fill_price, side)

            # FR-02: smart_open 走 attachAlgoOrds,成交后回查 algoId 并入位。
            sl_algo_id_resolved: Optional[str] = None
            sl_sync_state_resolved = 'pending'
            protection_state_resolved = 'unprotected'
            if self.exchange_id == 'okx' and sl_clord_id and stop_loss:
                try:
                    sl_algo_id_resolved = self._verify_attached_sl_after_fill(
                        symbol, sl_clord_id,
                    )
                except Exception as e:
                    self.logger.warning(f"[SL Resolve] {symbol} 解析异常: {e}")
                    sl_algo_id_resolved = None
                if sl_algo_id_resolved:
                    sl_sync_state_resolved = 'active'
                    protection_state_resolved = 'protected'
                else:
                    sl_sync_state_resolved = 'failed'
                    protection_state_resolved = 'unknown'
                    self.logger.error(
                        f"[SL Resolve] {symbol} attach SL algoId 未解析,"
                        f"clord_id={sl_clord_id}; 标记 protection_state=unknown"
                    )
                    if not self.testnet:
                        self._halt_symbol(symbol, reason='sl_algo_unresolved')

            position = {
                'symbol': symbol,
                'side': side,
                'entry_price': fill_price,
                'amount': amount,
                'amount_usdt': size_usdt,
                'leverage': leverage,
                'stop_loss': stop_loss,
                'take_profit': tp_first,
                'take_profit_levels': list(take_profit) if take_profit else [tp_first],
                'sl_order_id': sl_algo_id_resolved,
                # FR-02: 保护单生命周期字段。
                'exit_owner': 'local_partial_tp_exchange_sl',
                'sl_algo_id': sl_algo_id_resolved,
                'sl_algo_clord_id': sl_clord_id,
                'sl_sync_state': sl_sync_state_resolved,
                'protection_state': protection_state_resolved,
                'order_type': order_type,
                'original_sl': stop_loss,
                'highest_price': fill_price,
                'lowest_price': fill_price,
                'tp_filled': 0,
                'atr_pct': plan.get('atr_pct', 0.02),
                'original_amount': size_usdt,
                'entry_type': plan.get('entry_type', 'unknown'),
                'attribution': plan.get('attribution', {}),
                'slot_type': plan.get('slot_type', 'main'),
                'track': plan.get('track', 'main'),
                'exit_profile': plan.get('exit_profile', 'trend_runner'),
                'tactical_source': plan.get('tactical_source', ''),
                'tactical_max_hold_minutes': plan.get(
                    'tactical_max_hold_minutes',
                    plan.get('max_holding_minutes', 0),
                ),
                'tactical_close_reason': '',
                'open_time': time.time(),
                'request_id': plan.get('request_id', ''),
            }
            self._set_position_tp(position, position['take_profit'], position['take_profit_levels'])
            self.positions[symbol] = position
            self._save_positions()

            self.logger.info(
                f"智能开仓: {side} {symbol} @ {fill_price:.2f}, "
                f"杠杆={leverage}x, SL={stop_loss}, TP={tp_first}"
            )
            return position

        except Exception as e:
            self.logger.error(f"智能开仓失败: {e}")
            return None

    def _build_tp_sl_params(self, side: str, stop_loss: float, take_profit: float,
                              *, sl_clord_id: Optional[str] = None) -> dict:
        """开仓附带 TP/SL 的 attachAlgoOrds 包装。

        返回 `{'attachAlgoOrds': [...], 'sl_clord_id': ...}` 或 `{}`。OKX 与非 OKX
        共用,但仅 OKX 路径会真正把 attachAlgoOrds 加进 create_order 参数;
        reduceOnly/posSide 仍由 _build_open_order_params 决定。

        sl_clord_id 透传到 attach 字段,成交后由 _verify_attached_sl_after_fill()
        匹配并填充 position['sl_algo_id']。
        """
        attach = self._build_okx_attach_algo(stop_loss, take_profit, clord_id=sl_clord_id)
        if not attach:
            return {}
        out = {'attachAlgoOrds': attach}
        if sl_clord_id:
            out['sl_clord_id'] = sl_clord_id
        return out

    def _build_attach_algo_from_tp_sl(self, tp_sl_params: dict) -> Optional[list]:
        """从 _build_tp_sl_params 的输出还原 attachAlgoOrds 列表。"""
        if not tp_sl_params:
            return None
        return tp_sl_params.get('attachAlgoOrds')

    def _execute_limit_order(self, symbol: str, side: str, size_usdt: float,
                             current_price: float, entry_zone: dict,
                             leverage: int = 1, tp_sl_params: dict = None,
                             clord_id: str = None,
                             orig_plan: dict = None,
                             timeout_sec: int = 30,
                             no_fallback: bool = False) -> Optional[tuple]:
        """限价单执行，超时可选 cancel-and-give-up（pullback policy）或市价 fallback。"""
        import time

        # 获取实时价格，防止plan过期导致限价单超出交易所允许范围
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            live_price = ticker['last']
        except Exception:
            live_price = current_price

        if isinstance(entry_zone, list):
            low, high = entry_zone[0], entry_zone[1]
        else:
            low = entry_zone.get('low', current_price * 0.999)
            high = entry_zone.get('high', current_price * 1.001)
        limit_price = (low + high) / 2

        market = self.exchange.market(symbol)
        contract_size = float(market.get('contractSize', 1) or 1)
        amount = float(self.exchange.amount_to_precision(
            symbol, (size_usdt * leverage) / (limit_price * contract_size)
        ))
        order_side = 'buy' if side == 'long' else 'sell'

        if self.caps:
            ok, reason, norm = self.caps.precheck_order(
                symbol=symbol, side=order_side, size_usdt=size_usdt,
                price=limit_price, leverage=leverage
            )
            if not ok:
                self.logger.warning(f"限价单预检失败: {reason}")
                return None
            amount = norm.get('amount', amount)

        attach_algo = self._build_attach_algo_from_tp_sl(tp_sl_params)
        params = self._build_open_order_params(
            side, clord_id=clord_id, attach_algo=attach_algo,
        )

        order = self.exchange.create_order(
            symbol=symbol, type='limit', side=order_side,
            amount=amount, price=limit_price,
            params=params
        )
        order_id = order['id']
        self.logger.info(f"限价单挂出: {order_side} {amount:.6f} @ {limit_price:.2f}")

        deadline = time.time() + max(1, int(timeout_sec))
        while time.time() < deadline:
            time.sleep(3)
            try:
                status = self.exchange.fetch_order(order_id, symbol)
                if status['status'] == 'closed':
                    fill_price = status.get('average', limit_price)
                    filled_amount = status.get('filled', amount)
                    self.logger.info(f"限价单成交: {filled_amount:.6f} @ {fill_price:.2f}")
                    return (filled_amount, fill_price, order_id)
                elif status['status'] == 'canceled':
                    return None
            except Exception:
                pass

        try:
            self.exchange.cancel_order(order_id, symbol)
        except Exception:
            pass

        # 数据回测验收的 pullback 策略：未成交直接放弃，不做市价 fallback。
        if no_fallback:
            self.logger.info(
                f"[Pullback] {symbol} {side} 限价 {limit_price:.6g} 在 {timeout_sec}s 内未成交，放弃，不做市价fallback"
            )
            self._enqueue_drift_alert(
                'pullback_unfilled', symbol=symbol, side=side,
                limit_price=limit_price, timeout_sec=int(timeout_sec),
            )
            return None

        ticker = self.exchange.fetch_ticker(symbol)
        new_price = ticker['last']

        # === Gate 2: re-classify drift against ORIGINAL plan.entry_ref ===
        if orig_plan is not None:
            gate2 = self._classify_entry_drift(orig_plan, new_price)
            self._record_drift_decision_event(symbol, side, gate2, gate='gate_2')
            if gate2.decision == 'abandon':
                self.logger.warning(
                    f"[Drift Gate 2] {symbol} abandon drift={gate2.drift_pct*100:.2f}%"
                )
                self._enqueue_drift_alert(
                    'entry_drift_abandoned', symbol=symbol, side=side,
                    drift_pct=gate2.drift_pct, gate='gate_2',
                )
                return None
            if gate2.decision == 'recalc_fail':
                self._enqueue_drift_alert(
                    'entry_drift_rr_fail', symbol=symbol, side=side,
                    drift_pct=gate2.drift_pct, gate='gate_2',
                )
                return None
            if gate2.decision == 'recalc_pass':
                # Use recomputed SL/TP for the fallback market order's attach algo
                recomputed = gate2.new_plan
                sl_clord_existing = tp_sl_params.get('sl_clord_id') if tp_sl_params else None
                tp_sl_params = self._build_tp_sl_params(
                    side, recomputed['stop_loss'], recomputed['take_profit'][0],
                    sl_clord_id=sl_clord_existing,
                )

        amount = float(self.exchange.amount_to_precision(
            symbol, (size_usdt * leverage) / (new_price * contract_size)
        ))
        if self.caps:
            ok, reason, norm = self.caps.precheck_order(
                symbol=symbol, side=order_side, size_usdt=size_usdt,
                price=new_price, leverage=leverage
            )
            if not ok:
                self.logger.warning(f"限价单fallback预检失败: {reason}")
                return None
            amount = norm.get('amount', amount)
        fallback_attach = self._build_attach_algo_from_tp_sl(tp_sl_params)
        fallback_params = self._build_open_order_params(
            side, clord_id=clord_id, attach_algo=fallback_attach,
        )
        fallback_order = self.exchange.create_order(
            symbol=symbol, type='market', side=order_side,
            amount=amount, params=fallback_params
        )
        self.logger.info(f"限价单超时，市价成交: {amount:.6f} @ ~{new_price:.2f}")
        return (amount, new_price, fallback_order['id'])

    def _check_slippage(self, symbol: str, size_usdt: float, current_price: float) -> bool:
        """检查滑点：spread > 0.1% 或深度不足则返回False"""
        try:
            ob = self.exchange.fetch_order_book(symbol, limit=5)
            if not ob['asks'] or not ob['bids']:
                return False
            best_ask = ob['asks'][0][0]
            best_bid = ob['bids'][0][0]
            spread = (best_ask - best_bid) / best_bid
            if spread > 0.001:
                self.logger.warning(f"spread过大: {spread*100:.3f}%")
                return False
            ct_size = self._get_contract_size(symbol)
            depth_usdt = sum(p * q * ct_size for p, q in ob['asks'][:5])
            if depth_usdt < size_usdt * 3:
                self.logger.warning(f"深度不足: {depth_usdt:.0f} < {size_usdt*3:.0f}")
                return False
            return True
        except Exception:
            return True

    def _get_contract_size(self, symbol: str) -> float:
        """获取合约面值"""
        try:
            market = self.exchange.markets.get(symbol)
            if market:
                return float(market.get('contractSize', 1) or 1)
        except Exception:
            pass
        return 1.0

    def place_stop_loss_order(self, symbol: str, side: str, stop_price: float,
                              amount: float) -> Optional[str]:
        """挂交易所止损条件单。

        - OKX：走独立 algo（conditional + posSide），反向 side，不传 reduceOnly。
        - 其他：保留原 stop + reduceOnly=True 路径。
        """
        if self.exchange_id == 'okx':
            return self._place_protective_sl(
                symbol=symbol, side=side, stop_price=stop_price, amount=amount,
            )
        try:
            close_side = 'sell' if side == 'long' else 'buy'
            order = self.exchange.create_order(
                symbol=symbol,
                type='stop',
                side=close_side,
                amount=amount,
                price=stop_price,
                params={
                    'stopPrice': stop_price,
                    'reduceOnly': True,
                    'triggerPrice': stop_price,
                }
            )
            self.logger.info(f"止损条件单: {symbol} {close_side} @ {stop_price}")
            return order.get('id')
        except Exception as e:
            self.logger.warning(f"挂止损条件单失败（将用本地轮询兜底）: {e}")
            return None

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """撤单"""
        try:
            self.exchange.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            self.logger.warning(f"撤单失败: {e}")
            return False

    def _fetch_positions_with_retry(self):
        """fetch_positions() 的有界重试：吸收 OKX 瞬时网络/超时抖动。

        只重试 ccxt.NetworkError（含 RequestTimeout/ExchangeNotAvailable/DDoSProtection）；
        非瞬时异常（认证、参数等）直接抛出，由调用方按 ERROR 处理。
        本方法经 asyncio.to_thread 在线程内调用，time.sleep 不阻塞事件循环。
        """
        last_exc = None
        for attempt in range(1, _POS_SYNC_RETRY_ATTEMPTS + 1):
            try:
                return self.exchange.fetch_positions()
            except ccxt.NetworkError as e:
                last_exc = e
                if attempt < _POS_SYNC_RETRY_ATTEMPTS:
                    delay = _POS_SYNC_RETRY_BACKOFFS[min(attempt - 1, len(_POS_SYNC_RETRY_BACKOFFS) - 1)]
                    self.logger.warning(
                        f"[仓位同步] fetch_positions 第{attempt}/{_POS_SYNC_RETRY_ATTEMPTS}次失败"
                        f"({type(e).__name__})，{delay}s后重试"
                    )
                    time.sleep(delay)
                else:
                    self.logger.warning(
                        f"[仓位同步] fetch_positions 第{attempt}/{_POS_SYNC_RETRY_ATTEMPTS}次失败"
                        f"({type(e).__name__})，重试耗尽"
                    )
        raise last_exc

    def sync_positions(self) -> dict:
        """从交易所同步真实持仓，以交易所为准。返回新发现的持仓列表"""
        try:
            exchange_positions = self._fetch_positions_with_retry()
            active = {}
            for pos in exchange_positions:
                if pos['contracts'] and float(pos['contracts']) > 0:
                    # 统一转换为内部格式 LAYER/USDT:USDT → LAYER-USDT-SWAP
                    raw_sym = pos['symbol']
                    if '/' in raw_sym and ':' in raw_sym:
                        base = raw_sym.split('/')[0]
                        sym = f"{base}-USDT-SWAP"
                    else:
                        sym = raw_sym
                    side = 'long' if pos['side'] == 'long' else 'short'
                    lev = int(pos.get('leverage', 1)) or 1
                    notional = float(pos.get('notional', 0))
                    active[sym] = {
                        'symbol': sym,
                        'side': side,
                        'entry_price': float(pos.get('entryPrice', 0)),
                        'amount': float(pos['contracts']),
                        'amount_usdt': notional / lev,
                        'leverage': lev,
                        'unrealized_pnl': float(pos.get('unrealizedPnl', 0)),
                        'open_time': time.time(),
                    }

            removed_symbols = []
            if not hasattr(self, '_removed_positions_data'):
                self._removed_positions_data = []
            cooldown = getattr(self, '_close_cooldown', {})
            now_ts = time.time()
            for sym in list(self.positions.keys()):
                if sym not in active:
                    if sym in cooldown and now_ts < cooldown[sym]:
                        continue
                    pos_data = self.positions[sym].copy()
                    pos_data['symbol'] = sym
                    self._removed_positions_data.append(pos_data)
                    self.logger.info(f"仓位同步: {sym} 已不在交易所，移除本地记录")
                    removed_symbols.append(sym)
                    del self.positions[sym]
                    self._sl_check_failures.pop(sym, None)
                    halt_info = getattr(self, '_halted_symbols', {}).get(sym)
                    halt_reason = (halt_info or {}).get("reason", "")
                    if halt_reason:
                        self._maybe_auto_clear_protection_halt(
                            sym,
                            halt_reason,
                            source="self_heal:protection_resolved",
                        )
                    if hasattr(self, '_last_protection_alert'):
                        self._last_protection_alert.pop(sym, None)
            if not hasattr(self, '_last_removed_symbols'):
                self._last_removed_symbols = []
            self._last_removed_symbols.extend(removed_symbols)

            newly_synced = []
            cooldown = getattr(self, '_close_cooldown', {})
            now = time.time()
            if not hasattr(self, '_pending_resync'):
                self._pending_resync = {}
            confirm_ticks = (getattr(self, '_config', {}) or {}).get(
                'position_resync_confirm_ticks', 2)
            sidecar_owners = self._load_sidecar_owner_registry()
            for sym, ex_pos in active.items():
                # 第一道防线: 刚平仓的symbol在冷却期内不补录、不计双确认 tick
                # （防止API延迟导致幽灵持仓）
                if sym in cooldown and now < cooldown[sym]:
                    continue
                if sym in self.positions:
                    local = self.positions[sym]
                    if abs(local['amount'] - ex_pos['amount']) / max(ex_pos['amount'], 1e-8) > 0.01:
                        self.logger.info(f"仓位同步: {sym} 数量不一致，以交易所为准")
                        local['amount'] = ex_pos['amount']
                        local['amount_usdt'] = ex_pos['amount_usdt']
                    local['unrealized_pnl'] = ex_pos['unrealized_pnl']
                else:
                    if sidecar_owners and sidecar_owners.matches_position(sym, ex_pos['side']):
                        self._pending_resync.pop(sym, None)
                        self.logger.info(f"仓位同步: {sym} ignored as sidecar-owned")
                        continue
                    # 双确认: 本地缺失、交易所新出现的持仓必须连续 confirm_ticks
                    # 个 sync tick 都见到才补录（防交易所平仓后上报延迟产生幽灵持仓）。
                    cnt = self._pending_resync.get(sym, 0) + 1
                    if cnt < confirm_ticks:
                        self._pending_resync[sym] = cnt
                        self.logger.info(
                            f"仓位同步: {sym} 待确认 ({cnt}/{confirm_ticks} tick)，暂不补录"
                        )
                        continue                              # 等下个 tick 确认
                    self._pending_resync.pop(sym, None)
                    entry = ex_pos['entry_price']
                    if ex_pos['side'] == 'long':
                        ex_pos['stop_loss'] = entry * 0.97
                        ex_pos['take_profit'] = entry * 1.03
                    else:
                        ex_pos['stop_loss'] = entry * 1.03
                        ex_pos['take_profit'] = entry * 0.97
                    ex_pos['take_profit_levels'] = [ex_pos['take_profit']]
                    ex_pos['sl_order_id'] = None
                    # FR-02: 同步补录的仓位 protection_state 未知,需后续 reconcile
                    # 才能归属交易所 algo;在归属之前阻止 partial TP / 加仓。
                    ex_pos.setdefault('exit_owner', 'local_partial_tp_exchange_sl')
                    ex_pos.setdefault('sl_algo_id', None)
                    ex_pos.setdefault('sl_algo_clord_id', None)
                    ex_pos.setdefault('sl_sync_state', 'unknown')
                    ex_pos.setdefault('protection_state', 'unprotected')
                    ex_pos['order_type'] = 'market'
                    self.logger.info(f"仓位同步: 发现交易所持仓 {sym}，补录本地 (SL={ex_pos['stop_loss']:.6f} TP={ex_pos['take_profit']:.6f})")
                    self.positions[sym] = ex_pos
                    newly_synced.append(ex_pos)

            # 扫尾清幽灵: 本 tick 交易所已不再上报的 pending 候选清掉计数,
            # 防止跨多次 sync 的非连续上报错误累积到补录阈值。
            for sym in list(self._pending_resync):
                if sym not in active:
                    self._pending_resync.pop(sym, None)

            self._save_positions()
            self._last_sync_result = newly_synced

            # FR-07: 启动期/sync 完成后扫描存量 OKX algo 并迁移到 single-owner
            if self.exchange_id == 'okx':
                try:
                    self._migrate_all_symbols_algos()
                except Exception as e:
                    self.logger.warning(f"[Migrate] sync 后 algo 迁移失败: {e}")

            return self.positions.copy()

        except Exception as e:
            self.logger.error(f"仓位同步失败: {type(e).__name__}: {e}")
            self._last_sync_result = []
            return self.positions.copy()

    def get_newly_synced(self) -> list:
        """获取上次sync_positions发现的新持仓（供agent层发布通知）"""
        result = getattr(self, '_last_sync_result', [])
        self._last_sync_result = []
        return result

    def get_removed_symbols(self) -> list:
        """获取上次sync_positions发现的已被交易所平仓的标的"""
        result = getattr(self, '_last_removed_symbols', [])
        self._last_removed_symbols = []
        return result

    def get_removed_positions_data(self) -> list:
        """获取被移除持仓的完整数据（含entry_price/side/stop_loss，用于计算PnL）"""
        result = getattr(self, '_removed_positions_data', [])
        self._removed_positions_data = []
        return result

    def reduce_position(self, symbol: str, pct: float,
                        tp_advance: Optional[int] = None,
                        action_id: Optional[str] = None,
                        action_kind: Optional[str] = None) -> Optional[Dict]:
        """减仓指定百分比

        参考：Binance/OKX reduceOnly模式 + Freqtrade partial exit
        减仓后取消旧SL条件单（数量不匹配会导致交易所拒绝）

        tp_advance:
            None  - 普通减仓(RiskGuard / position analyst),不动 tp_filled。
            1 / 2 - partial TP 路径,reduce 真实成交后才把 tp_filled 推进到该值并
                    锁利 SL。详见 docs/partial_tp_lifecycle_prd.md FR-03/FR-05。

        action_id / action_kind: FR-06 exit lock 串行化标识。同 symbol 同时
        到达 partial_tp + risk_alert + local_stop 时,只允许第一个进入,其他
        返回 None 并打 exit_locked。
        """
        if symbol not in self.positions:
            return None
        if self.positions[symbol].get('strategy_owner') == 'tactical_v2':
            self.logger.warning(
                f"[OwnerIsolation] {symbol} Tactical V2 拒绝 generic reduce_position"
            )
            return None

        if action_kind is None:
            action_kind = f'partial_tp_{tp_advance}' if tp_advance else 'reduce'
        if action_id is None:
            action_id = f"{action_kind}-{symbol}-{uuid.uuid4().hex[:8]}"
        acquire, holder = self._try_acquire_exit_lock(symbol, action_kind, action_id)
        if acquire == 'locked':
            self.logger.warning(
                f"[ExitLock] {symbol} {action_kind} 被 {holder.get('kind')} "
                f"({holder.get('action_id')}) 占用,exit_locked 拒绝"
            )
            return None
        if acquire == 'reentrant':
            self.logger.info(f"[ExitLock] {symbol} {action_kind} 重入,幂等返回")
            return None

        try:
            position = self.positions[symbol]
            old_sl_algo_id = position.get('sl_algo_id') or position.get('sl_order_id')
            old_sl_clord_id = position.get('sl_algo_clord_id')
            old_amount = position.get('amount', 0)
            old_amount_usdt = position.get('amount_usdt', 0)
            old_stop_loss = position.get('stop_loss')
            entry_request_id = position.get('request_id', '')

            # 结构化结果(FR-3A)。任一失败路径都通过本 dict 返回,调用方据此停推 tp_filled、
            # 阻断后续 add/open/reduce、live OKX halt。
            result: Dict[str, Any] = {
                'ok': False,
                'symbol': symbol,
                'operation': 'reduce_position',
                'action_id': action_id,
                'action_kind': action_kind,
                'requested_pct': pct,
                'requested_reduce_amount': 0.0,
                'actual_reduce_amount': 0.0,
                'order': None,
                'reduce_ok': False,
                'cancel_ok': False,
                'replace_ok': False,
                'protective_update_state': 'unknown',
                'protection_state': position.get('protection_state', 'unknown'),
                'old_sl_algo_id': old_sl_algo_id,
                'old_sl_algo_clord_id': old_sl_clord_id,
                'new_sl_algo_id': None,
                'sl_sync_state': position.get('sl_sync_state', 'unknown'),
                'reduced_pct': pct,
                'realized_pnl': 0.0,
                'pnl': 0.0,
                'halt_required': False,
                'reason': '',
                'warnings': [],
                'entry_request_id': entry_request_id,
                'timestamp': time.time(),
            }

            reduce_amount = old_amount * pct
            try:
                reduce_amount = float(self.exchange.amount_to_precision(symbol, reduce_amount))
            except Exception:
                pass
            result['requested_reduce_amount'] = reduce_amount

            if reduce_amount <= 0:
                result['reason'] = 'reduce_amount_zero'
                return result

            if self.exchange_id == 'okx':
                ex_pos = self._fetch_okx_position_state(symbol)
                if ex_pos is None:
                    self._mark_external_closed(symbol, reason='reduce_already_flat')
                    result['reason'] = 'already_flat'
                    return result
                if ex_pos['side'] != position['side']:
                    self.logger.error(
                        f"[OKX reduce] {symbol} 方向冲突: 本地{position['side']} vs 交易所{ex_pos['side']}, 暂停"
                    )
                    self._halt_symbol(symbol, reason='direction_conflict_reduce')
                    result['reason'] = 'direction_conflict'
                    result['halt_required'] = True
                    return result
                if reduce_amount > ex_pos['available_contracts']:
                    self.logger.warning(
                        f"[OKX reduce] {symbol} 数量超出可平 {reduce_amount} > {ex_pos['available_contracts']}, 收敛"
                    )
                    reduce_amount = ex_pos['available_contracts']
                    try:
                        reduce_amount = float(self.exchange.amount_to_precision(symbol, reduce_amount))
                    except Exception:
                        pass
                    if reduce_amount <= 0:
                        result['reason'] = 'reduce_amount_zero'
                        return result
                    result['requested_reduce_amount'] = reduce_amount

            # FR-3A: 撤旧 SL 失败 → 立即返回,不发 reduce order,不清旧 ID。
            had_old_sl = bool(old_sl_algo_id)
            cancel_ok = True
            if had_old_sl:
                cancel_ok = self._cancel_protective_sl(symbol, position)
            result['cancel_ok'] = cancel_ok if had_old_sl else True
            if had_old_sl and not cancel_ok:
                position['sl_sync_state'] = 'failed'
                position['protection_state'] = 'unknown'
                position['last_protection_error'] = 'sl_cancel_failed'
                result['protective_update_state'] = 'cancel_failed'
                result['protection_state'] = 'unknown'
                result['sl_sync_state'] = 'failed'
                result['reason'] = 'sl_cancel_failed'
                result['warnings'].append('old_sl_may_still_be_live')
                self.logger.error(
                    f"[Reduce] {symbol} 撤旧 SL {old_sl_algo_id} 失败,放弃缩仓; "
                    f"old_sl_algo_id 保留,protection_state=unknown"
                )
                if self.exchange_id == 'okx' and not self.testnet:
                    self._halt_symbol(symbol, reason='sl_cancel_failed')
                    result['halt_required'] = True
                self._save_positions()
                return result
            if had_old_sl and cancel_ok:
                # 撤旧成功才清空本地 ID,后面挂新 SL 时再回填
                position['sl_order_id'] = None
                position['sl_algo_id'] = None
                position['sl_algo_clord_id'] = None
                position['sl_sync_state'] = 'pending'

            order_side = 'sell' if position['side'] == 'long' else 'buy'
            reduce_params = self._build_close_order_params(position)
            try:
                order = self.exchange.create_order(
                    symbol=symbol, type='market', side=order_side,
                    amount=reduce_amount, params=reduce_params,
                )
            except Exception as ce:
                error_msg = str(ce)
                if self.exchange_id == 'okx' and _is_okx_position_reject(error_msg):
                    review = self._handle_okx_close_reject(symbol, error_msg, action='reduce')
                    self.logger.warning(f"[OKX reduce 拒单复核] {symbol} → {review['status']}")
                else:
                    self.logger.error(f"减仓下单失败: {ce}")
                # FR-3A: 撤旧成功但 reduce reject → 尝试 restore 原 SL,失败则 halt
                restore_ok = False
                if had_old_sl and old_stop_loss:
                    restore_ok = self._replace_protective_sl(
                        symbol, position, old_stop_loss
                    )
                result['reduce_ok'] = False
                result['order'] = None
                result['reason'] = 'reduce_rejected'
                if restore_ok:
                    result['protective_update_state'] = 'restored_old_sl'
                    result['protection_state'] = position.get('protection_state', 'unknown')
                    result['sl_sync_state'] = position.get('sl_sync_state', 'unknown')
                    result['new_sl_algo_id'] = position.get('sl_algo_id')
                    result['warnings'].append('reduce_rejected_old_sl_restored')
                else:
                    if had_old_sl:
                        position['protection_state'] = 'unknown'
                        position['sl_sync_state'] = 'failed'
                        position['last_protection_error'] = 'sl_restore_failed'
                        result['protective_update_state'] = 'restore_failed'
                        result['protection_state'] = 'unknown'
                        result['sl_sync_state'] = 'failed'
                        result['warnings'].append('reduce_rejected_protection_lost')
                        if self.exchange_id == 'okx' and not self.testnet:
                            self._halt_symbol(symbol, reason='sl_restore_failed')
                            result['halt_required'] = True
                    else:
                        result['protective_update_state'] = 'no_op'
                self._save_positions()
                return result

            result['order'] = order
            result['reduce_ok'] = True
            result['actual_reduce_amount'] = reduce_amount

            # 真实成交 PnL：通过 ledger 记录减仓
            reduce_usdt = old_amount_usdt * pct
            realized_pnl = 0.0
            if self.ledger:
                try:
                    ledger_event = self.ledger.record_reduce(
                        order_id=order['id'], symbol=symbol, side=position['side'],
                        entry_price=position['entry_price'],
                        reduce_usdt=reduce_usdt,
                        leverage=position.get('leverage', self.leverage),
                        estimated_price=self.exchange.fetch_ticker(symbol)['last']
                    )
                    realized_pnl = ledger_event['realized_pnl']
                except Exception as e:
                    self.logger.warning(f"[Ledger] 减仓记录失败: {e}")

            if realized_pnl != 0:
                self.risk_manager.record_trade(realized_pnl)
            result['realized_pnl'] = realized_pnl
            result['pnl'] = realized_pnl

            position['amount'] = old_amount - reduce_amount
            position['amount_usdt'] = old_amount_usdt * (1 - pct)

            min_amount = self.exchange.market(symbol).get('limits', {}).get('amount', {}).get('min', 1e-8)
            position_dust_closed = False
            if position['amount'] < max(min_amount, 1e-8):
                del self.positions[symbol]
                self.logger.info(f"减仓后剩余量过小，视为全平: {symbol}")
                position_dust_closed = True
                result['protective_update_state'] = 'dust_closed'
                result['protection_state'] = 'closed'

            replace_ok = True
            if not position_dust_closed:
                # FR-3A: 任意 reduce 成功后,只要剩余仓位存在,必须重挂 residual SL。
                # tp_advance 走锁利 SL,普通 reduce 走原 stop_loss。
                if tp_advance is not None:
                    entry = position['entry_price']
                    original_sl = position.get('original_sl', position.get('stop_loss'))
                    R = abs(entry - original_sl) / entry if entry and original_sl else 0.0
                    is_long = (position['side'] == 'long')
                    if tp_advance == 1:
                        new_sl = entry * (1 + R * 0.5) if is_long else entry * (1 - R * 0.5)
                    elif tp_advance == 2:
                        new_sl = entry * (1 + R * 1.5) if is_long else entry * (1 - R * 1.5)
                    else:
                        new_sl = None
                    position['tp_filled'] = tp_advance
                    if new_sl is not None and R > 0:
                        replace_ok = self._replace_protective_sl(symbol, position, new_sl)
                        if replace_ok:
                            position['stop_loss'] = new_sl
                else:
                    remaining_sl = position.get('stop_loss')
                    if remaining_sl:
                        replace_ok = self._replace_protective_sl(symbol, position, remaining_sl)
                    else:
                        replace_ok = False
                        result['warnings'].append('residual_no_stop_loss_target')

                if replace_ok:
                    result['protective_update_state'] = 'protected'
                    result['protection_state'] = position.get('protection_state', 'protected')
                    result['sl_sync_state'] = position.get('sl_sync_state', 'active')
                    result['new_sl_algo_id'] = position.get('sl_algo_id')
                    if tp_advance is not None:
                        position['partial_tp_state'] = 'protected'
                else:
                    # reduce 已成交,但 residual 保护单挂不上 → 阻断后续 add/open/reduce
                    position['protection_state'] = 'unknown'
                    position['sl_sync_state'] = 'failed'
                    position['last_protection_error'] = 'sl_replace_failed_after_reduce'
                    if tp_advance is not None:
                        position['partial_tp_state'] = 'protection_failed'
                    result['protective_update_state'] = 'replace_failed'
                    result['protection_state'] = 'unknown'
                    result['sl_sync_state'] = 'failed'
                    result['warnings'].append('residual_protection_failed')
                    self.logger.error(
                        f"[Reduce] {symbol} reduce 成功但 residual SL 重挂失败,"
                        f"protection_state=unknown,阻断后续 add/open/reduce"
                    )
                    if self.exchange_id == 'okx' and not self.testnet:
                        self._halt_symbol(symbol, reason='sl_replace_failed')
                        result['halt_required'] = True

            result['replace_ok'] = replace_ok
            result['ok'] = result['reduce_ok'] and (replace_ok or position_dust_closed)
            self._save_positions()

            self.logger.info(
                f"减仓: {symbol} 减{pct*100:.0f}%, 剩余{position.get('amount', 0):.6f}, "
                f"PnL={realized_pnl:+.4f}, protective={result['protective_update_state']}"
            )
            return result

        except Exception as e:
            self.logger.error(f"减仓失败: {e}")
            return None
        finally:
            self._release_exit_lock(symbol, action_id)

    def add_to_position(self, symbol: str, side: str, size_pct: float = 0.3) -> Optional[Dict]:
        """加仓：在已有持仓方向上追加仓位

        Args:
            symbol: 标的
            side: 方向（必须与现有持仓一致）
            size_pct: 加仓比例（相对于max_trade_amount）

        参考：Freqtrade adjust_trade_position + stoploss_on_exchange_update
        加仓后重新计算SL/TP以反映新均价
        """
        symbol = self._normalize_symbol(symbol)
        if self.exchange_id == 'okx' and not self.can_open_new_okx():
            self.logger.error(f"[OKX posMode] 未知，禁止加仓: {symbol}")
            return None
        if self.is_symbol_halted(symbol):
            self.logger.warning(f"[Halt] {symbol} 已 halt，拒绝加仓")
            return None
        if symbol not in self.positions:
            self.logger.warning(f"加仓失败: {symbol} 无持仓")
            return None

        position = self.positions[symbol]
        if position.get('strategy_owner') == 'tactical_v2':
            self.logger.warning(
                f"[OwnerIsolation] {symbol} Tactical V2 拒绝 generic add_to_position"
            )
            return None
        # FR-05: 保护单未就绪时禁止加仓
        prot_state = position.get('protection_state')
        if prot_state and prot_state not in ('protected',):
            self.logger.warning(
                f"[FR-05] {symbol} protection_state={prot_state},拒绝加仓"
            )
            return None
        if position['side'] != side:
            self.logger.warning(f"加仓方向冲突: 持仓{position['side']} vs 请求{side}")
            return None

        # 加仓上限：总保证金不超过max_trade_amount的2倍
        current_margin = position.get('amount_usdt', 0)
        max_total = self.risk_manager.max_trade_amount * 2
        if current_margin >= max_total:
            self.logger.warning(f"加仓拒绝: 已达保证金上限 {current_margin:.2f} >= {max_total:.2f}")
            return None

        try:
            balance = self.get_balance()
            can_trade, msg = self.risk_manager.check_can_trade(balance)
            if not can_trade:
                self.logger.warning(f"加仓风控拒绝: {msg}")
                return None

            add_usdt = self.risk_manager.max_trade_amount * size_pct
            # 不超过上限
            add_usdt = min(add_usdt, max_total - current_margin)
            if add_usdt < 1.0:
                self.logger.warning(f"加仓金额过小: {add_usdt:.2f} USDT，放弃")
                return None

            free_balance = self.balance_adapter.get_free() if self.balance_adapter else self.exchange.fetch_balance()['USDT']['free']
            if free_balance < add_usdt * 1.1:
                self.logger.warning(f"加仓余额不足: free={free_balance:.2f} < 需要{add_usdt:.2f}")
                return None

            leverage = position.get('leverage', self.leverage)
            try:
                self.exchange.set_leverage(leverage, symbol)
            except Exception:
                pass

            ticker = self.exchange.fetch_ticker(symbol)
            current_price = ticker['last']

            # P1-2: 订单参数预检
            if self.caps:
                ok, reason, _ = self.caps.precheck_order(
                    symbol=symbol, side='buy' if side == 'long' else 'sell',
                    size_usdt=add_usdt, price=current_price, leverage=leverage
                )
                if not ok:
                    self.logger.warning(f"[precheck] {symbol} 加仓拒绝: {reason}")
                    return None

            contract_value = add_usdt * leverage
            market = self.exchange.market(symbol)
            contract_size = float(market.get('contractSize', 1) or 1)
            amount = float(self.exchange.amount_to_precision(
                symbol, contract_value / (current_price * contract_size)
            ))

            min_amount = market.get('limits', {}).get('amount', {}).get('min', 0)
            if min_amount and amount < min_amount:
                self.logger.warning(f"加仓数量{amount:.4f}低于最小值{min_amount}，放弃")
                return None

            order_side = 'buy' if side == 'long' else 'sell'
            add_clord = None
            if self.idempotency and self.exchange_id == 'okx':
                add_clord = self.idempotency.gen_client_order_id(symbol, f'add_{side}')
            add_params = self._build_open_order_params(side, clord_id=add_clord)
            order = self.exchange.create_order(
                symbol=symbol, type='market', side=order_side,
                amount=amount, params=add_params
            )

            # P1-4: 加仓 ledger 记录
            if self.ledger and order:
                try:
                    self.ledger.record_add(
                        order_id=order['id'], symbol=symbol, side=side,
                        amount_usdt=add_usdt, leverage=leverage,
                        estimated_price=current_price
                    )
                except Exception as e:
                    self.logger.warning(f"[Ledger] 加仓记录失败: {e}")

            # 更新持仓记录：加权平均入场价
            old_amount = position['amount']
            old_entry = position['entry_price']
            new_total = old_amount + amount
            position['entry_price'] = (old_entry * old_amount + current_price * amount) / new_total
            position['amount'] = new_total
            position['amount_usdt'] = current_margin + add_usdt

            # 加仓后基于新均价重算SL/TP（Freqtrade stoploss_on_exchange_update模式）
            new_entry = position['entry_price']
            old_sl = position.get('stop_loss')
            old_tp = position.get('take_profit')
            if old_sl and old_entry > 0:
                # 保持原SL距离比例
                sl_dist_pct = abs(old_sl - old_entry) / old_entry
                if side == 'long':
                    position['stop_loss'] = new_entry * (1 - sl_dist_pct)
                else:
                    position['stop_loss'] = new_entry * (1 + sl_dist_pct)
                position['original_sl'] = position['stop_loss']  # 重置1R基准
            if old_tp and old_entry > 0:
                # P1-01: 按每个 level 各自距旧均价比例平移整个 take_profit_levels，
                # 经 _set_position_tp 单一收口写入，保证 take_profit==levels[0] 不变量
                # 在加仓后保持（否则下一轮 _update_trailing 会误判 tp_invariant_breach
                # 并触发全局熔断）。take_profit_levels 不收缩、tp_filled 不动。
                old_levels = position.get('take_profit_levels') or [old_tp]
                new_levels = []
                for lvl in old_levels:
                    dist = abs(lvl - old_entry) / old_entry
                    new_levels.append(new_entry * (1 + dist) if side == 'long'
                                      else new_entry * (1 - dist))
                self._set_position_tp(position, new_levels[0], new_levels)

            # 加仓后更新交易所SL条件单（旧单数量/价格不匹配）
            new_sl = position.get('stop_loss')
            if new_sl:
                self._replace_protective_sl(symbol, position, new_sl)

            self._save_positions()

            self.logger.info(
                f"加仓成功: {side} {symbol} +{amount:.4f} @ {current_price:.4f}, "
                f"均价={position['entry_price']:.4f}, 总量={new_total:.4f}, "
                f"新SL={position.get('stop_loss')}, 新TP={position.get('take_profit')}"
            )
            return {
                'symbol': symbol,
                'side': side,
                'add_amount': amount,
                'add_amount_usdt': add_usdt,
                'amount_usdt': position['amount_usdt'],  # 累加后的新总保证金（统一风险预算语义）
                'fill_price': current_price,
                'new_entry_price': position['entry_price'],
                'new_total_amount': new_total,
                'new_stop_loss': position.get('stop_loss'),
                'new_take_profit': position.get('take_profit'),
                'order': order,
            }

        except Exception as e:
            self.logger.error(f"加仓失败: {e}")
            return None
