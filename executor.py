#!/usr/bin/env python3
"""合约执行器 - 基于ccxt的统一接口"""

import ccxt
import json
import os
import threading
import time
import uuid
from typing import Dict, Optional
from risk_manager import RiskManager
from utils.logger import setup_logger


# OKX 拒单错误码：与持仓状态相关，必须做交易所状态复核
OKX_POSITION_REJECT_CODES = ('51169', '51205', '51112', '51333')


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
                 positions_file: str = 'data/positions.json'):
        """
        Args:
            exchange_id: 交易所ID (binance/okx)
            api_key: API密钥
            secret: API密钥
            password: API密码（OKX需要）
            testnet: 是否使用测试网
            leverage: 杠杆倍数（默认1倍，不使用杠杆）
            positions_file: 持仓持久化文件路径
        """
        self.logger = setup_logger('executor')
        self.exchange_id = exchange_id
        self.testnet = testnet
        self.leverage = leverage
        self.positions_file = positions_file

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
        except Exception as e:
            self.logger.warning(f"config_loader 加载失败，使用 env 兜底: {e}")
            max_amount = float(os.getenv('MAX_TRADE_AMOUNT', 10))
            max_dd = float(os.getenv('MAX_DRAWDOWN_PCT', 20))
            max_daily = abs(float(os.getenv('MAX_DAILY_LOSS', 50)))
            _cap = float(os.getenv('EFFECTIVE_BALANCE_CAP', 0)) or None
            _baseline_mode = os.getenv('DRAWDOWN_BASELINE_MODE', 'session_start')
        self.risk_manager = RiskManager(
            max_trade_amount=max_amount,
            max_drawdown_pct=max_dd,
            max_daily_loss=max_daily,
            state_file='data/risk_state.json',
            effective_balance_cap=_cap,
            baseline_mode=_baseline_mode,
        )

        # 持仓记录
        self.positions = {}
        self._load_positions()

        # 止损检查连续失败计数器（key=symbol）
        self._sl_check_failures = {}
        self._sl_max_failures = 3  # 连续失败N次后强制平仓
        self._last_sl_update = {}  # {symbol: timestamp} SL更新节流

        # FR-06: per-symbol exit lock,串行化所有 close/reduce/partial_tp 路径,
        # 防止 partial_tp_1 / risk_alert reduce / local_stop close 并发下出双单。
        # 详见 docs/partial_tp_lifecycle_prd.md FR-04/FR-06。
        self._exit_lock_mu = threading.Lock()
        self._exit_locks: Dict[str, dict] = {}

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
            self.ledger = LiveLedger(self.exchange, logger=self.logger)
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
        """生成符合 OKX algoClOrdId 字母数字限制的 32 字符以内 client id。"""
        base = symbol.replace('-', '').replace('/', '').replace(':', '').upper()[:8]
        return f"sl{base}{uuid.uuid4().hex[:18]}"

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

    def _list_pending_algos(self, symbol: str) -> list:
        """列出 OKX 指定 symbol 的 pending algo orders。

        返回归一化的 dict 列表,每条含 algoId / algoClOrdId / ordType / sl_trigger /
        tp_trigger / side / posSide 等关键字段。仅 OKX 路径有效;其他交易所返回空。
        """
        if self.exchange_id != 'okx':
            return []
        rows: list = []
        try:
            fetcher = getattr(
                self.exchange, 'private_get_trade_orders_algo_pending', None,
            )
            if fetcher is not None:
                resp = fetcher({'ordType': 'conditional'})
                rows = resp.get('data') if isinstance(resp, dict) else []
                rows = rows or []
            else:
                orders = self.exchange.fetch_open_orders(
                    symbol, params={'ordType': 'conditional'},
                ) or []
                rows = [(o or {}).get('info') or {} for o in orders]
        except Exception as e:
            self.logger.warning(f"[Migrate] {symbol} 拉 pending algo 失败: {e}")
            return []

        inst_id = symbol.replace('/', '-').replace(':USDT', '')
        out: list = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_inst = row.get('instId') or row.get('inst_id')
            if row_inst and row_inst != inst_id:
                continue
            out.append({
                'algoId': row.get('algoId'),
                'algoClOrdId': row.get('algoClOrdId'),
                'ordType': row.get('ordType'),
                'side': row.get('side'),
                'posSide': row.get('posSide'),
                'sl_trigger': row.get('slTriggerPx'),
                'tp_trigger': row.get('tpTriggerPx'),
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
          1. 列 pending algo
          2. 任何 TP algo 一律撤掉(本系统 exit_owner=local_partial_tp_exchange_sl)
          3. SL algo 尝试归属本地 position;归属成功 → 写 sl_algo_id,
             protection_state=protected;无法归属(无对应仓位 / 多个 SL / 方向冲突)
             → halt symbol 并撤掉残留
          4. 本地有仓位但 pending 中无 SL → live halt,testnet 重挂

        返回 dict 摘要,包含 cancelled_tp / matched_sl / orphan_sl /
        missing_sl / halted。
        """
        summary = {
            'symbol': symbol,
            'cancelled_tp': 0,
            'matched_sl': None,
            'orphan_sl': 0,
            'missing_sl': False,
            'halted': False,
        }
        if self.exchange_id != 'okx':
            return summary

        algos = self._list_pending_algos(symbol)
        position = self.positions.get(symbol)

        sl_algos: list = []
        for algo in algos:
            algo_id = algo.get('algoId')
            if not algo_id:
                continue
            tp_trigger = algo.get('tp_trigger')
            sl_trigger = algo.get('sl_trigger')
            has_tp = tp_trigger not in (None, '', '0')
            has_sl = sl_trigger not in (None, '', '0')
            if has_tp and not has_sl:
                # 纯 TP algo,exit_owner 是本地 → 必须撤
                if self._cancel_algo_by_id(symbol, algo_id):
                    summary['cancelled_tp'] += 1
                    self.logger.info(
                        f"[Migrate] {symbol} 取消存量 TP algo {algo_id}"
                    )
                continue
            if has_sl:
                sl_algos.append(algo)

        if position is None:
            # 本地无仓位,所有残留 SL 全撤
            for algo in sl_algos:
                if self._cancel_algo_by_id(symbol, algo['algoId']):
                    summary['orphan_sl'] += 1
                    self.logger.info(
                        f"[Migrate] {symbol} 无本地仓位,撤残留 SL algo "
                        f"{algo['algoId']}"
                    )
            return summary

        # 本地有仓位:归属唯一 SL,多余 / 方向冲突一律 halt
        if not sl_algos:
            summary['missing_sl'] = True
            self.logger.error(
                f"[Migrate] {symbol} 本地有仓位但交易所无 SL algo,"
                f"protection_state→unknown"
            )
            position['sl_algo_id'] = None
            position['sl_algo_clord_id'] = None
            position['sl_sync_state'] = 'failed'
            position['protection_state'] = 'unknown'
            if not self.testnet:
                self._halt_symbol(symbol, reason='migrate_missing_sl')
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
        try:
            sl_trigger = float(algo.get('sl_trigger') or 0)
            if sl_trigger > 0:
                position['stop_loss'] = sl_trigger
        except (TypeError, ValueError):
            pass
        summary['matched_sl'] = algo['algoId']
        self._save_positions()
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
        candidates = set(self.positions.keys())
        try:
            fetcher = getattr(
                self.exchange, 'private_get_trade_orders_algo_pending', None,
            )
            if fetcher is not None:
                resp = fetcher({'ordType': 'conditional'})
                rows = resp.get('data') if isinstance(resp, dict) else []
                for row in (rows or []):
                    inst = (row or {}).get('instId')
                    if inst:
                        candidates.add(inst)
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

    def _fetch_okx_position_state(self, symbol: str) -> Optional[dict]:
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

    def is_symbol_halted(self, symbol: str) -> bool:
        return symbol in getattr(self, '_halted_symbols', {})

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
            sl_order_id = self._place_protective_sl(
                symbol=symbol, side=side, stop_price=stop_loss, amount=amount,
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
                'sl_algo_clord_id': None,
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

    def _replace_protective_sl(self, symbol: str, position: dict,
                                 new_sl: float) -> bool:
        """FR-04 单一入口: 撤旧 SL → 挂新 SL → 更新保护字段。

        所有 SL 价位变更(BE move / TP1 lock / ATR trail / add_position 重挂)
        必须经由此函数,使 position['sl_algo_id'] 始终对应交易所唯一保护单。

        失败时 protection_state 置 unknown,non-testnet OKX 触发 halt_symbol。
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

        self._cancel_protective_sl(symbol, position)

        new_clord = self._make_sl_clord_id(symbol) if self.exchange_id == 'okx' else None
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
            return True

        position['sl_order_id'] = None
        position['sl_algo_id'] = None
        position['sl_algo_clord_id'] = None
        position['sl_sync_state'] = 'failed'
        position['protection_state'] = 'unknown'
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

            # 取消独立 SL 条件单（_open_position 路径会设置；attachAlgoOrds 路径由 OKX 自动取消）
            if position.get('sl_order_id') or position.get('sl_algo_id'):
                self._cancel_protective_sl(symbol, position)

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
            }

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
        side = position['side']
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

    def get_position(self, symbol: str) -> Optional[Dict]:
        """获取持仓信息"""
        return self.positions.get(symbol)

    def get_all_positions(self) -> Dict:
        """获取所有持仓"""
        return self.positions.copy()

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

            # 预计算止盈止损价格（开仓时一并提交）
            if not stop_loss:
                stop_loss = self.risk_manager.calculate_stop_loss(current_price, side)
            tp_first = take_profit[0] if take_profit else self.risk_manager.calculate_take_profit(current_price, side)

            # SL方向校验：价格可能在Judge决策后变动，导致SL落在错误一侧
            if side == 'short' and stop_loss <= current_price:
                stop_loss = current_price * 1.015
                self.logger.warning(f"SL方向修正(short): SL={stop_loss:.4f} > entry={current_price:.4f}")
            elif side == 'long' and stop_loss >= current_price:
                stop_loss = current_price * 0.985
                self.logger.warning(f"SL方向修正(long): SL={stop_loss:.4f} < entry={current_price:.4f}")

            # TP方向校验
            if tp_first:
                if side == 'short' and tp_first >= current_price:
                    tp_first = current_price * 0.97
                    self.logger.warning(f"TP方向修正(short): TP={tp_first:.4f} < entry={current_price:.4f}")
                elif side == 'long' and tp_first <= current_price:
                    tp_first = current_price * 1.03
                    self.logger.warning(f"TP方向修正(long): TP={tp_first:.4f} > entry={current_price:.4f}")

            # 构建附带TP/SL的下单参数
            sl_clord_id = self._make_sl_clord_id(symbol) if self.exchange_id == 'okx' and stop_loss else None
            tp_sl_params = self._build_tp_sl_params(side, stop_loss, tp_first, sl_clord_id=sl_clord_id)

            if order_type == 'limit' and entry_zone:
                filled = self._execute_limit_order(symbol, side, size_usdt, current_price, entry_zone, leverage, tp_sl_params, clord_id)
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
                        filled = self._execute_limit_order(symbol, side, size_usdt, current_price, entry_zone, leverage, tp_sl_params, clord_id)
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
                    sl_algo_id_resolved = self._resolve_attached_sl_algo_id(
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
                'take_profit_levels': take_profit,
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
                'open_time': time.time(),
                'request_id': plan.get('request_id', ''),
            }
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

        sl_clord_id 透传到 attach 字段,成交后由 _resolve_attached_sl_algo_id()
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
                             clord_id: str = None) -> Optional[tuple]:
        """限价单执行，30秒超时，附带TP/SL"""
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

        # 限价单价格偏离实时价格超过2%时，基于实时价格重新计算
        if abs(limit_price - live_price) / live_price > 0.02:
            self.logger.warning(f"限价单价格{limit_price:.4f}偏离实时价{live_price:.4f}超2%，重新校准")
            limit_price = live_price * (0.999 if side == 'long' else 1.001)

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

        deadline = time.time() + 30
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

        ticker = self.exchange.fetch_ticker(symbol)
        new_price = ticker['last']
        price_change = abs(new_price - current_price) / current_price
        if price_change > 0.005:
            self.logger.info(f"价格变化>{price_change*100:.1f}%，放弃入场")
            return None

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

    def sync_positions(self) -> dict:
        """从交易所同步真实持仓，以交易所为准。返回新发现的持仓列表"""
        try:
            exchange_positions = self.exchange.fetch_positions()
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
            if not hasattr(self, '_last_removed_symbols'):
                self._last_removed_symbols = []
            self._last_removed_symbols.extend(removed_symbols)

            newly_synced = []
            cooldown = getattr(self, '_close_cooldown', {})
            now = time.time()
            for sym, ex_pos in active.items():
                # 刚平仓的symbol在冷却期内不重新补录（防止API延迟导致幽灵持仓）
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
            self.logger.error(f"仓位同步失败: {e}")
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
            reduce_amount = position['amount'] * pct

            # 精度处理：用交易所精度格式化
            try:
                reduce_amount = float(self.exchange.amount_to_precision(symbol, reduce_amount))
            except Exception:
                pass

            if reduce_amount <= 0:
                return None

            # OKX：减仓前以交易所真实仓位为准
            if self.exchange_id == 'okx':
                ex_pos = self._fetch_okx_position_state(symbol)
                if ex_pos is None:
                    self._mark_external_closed(symbol, reason='reduce_already_flat')
                    return None
                if ex_pos['side'] != position['side']:
                    self.logger.error(
                        f"[OKX reduce] {symbol} 方向冲突: 本地{position['side']} vs 交易所{ex_pos['side']}, 暂停"
                    )
                    self._halt_symbol(symbol, reason='direction_conflict_reduce')
                    return None
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
                        return None

            # 减仓前取消旧SL条件单（数量变化后旧单无效）
            if position.get('sl_order_id') or position.get('sl_algo_id'):
                self._cancel_protective_sl(symbol, position)
                position['sl_order_id'] = None
                position['sl_algo_id'] = None
                position['sl_algo_clord_id'] = None
                position['sl_sync_state'] = 'pending'

            order_side = 'sell' if position['side'] == 'long' else 'buy'
            reduce_params = self._build_close_order_params(position)
            order = self.exchange.create_order(
                symbol=symbol, type='market', side=order_side,
                amount=reduce_amount, params=reduce_params,
            )

            # 真实成交 PnL：通过 ledger 记录减仓
            reduce_usdt = position['amount_usdt'] * pct
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

            # 减仓 PnL 立即计入风控
            if realized_pnl != 0:
                self.risk_manager.record_trade(realized_pnl)

            position['amount'] -= reduce_amount
            position['amount_usdt'] *= (1 - pct)

            # 浮点精度兜底：剩余量极小时视为全平
            min_amount = self.exchange.market(symbol).get('limits', {}).get('amount', {}).get('min', 1e-8)
            position_dust_closed = False
            if position['amount'] < max(min_amount, 1e-8):
                del self.positions[symbol]
                self.logger.info(f"减仓后剩余量过小，视为全平: {symbol}")
                position_dust_closed = True

            # partial TP 真实减仓成功后才推进 tp_filled 并锁利 SL。
            # 全平 dust 情况下不再有剩余仓位需要保护,直接跳过。
            if tp_advance is not None and not position_dust_closed:
                entry = position['entry_price']
                original_sl = position.get('original_sl', position['stop_loss'])
                R = abs(entry - original_sl) / entry if entry else 0.0
                is_long = (position['side'] == 'long')
                if tp_advance == 1:
                    new_sl = entry * (1 + R * 0.5) if is_long else entry * (1 - R * 0.5)
                elif tp_advance == 2:
                    new_sl = entry * (1 + R * 1.5) if is_long else entry * (1 - R * 1.5)
                else:
                    new_sl = None
                position['tp_filled'] = tp_advance
                if new_sl is not None and R > 0:
                    self._move_sl(symbol, position, new_sl)
                    if position.get('protection_state') == 'protected':
                        self.logger.info(
                            f"[PartialTP] {symbol} TP{tp_advance} reduce 成功,"
                            f"tp_filled={tp_advance},SL→{new_sl:.4f}"
                        )
                    else:
                        # FR-05: reduce 成功但保护单更新失败,告警 + 阻断后续动作
                        self.logger.error(
                            f"[PartialTP] {symbol} TP{tp_advance} reduce 成功但 SL "
                            f"重挂失败,protection_state="
                            f"{position.get('protection_state')}; 阻断后续 add/open"
                        )
                        if self.testnet:
                            position['protection_state'] = 'local_fallback'
            elif not position_dust_closed:
                # FR-05: 普通减仓后也要确保剩余仓位有有效保护单
                remaining_sl = position.get('stop_loss')
                if remaining_sl:
                    self._replace_protective_sl(symbol, position, remaining_sl)
            self._save_positions()

            self.logger.info(f"减仓: {symbol} 减{pct*100:.0f}%, 剩余{position.get('amount', 0):.6f}, PnL={realized_pnl:+.4f}")
            return {'symbol': symbol, 'reduced_pct': pct, 'order': order, 'realized_pnl': realized_pnl, 'entry_request_id': position.get('request_id', '')}

        except Exception as e:
            error_msg = str(e)
            if self.exchange_id == 'okx' and _is_okx_position_reject(error_msg):
                review = self._handle_okx_close_reject(symbol, error_msg, action='reduce')
                self.logger.warning(f"[OKX reduce 拒单复核] {symbol} → {review['status']}")
                return None
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
                tp_dist_pct = abs(old_tp - old_entry) / old_entry
                if side == 'long':
                    position['take_profit'] = new_entry * (1 + tp_dist_pct)
                else:
                    position['take_profit'] = new_entry * (1 - tp_dist_pct)

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
